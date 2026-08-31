"""The CP-SAT scheduling model.

The whole timetable is one constraint-satisfaction problem. Clash-freedom is
encoded as *hard* constraints, which is the point of using a solver rather than
a heuristic: the model cannot return a schedule in which a teacher is in two
rooms at once, because such an assignment does not satisfy the constraints. If
no clash-free timetable exists, the solver says so instead of quietly producing
a broken one.

Teacher preferences are *soft*: they become a weighted objective the solver
maximises after satisfying every hard constraint.
"""

from __future__ import annotations

import time
from collections import defaultdict
from dataclasses import dataclass, field

from ortools.sat.python import cp_model

from .grid import Grid, Slot
from .models import Config, ScheduledSession, Solution, Target
from .resources import Resources, build_resources

# Dwarfs every preference term, so the relaxed solve drops as few sessions as
# possible before it starts caring about anybody's morning preference.
SHORTFALL_PENALTY = 10_000


@dataclass
class BuiltModel:
    model: cp_model.CpModel
    grid: Grid
    res: Resources
    # (assignment id, run index) -> "this session starts here"
    x: dict[tuple[str, int], cp_model.IntVar] = field(default_factory=dict)
    # (assignment id, run index) -> the slots that placement occupies
    runs: dict[str, list[list[Slot]]] = field(default_factory=dict)
    # (assignment id, run index, room id) -> "and it uses this room"
    y: dict[tuple[str, int, str], cp_model.IntVar] = field(default_factory=dict)
    # (assignment id, run index) -> rooms that placement may actually use
    usable_rooms: dict[tuple[str, int], list[str]] = field(default_factory=dict)
    # assignment id -> sessions we failed to place (relaxed mode only)
    shortfall: dict[str, cp_model.IntVar] = field(default_factory=dict)


