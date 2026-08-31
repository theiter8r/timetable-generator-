"""Pre-flight feasibility checks.

A bare ``INFEASIBLE`` from the solver tells a timetable coordinator nothing, so
before we build the model we run cheap arithmetic that catches the great
majority of real misconfigurations and names the culprit:

    "SE-B is assigned 40 hrs of class but the week only has 36 teaching slots."

Anything reported at ``error`` level makes the config genuinely unsatisfiable;
``warning`` level is survivable but worth knowing about.
"""

from __future__ import annotations

from collections import defaultdict

from .grid import Grid
from .models import Config, Issue
from .resources import Resources, build_resources, is_synthetic


def _label(config: Config, teacher_id: str) -> str:
    teacher = config.teacher(teacher_id)
    return teacher.name or teacher_id if teacher else teacher_id


def _batch_label(batch_id: str) -> str:
    return batch_id.split("::")[0] if is_synthetic(batch_id) else batch_id


def check_references(config: Config, grid: Grid) -> list[Issue]:
    """Dangling ids -- almost always a typo in hand-edited config."""
    issues: list[Issue] = []
    division_ids = {d.id for d in config.divisions}
    batch_ids = {b.id for b in config.batches}
    subject_ids = {s.id for s in config.subjects}
    teacher_ids = {t.id for t in config.teachers}
    room_ids = {r.id for r in config.rooms}

    for batch in config.batches:
        if batch.division not in division_ids:
            issues.append(Issue(level="error", code="bad-ref", entity=batch.id,
                                message=f"Batch {batch.id} belongs to unknown division "
                                        f"{batch.division!r}."))

    for division in config.divisions:
        if division.home_room and division.home_room not in room_ids:
            issues.append(Issue(level="warning", code="bad-ref", entity=division.id,
                                message=f"Division {division.id} has unknown home room "
                                        f"{division.home_room!r}."))

    for a in config.assignments:
        if a.subject not in subject_ids:
            issues.append(Issue(level="error", code="bad-ref", entity=a.id,
                                message=f"Assignment {a.id} refers to unknown subject "
                                        f"{a.subject!r}."))
        known = division_ids if a.target.kind == "division" else batch_ids
        if a.target.id not in known:
            issues.append(Issue(level="error", code="bad-ref", entity=a.id,
                                message=f"Assignment {a.id} targets unknown "
                                        f"{a.target.kind} {a.target.id!r}."))
        for t in a.teachers:
            if t not in teacher_ids:
                issues.append(Issue(level="error", code="bad-ref", entity=a.id,
                                    message=f"Assignment {a.id} refers to unknown teacher "
                                            f"{t!r}."))
        for r in a.allowed_rooms:
            if r not in room_ids:
                issues.append(Issue(level="error", code="bad-ref", entity=a.id,
                                    message=f"Assignment {a.id} allows unknown room {r!r}."))
        if not a.teachers:
            issues.append(Issue(level="warning", code="unstaffed", entity=a.id,
                                message=f"Assignment {a.id} has no teacher assigned; it will "
                                        f"be scheduled but nobody is booked to take it."))
        if a.sessions_per_week < 1 or a.slots_per_session < 1:
            issues.append(Issue(level="error", code="bad-count", entity=a.id,
                                message=f"Assignment {a.id} must have at least one session of "
                                        f"at least one slot."))

    for event in config.pinned:
        if grid.get(event.day, event.period) is None:
            issues.append(Issue(level="warning", code="bad-ref", entity=event.id,
                                message=f"Pinned event {event.name!r} sits at "
                                        f"{event.day} {event.period}, which is not a teaching "
                                        f"slot; it will be ignored."))
    return issues


def check_grid(config: Config, grid: Grid) -> list[Issue]:
    issues: list[Issue] = []
    if not config.days:
        issues.append(Issue(level="error", code="empty-grid",
                            message="No days configured."))
    if not grid.teaching_periods:
        issues.append(Issue(level="error", code="empty-grid",
                            message="No teaching periods configured -- every period is a "
                                    "break."))
    return issues


def check_student_load(config: Config, grid: Grid, res: Resources) -> list[Issue]:
    """Can each batch's week physically hold everything asked of it?"""
    issues: list[Issue] = []
    demand: dict[str, int] = defaultdict(int)
    for a in config.assignments:
        for batch_id in res.batches_for(a.target):
            demand[batch_id] += a.weekly_slots

    total = len(grid)
    for batch_id, hours in sorted(demand.items()):
        available = total - len(res.batch_blocked.get(batch_id, set()))
        if hours > available:
            issues.append(Issue(
                level="error", code="student-overload", entity=batch_id,
                message=f"{_batch_label(batch_id)} is assigned {hours} slots of class but only "
                        f"{available} of the week's {total} teaching slots are free for it. "
                        f"Drop {hours - available} slot(s) or add periods to the grid.",
            ))
    return issues


