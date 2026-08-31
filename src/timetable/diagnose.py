"""Turning "it didn't fit" into "here is why it didn't fit".

When the relaxed model drops sessions, the raw output is just a count. This
module looks at which resource was actually saturated around that assignment --
the students' week, the teacher's week, or the room pool -- and says so.
"""

from __future__ import annotations

from collections import defaultdict

from .grid import Grid
from .models import Config, Unplaced
from .resources import Resources, is_synthetic


def _name(config: Config, teacher_id: str) -> str:
    teacher = config.teacher(teacher_id)
    return (teacher.name or teacher_id) if teacher else teacher_id


def _pretty(batch_id: str) -> str:
    return batch_id.split("::")[0] if is_synthetic(batch_id) else batch_id


def _reason(config: Config, grid: Grid, res: Resources, assignment_id: str) -> str:
    """Best explanation we can give for one assignment coming up short."""
    a = next((x for x in config.assignments if x.id == assignment_id), None)
    if a is None:
        return "Assignment no longer exists."

    total = len(grid)
    culprits: list[str] = []

    # Is the audience's week simply full?
    for batch_id in res.batches_for(a.target):
        demand = sum(
            other.weekly_slots
            for other in config.assignments
            if batch_id in res.batches_for(other.target)
        )
        free = total - len(res.batch_blocked.get(batch_id, set()))
        if demand >= free:
            culprits.append(
                f"{_pretty(batch_id)}'s week is full ({demand} slots wanted, {free} available)"
            )
            break

    # Is a teacher stretched?
    for teacher_id in a.teachers:
        demand = sum(
            other.weekly_slots for other in config.assignments if teacher_id in other.teachers
        )
        free = res.teacher_available_slots(teacher_id)
        if demand >= free:
            culprits.append(
                f"{_name(config, teacher_id)} is fully booked ({demand} slots assigned, "
                f"{free} available)"
            )

    # Is the room pool the bottleneck?
    pool = frozenset(res.eligible_rooms(a))
    if pool:
        demand = sum(
            other.weekly_slots
            for other in config.assignments
            if frozenset(res.eligible_rooms(other)) <= pool
        )
        capacity = sum(total - len(res.room_blocked.get(r, set())) for r in pool)
        if demand > capacity * 0.9:
            names = ", ".join(sorted(pool)) if len(pool) <= 4 else f"{len(pool)} rooms"
            culprits.append(
                f"the room pool [{names}] is near capacity ({demand} slots wanted, "
                f"{capacity} available)"
            )

    if not culprits:
        if config.options.parallel_batch_labs and a.target.kind == "batch":
            return (
                "No clash-free slot was left. Practicals are being kept in lockstep across the "
                "division; turning off 'parallel batch labs' would give the solver more room."
            )
        if config.options.one_session_per_day:
            return (
                "No clash-free slot was left on a day this subject is not already scheduled. "
                "Relaxing 'one session per day' would give the solver more room."
            )
        return "No clash-free slot was left once every other class was placed."

    return "Blocked because " + "; and ".join(culprits) + "."


def explain_shortfalls(config: Config, grid: Grid, res: Resources,
                       shortfalls: dict[str, int]) -> list[Unplaced]:
    out: list[Unplaced] = []
    for assignment_id, missing in sorted(shortfalls.items()):
        a = next((x for x in config.assignments if x.id == assignment_id), None)
        requested = a.sessions_per_week if a else missing
        out.append(
            Unplaced(
                assignment=assignment_id,
                requested=requested,
                placed=requested - missing,
                reason=_reason(config, grid, res, assignment_id),
            )
        )
    return out


def utilisation(config: Config, grid: Grid, res: Resources) -> dict[str, object]:
    """Headline load figures, handy both for the UI and for spotting a config
    that is technically feasible but uncomfortably tight."""
    total = len(grid)
    teacher_demand: dict[str, int] = defaultdict(int)
    for a in config.assignments:
        for teacher_id in a.teachers:
            teacher_demand[teacher_id] += a.weekly_slots

    teachers = [
        {
            "id": t.id,
            "name": t.name or t.id,
            "assigned": teacher_demand.get(t.id, 0),
            "available": res.teacher_available_slots(t.id),
        }
        for t in config.teachers
    ]
    teachers.sort(key=lambda r: -r["assigned"])

    room_demand: dict[str, int] = defaultdict(int)
    for a in config.assignments:
        for room_id in res.eligible_rooms(a):
            room_demand[room_id] += a.weekly_slots / max(1, len(res.eligible_rooms(a)))

    return {
        "total_slots": total,
        "teachers": teachers,
        "rooms": [
            {
                "id": r.id,
                "name": r.name or r.id,
                "type": r.type,
                "expected": round(room_demand.get(r.id, 0), 1),
                "available": total - len(res.room_blocked.get(r.id, set())),
            }
            for r in config.rooms
        ],
    }