def build_model(config: Config, grid: Grid, res: Resources, *, relax: bool = False) -> BuiltModel:
    model = cp_model.CpModel()
    built = BuiltModel(model=model, grid=grid, res=res)
    n_slots = len(grid)

    # --- variables -----------------------------------------------------
    # Candidate runs are pre-pruned by resources.candidate_runs(): anything that
    # breaks contiguity, lands on a teacher's day off or collides with a pin is
    # never given a variable, so those rules hold by construction.
    eligible: dict[str, list[str]] = {a.id: res.eligible_rooms(a) for a in config.assignments}

    for a in config.assignments:
        runs = res.candidate_runs(a)
        built.runs[a.id] = runs
        for i, run in enumerate(runs):
            covered = {s.index for s in run}
            var = model.NewBoolVar(f"x[{a.id},{i}]")
            built.x[(a.id, i)] = var

            usable = [
                r for r in eligible[a.id]
                if not (res.room_blocked.get(r, set()) & covered)
            ]
            built.usable_rooms[(a.id, i)] = usable
            for room_id in usable:
                built.y[(a.id, i, room_id)] = model.NewBoolVar(f"y[{a.id},{i},{room_id}]")
            # (5) Room linkage: a scheduled session takes exactly one room.
            model.Add(sum(built.y[(a.id, i, r)] for r in usable) == var)

    # --- (1) every assignment gets the sessions it was given ------------
    for a in config.assignments:
        placed = sum(built.x[(a.id, i)] for i in range(len(built.runs[a.id])))
        if relax:
            short = model.NewIntVar(0, a.sessions_per_week, f"short[{a.id}]")
            built.shortfall[a.id] = short
            model.Add(placed + short == a.sessions_per_week)
        else:
            model.Add(placed == a.sessions_per_week)

    # --- occupancy tables ----------------------------------------------
    teacher_at: dict[tuple[str, int], list[cp_model.IntVar]] = defaultdict(list)
    batch_at: dict[tuple[str, int], list[cp_model.IntVar]] = defaultdict(list)
    room_at: dict[tuple[str, int], list[cp_model.IntVar]] = defaultdict(list)

    for a in config.assignments:
        batches = res.batches_for(a.target)
        for i, run in enumerate(built.runs[a.id]):
            var = built.x[(a.id, i)]
            for slot in run:
                for teacher_id in a.teachers:
                    teacher_at[(teacher_id, slot.index)].append(var)
                for batch_id in batches:
                    batch_at[(batch_id, slot.index)].append(var)
                for room_id in built.usable_rooms[(a.id, i)]:
                    room_at[(room_id, slot.index)].append(built.y[(a.id, i, room_id)])

    # --- (2) no teacher is in two places at once ------------------------
    # This is the headline guarantee. Because assignments span every year, it
    # covers the SE-B / SE-C overlap directly: both sessions would contribute to
    # the same (teacher, slot) sum, and the sum may not exceed one.
    for (_, _), vars_ in teacher_at.items():
        if len(vars_) > 1:
            model.AddAtMostOne(vars_)

    # --- (3) no group of students is in two places at once --------------
    # Stated per *batch*, which yields all three behaviours at once: a
    # division's lecture blocks every batch under it, two practicals for one
    # batch conflict, and sibling batches may still run practicals in parallel.
    for (_, _), vars_ in batch_at.items():
        if len(vars_) > 1:
            model.AddAtMostOne(vars_)

    # --- (4) no room hosts two sessions at once -------------------------
    for (_, _), vars_ in room_at.items():
        if len(vars_) > 1:
            model.AddAtMostOne(vars_)

    # --- (6) at most one session of a subject per day -------------------
    if config.options.one_session_per_day:
        for a in config.assignments:
            if a.sessions_per_week > len(config.days):
                continue  # impossible to honour; validate.py warns about it
            by_day: dict[str, list[cp_model.IntVar]] = defaultdict(list)
            for i, run in enumerate(built.runs[a.id]):
                by_day[run[0].day].append(built.x[(a.id, i)])
            for vars_ in by_day.values():
                if len(vars_) > 1:
                    model.AddAtMostOne(vars_)

    # --- (7) optional per-teacher daily cap -----------------------------
    for teacher in config.teachers:
        if teacher.max_per_day is None:
            continue
        by_day: dict[str, list[tuple[int, cp_model.IntVar]]] = defaultdict(list)
        for a in config.assignments:
            if teacher.id not in a.teachers:
                continue
            for i, run in enumerate(built.runs[a.id]):
                by_day[run[0].day].append((len(run), built.x[(a.id, i)]))
        for terms in by_day.values():
            if terms:
                model.Add(sum(n * v for n, v in terms) <= teacher.max_per_day)

    # --- busy indicators (needed by the parallel-lab rule and objective) --
    weights = config.weights
    needs_busy = (
        config.options.parallel_batch_labs
        or weights.even_spread > 0
        or weights.student_gap > 0
    )
    busy: dict[tuple[str, int], cp_model.IntVar] = {}
    if needs_busy:
        for batch_id in res.batch_division:
            for k in range(n_slots):
                occupants = batch_at.get((batch_id, k), [])
                var = model.NewBoolVar(f"busy[{batch_id},{k}]")
                if occupants:
                    model.Add(var == sum(occupants))
                else:
                    model.Add(var == 0)
                busy[(batch_id, k)] = var

    # --- (8) practicals run for all batches of a division together -------
    if config.options.parallel_batch_labs:
        practical_at: dict[tuple[str, int], list[cp_model.IntVar]] = defaultdict(list)
        for a in config.assignments:
            if a.target.kind != "batch":
                continue
            for i, run in enumerate(built.runs[a.id]):
                for slot in run:
                    practical_at[(a.target.id, slot.index)].append(built.x[(a.id, i)])

        for division_id, batch_ids in res.division_batches.items():
            if len(batch_ids) < 2:
                continue
            for k in range(n_slots):
                block = model.NewBoolVar(f"lab_block[{division_id},{k}]")
                for batch_id in batch_ids:
                    occupants = practical_at.get((batch_id, k), [])
                    if occupants:
                        model.Add(sum(occupants) == block)
                    else:
                        model.Add(block == 0)

    # --- objective ------------------------------------------------------
    terms: list[cp_model.LinearExpr] = []

    # Teacher session preference: morning, or after the break.
    if weights.session_preference > 0:
        for a in config.assignments:
            teachers = [config.teacher(t) for t in a.teachers]
            teachers = [t for t in teachers if t and t.session_preference != "none"]
            if not teachers:
                continue
            for i, run in enumerate(built.runs[a.id]):
                score = sum(
                    teacher.preference_weight
                    for teacher in teachers
                    for slot in run
                    if slot.session == teacher.session_preference
                )
                if score:
                    terms.append(weights.session_preference * score * built.x[(a.id, i)])

    # Even weekly spread: discourage piling a batch's week into a few days.
    if weights.even_spread > 0:
        demand: dict[str, int] = defaultdict(int)
        for a in config.assignments:
            for batch_id in res.batches_for(a.target):
                demand[batch_id] += a.weekly_slots
        n_days = max(1, len(config.days))
        for batch_id in res.batch_division:
            target = -(-demand[batch_id] // n_days)  # ceil: the balanced daily load
            for day in config.days:
                day_slots = grid.by_day.get(day, [])
                if not day_slots:
                    continue
                excess = model.NewIntVar(0, len(day_slots), f"excess[{batch_id},{day}]")
                load = sum(busy[(batch_id, s.index)] for s in day_slots)
                model.Add(excess >= load - target)
                terms.append(-weights.even_spread * excess)

    # Student gaps: a free period wedged between two busy ones is a hole in the
    # students' day. Not requested, hence the low default weight -- set
    # weights.student_gap to 0 to drop these variables entirely.
    if weights.student_gap > 0:
        for batch_id in res.batch_division:
            for day in config.days:
                day_slots = grid.by_day.get(day, [])
                for j in range(1, len(day_slots) - 1):
                    prev_, here, next_ = (day_slots[j - 1], day_slots[j], day_slots[j + 1])
                    hole = model.NewBoolVar(f"hole[{batch_id},{day},{j}]")
                    model.Add(
                        hole
                        >= busy[(batch_id, prev_.index)]
                        + busy[(batch_id, next_.index)]
                        - busy[(batch_id, here.index)]
                        - 1
                    )
                    terms.append(-weights.student_gap * hole)

    if relax:
        for short in built.shortfall.values():
            terms.append(-SHORTFALL_PENALTY * short)

    if terms:
        model.Maximize(sum(terms))

    return built


def _extract(config: Config, built: BuiltModel,
             solver: cp_model.CpSolver) -> list[ScheduledSession]:
    sessions: list[ScheduledSession] = []
    for a in config.assignments:
        for i, run in enumerate(built.runs[a.id]):
            if not solver.BooleanValue(built.x[(a.id, i)]):
                continue
            room = next(
                (r for r in built.usable_rooms[(a.id, i)]
                 if solver.BooleanValue(built.y[(a.id, i, r)])),
                None,
            )
            sessions.append(
                ScheduledSession(
                    assignment=a.id,
                    subject=a.subject,
                    target=Target(kind=a.target.kind, id=a.target.id),
                    teachers=list(a.teachers),
                    room=room,
                    day=run[0].day,
                    slots=[s.ref() for s in run],
                )
            )
    sessions.sort(key=lambda s: (s.day, s.slots[0].period, s.target.id))
    return sessions


def solve(config: Config, *, relax: bool = False, max_seconds: float | None = None,
          grid: Grid | None = None, res: Resources | None = None) -> Solution:
    """Build and solve the model, returning a :class:`Solution`.

    With ``relax=True`` the "every assignment gets all its sessions" constraint
    is softened by heavily-penalised slack, so instead of a bare INFEASIBLE we
    learn exactly which sessions could not be fitted.
    """
    grid = grid or Grid(config)
    res = res or build_resources(config, grid)
    built = build_model(config, grid, res, relax=relax)

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = max_seconds or config.options.max_seconds
    solver.parameters.num_workers = 8
    solver.parameters.random_seed = config.options.random_seed

    started = time.perf_counter()
    status = solver.Solve(built.model)
    elapsed = time.perf_counter() - started
    name = solver.StatusName(status)

    solution = Solution(status=name, solve_seconds=round(elapsed, 3))
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return solution

    solution.sessions = _extract(config, built, solver)
    solution.objective = solver.ObjectiveValue() if built.model.HasObjective() else None

    if relax:
        from .diagnose import explain_shortfalls

        shortfalls = {
            a_id: solver.Value(var)
            for a_id, var in built.shortfall.items()
            if solver.Value(var) > 0
        }
        if shortfalls:
            solution.unplaced = explain_shortfalls(config, grid, res, shortfalls)
            solution.status = "PARTIAL"
            solution.messages.append(
                f"{sum(shortfalls.values())} session(s) across {len(shortfalls)} assignment(s) "
                f"could not be placed. Everything shown below is still clash-free."
            )
    return solution


def solve_with_fallback(config: Config, max_seconds: float | None = None) -> Solution:
    """Solve normally; if that is infeasible, fall back to the relaxed model so
    the user gets a partial timetable plus an explanation rather than nothing."""
    solution = solve(config, max_seconds=max_seconds)
    if solution.status in ("OPTIMAL", "FEASIBLE"):
        return solution
    if solution.status == "INFEASIBLE":
        relaxed = solve(config, relax=True, max_seconds=max_seconds)
        if relaxed.sessions or relaxed.unplaced:
            relaxed.messages.insert(
                0,
                "No timetable satisfies every requirement, so this is the closest fit: as many "
                "sessions as possible placed, with the rest listed as unplaced.",
            )
            return relaxed
    return solution