def check_teacher_load(config: Config, grid: Grid, res: Resources) -> list[Issue]:
    issues: list[Issue] = []
    demand: dict[str, int] = defaultdict(int)
    for a in config.assignments:
        for teacher_id in a.teachers:
            demand[teacher_id] += a.weekly_slots

    for teacher in config.teachers:
        hours = demand.get(teacher.id, 0)
        if not hours:
            continue
        available = res.teacher_available_slots(teacher.id)
        if hours > available:
            blocked = len(res.teacher_blocked.get(teacher.id, set()))
            detail = f" ({blocked} slot(s) blocked by days off or pinned events)" if blocked else ""
            issues.append(Issue(
                level="error", code="teacher-overload", entity=teacher.id,
                message=f"{_label(config, teacher.id)} is assigned {hours} slots but has only "
                        f"{available} available{detail}.",
            ))
            continue
        if teacher.max_per_day is not None:
            free_days = {
                slot.day for slot in grid.slots
                if slot.index not in res.teacher_blocked.get(teacher.id, set())
            }
            ceiling = teacher.max_per_day * len(free_days)
            if hours > ceiling:
                issues.append(Issue(
                    level="error", code="teacher-daily-cap", entity=teacher.id,
                    message=f"{_label(config, teacher.id)} is assigned {hours} slots but a cap "
                            f"of {teacher.max_per_day}/day across {len(free_days)} available "
                            f"day(s) allows at most {ceiling}.",
                ))
    return issues


def check_rooms(config: Config, grid: Grid, res: Resources) -> list[Issue]:
    """Necessary condition: any set of rooms must be able to absorb everything
    that can only be held in that set."""
    issues: list[Issue] = []
    total = len(grid)

    eligible: dict[str, frozenset[str]] = {}
    for a in config.assignments:
        rooms = frozenset(res.eligible_rooms(a))
        eligible[a.id] = rooms
        if not rooms:
            subject = config.subject(a.subject)
            wanted = subject.room_type if subject else "?"
            issues.append(Issue(
                level="error", code="no-room", entity=a.id,
                message=f"Assignment {a.id} has no usable room: nothing of type {wanted!r} is "
                        f"big enough for {res.audience_strength(a.target)} students.",
            ))

    def capacity(room_ids: frozenset[str]) -> int:
        return sum(total - len(res.room_blocked.get(r, set())) for r in room_ids)

    # Check both the exact room sets assignments are restricted to, and the
    # broader per-type pools they sit inside.
    candidates: set[frozenset[str]] = {r for r in eligible.values() if r}
    by_type: dict[str, set[str]] = defaultdict(set)
    for room in config.rooms:
        by_type[room.type].add(room.id)
    candidates |= {frozenset(v) for v in by_type.values()}

    for pool in candidates:
        demand = sum(a.weekly_slots for a in config.assignments
                     if eligible.get(a.id) and eligible[a.id] <= pool)
        available = capacity(pool)
        if demand > available:
            names = ", ".join(sorted(pool)) if len(pool) <= 6 else f"{len(pool)} rooms"
            issues.append(Issue(
                level="error", code="room-overload", entity=names,
                message=f"{demand} slots must be held in [{names}] but those rooms only offer "
                        f"{available} slot(s) across the week. Add a room or reduce the load.",
            ))
    return issues


def check_placements(config: Config, res: Resources) -> list[Issue]:
    """Every assignment needs at least one legal spot to land in."""
    issues: list[Issue] = []
    for a in config.assignments:
        if not res.candidate_runs(a):
            who = ", ".join(_label(config, t) for t in a.teachers) or "nobody"
            reason = (f"no run of {a.slots_per_session} back-to-back slots exists"
                      if a.slots_per_session > 1
                      else "no slot is free")
            issues.append(Issue(
                level="error", code="no-placement", entity=a.id,
                message=f"Assignment {a.id} can never be placed: {reason} once the "
                        f"availability of {who} and pinned events are taken into account.",
            ))
    return issues


def check_spread(config: Config, grid: Grid) -> list[Issue]:
    issues: list[Issue] = []
    if not config.options.one_session_per_day:
        return issues
    for a in config.assignments:
        if a.sessions_per_week > len(config.days):
            issues.append(Issue(
                level="warning", code="spread-relaxed", entity=a.id,
                message=f"Assignment {a.id} needs {a.sessions_per_week} sessions but there are "
                        f"only {len(config.days)} days, so the one-per-day rule is relaxed for "
                        f"it.",
            ))
    return issues


def check_parallel_labs(config: Config, res: Resources) -> list[Issue]:
    """Parallel batch labs require every batch of a division to carry the same
    practical load -- otherwise some batch is always left over."""
    issues: list[Issue] = []
    if not config.options.parallel_batch_labs:
        return issues

    per_batch: dict[str, int] = defaultdict(int)
    for a in config.assignments:
        if a.target.kind == "batch":
            per_batch[a.target.id] += a.weekly_slots

    for division_id, batch_ids in res.division_batches.items():
        loads = {b: per_batch.get(b, 0) for b in batch_ids}
        if len(set(loads.values())) > 1:
            detail = ", ".join(f"{_batch_label(b)}={n}" for b, n in sorted(loads.items()))
            issues.append(Issue(
                level="error", code="uneven-batch-load", entity=division_id,
                message=f"'Parallel batch labs' is on, so every batch of {division_id} must "
                        f"have the same number of practical slots, but they differ ({detail}). "
                        f"Even the loads or turn the option off.",
            ))
    return issues


def check_lab_block_capacity(config: Config, grid: Grid, res: Resources) -> list[Issue]:
    """Can the week actually hold every parallel lab block?

    With parallel batch labs on, a division's practical occupies one room *per
    batch* simultaneously, so only ``rooms // batches`` divisions can be in
    practicals at any moment. Multiply that by the number of non-overlapping
    double periods the grid offers and you get a hard ceiling on lab blocks --
    a ceiling that is easy to breach and, without this check, surfaces only as
    a bare INFEASIBLE with no clue attached.
    """
    issues: list[Issue] = []
    if not config.options.parallel_batch_labs:
        return issues

    # Group practical demand by the room pool it must be held in.
    by_type: dict[str, set[str]] = defaultdict(set)
    for room in config.rooms:
        by_type[room.type].add(room.id)

    # Sibling batches share one block, so demand is counted per *batch* and then
    # reduced to a single representative batch per division -- counting all
    # three would triple the figure.
    per_batch: dict[str, dict[str, list[int]]] = defaultdict(lambda: defaultdict(list))
    for a in config.assignments:
        if a.target.kind != "batch":
            continue
        pool = frozenset(res.eligible_rooms(a))
        if not res.batch_division.get(a.target.id) or not pool:
            continue
        matching = [t for t, rooms in by_type.items() if pool <= rooms]
        if len(matching) != 1:
            continue  # spans several pools; the generic room check covers it
        per_batch[matching[0]][a.target.id] += [a.slots_per_session] * a.sessions_per_week

    demand: dict[str, dict[str, list[int]]] = defaultdict(dict)
    for room_type, batches in per_batch.items():
        for division_id, batch_ids in res.division_batches.items():
            representative = next((b for b in batch_ids if b in batches), None)
            if representative is not None:
                demand[room_type][division_id] = batches[representative]

    for room_type, per_division in demand.items():
        lengths = {n for blocks in per_division.values() for n in blocks}
        batch_counts = {
            len(res.division_batches.get(d, [])) for d in per_division
        }
        # The bound below assumes a uniform block length and batch count; with
        # a mixed setup we stay quiet rather than risk a bogus error.
        if len(lengths) != 1 or len(batch_counts) != 1:
            continue

        length = lengths.pop()
        batches = batch_counts.pop()
        if batches < 1:
            continue

        rooms = len(by_type[room_type])
        parallel = rooms // batches
        blocks_needed = sum(len(b) for b in per_division.values())
        positions = grid.max_disjoint_runs(length)
        capacity = positions * parallel

        if blocks_needed > capacity:
            if parallel == 0:
                detail = (f"a single division needs {batches} {room_type} rooms at once but "
                          f"only {rooms} exist")
            else:
                detail = (f"{rooms} {room_type} rooms let only {parallel} division(s) run "
                          f"practicals at a time, and the week has {positions} "
                          f"non-overlapping {length}-slot blocks ({positions} x {parallel} = "
                          f"{capacity})")
            issues.append(Issue(
                level="error", code="lab-block-overload", entity=room_type,
                message=f"{blocks_needed} parallel lab blocks are required but at most "
                        f"{capacity} fit: {detail}. Add {room_type} rooms, shorten the "
                        f"practicals, or turn off 'parallel batch labs'.",
            ))
    return issues


def validate(config: Config, grid: Grid | None = None,
             res: Resources | None = None) -> list[Issue]:
    """Run every pre-flight check. Errors first, then warnings."""
    grid = grid or Grid(config)
    issues = check_grid(config, grid)
    if any(i.level == "error" for i in issues):
        return issues  # nothing else is meaningful without a grid

    issues += check_references(config, grid)
    if any(i.code == "bad-ref" and i.level == "error" for i in issues):
        # Dangling ids make the arithmetic below meaningless; fix those first.
        return issues

    res = res or build_resources(config, grid)
    issues += check_student_load(config, grid, res)
    issues += check_teacher_load(config, grid, res)
    issues += check_rooms(config, grid, res)
    issues += check_placements(config, res)
    issues += check_parallel_labs(config, res)
    issues += check_lab_block_capacity(config, grid, res)
    issues += check_spread(config, grid)

    order = {"error": 0, "warning": 1}
    return sorted(issues, key=lambda i: (order[i.level], i.code, i.entity))
