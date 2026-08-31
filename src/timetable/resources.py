"""Shared derivations used by the validator, the solver and the diagnostics.

Chiefly: who is already blocked when (unavailability + pinned events), which
students a session actually consumes, and which rooms it may use. Keeping this
in one place means the validator's arithmetic and the solver's constraints can
never drift apart.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .grid import Grid, Slot
from .models import Assignment, Config, Target

# Divisions that define no batches still need a student resource to track, so we
# invent exactly one covering the whole division.
SYNTHETIC_BATCH_SUFFIX = "::all"


def synthetic_batch_id(division_id: str) -> str:
    return f"{division_id}{SYNTHETIC_BATCH_SUFFIX}"


def is_synthetic(batch_id: str) -> bool:
    return batch_id.endswith(SYNTHETIC_BATCH_SUFFIX)


@dataclass
class Resources:
    """Indexed, solver-ready view of a config against a grid."""

    config: Config
    grid: Grid

    # batch id -> division id, including synthetic batches
    batch_division: dict[str, str] = field(default_factory=dict)
    # division id -> its batch ids
    division_batches: dict[str, list[str]] = field(default_factory=dict)

    # Slots already consumed by unavailability or pinned events.
    teacher_blocked: dict[str, set[int]] = field(default_factory=dict)
    batch_blocked: dict[str, set[int]] = field(default_factory=dict)
    room_blocked: dict[str, set[int]] = field(default_factory=dict)

    def batches_for(self, target: Target) -> list[str]:
        """The student groups a session for ``target`` makes busy.

        A division's lecture occupies every one of its batches -- which is what
        stops a batch being pulled into a practical at the same time.
        """
        if target.kind == "batch":
            return [target.id] if target.id in self.batch_division else []
        return list(self.division_batches.get(target.id, []))

    def audience_strength(self, target: Target) -> int:
        if target.kind == "batch":
            batch = self.config.batch(target.id)
            return batch.strength if batch else 0
        division = self.config.division(target.id)
        return division.strength if division else 0

    def eligible_rooms(self, assignment: Assignment) -> list[str]:
        """Rooms this assignment may use, honouring type and capacity."""
        if assignment.allowed_rooms:
            return [r for r in assignment.allowed_rooms if self.config.room(r)]
        subject = self.config.subject(assignment.subject)
        room_type = subject.room_type if subject else "classroom"
        strength = self.audience_strength(assignment.target)
        return [
            room.id
            for room in self.config.rooms
            if room.type == room_type and room.capacity >= strength
        ]

    def teacher_free(self, teacher_id: str, slots: list[Slot]) -> bool:
        blocked = self.teacher_blocked.get(teacher_id, set())
        return all(slot.index not in blocked for slot in slots)

    def teacher_available_slots(self, teacher_id: str) -> int:
        return len(self.grid) - len(self.teacher_blocked.get(teacher_id, set()))

    def candidate_runs(self, assignment: Assignment) -> list[list[Slot]]:
        """Every legal placement of one session of ``assignment``.

        Pruned here rather than constrained in the model: runs that break
        contiguity, land on a teacher's day off, or clash with a pin never
        become variables at all. This is both the main performance win and the
        reason teacher unavailability is honoured by construction.
        """
        length = max(1, assignment.slots_per_session)
        batches = self.batches_for(assignment.target)
        out: list[list[Slot]] = []
        for run in self.grid.runs(length):
            if not all(self.teacher_free(t, run) for t in assignment.teachers):
                continue
            if any(
                slot.index in self.batch_blocked.get(batch, set())
                for batch in batches
                for slot in run
            ):
                continue
            out.append(run)
        return out


def build_resources(config: Config, grid: Grid) -> Resources:
    res = Resources(config=config, grid=grid)

    for division in config.divisions:
        own = config.batches_of(division.id)
        if own:
            ids = [b.id for b in own]
        else:
            ids = [synthetic_batch_id(division.id)]
        res.division_batches[division.id] = ids
        for batch_id in ids:
            res.batch_division[batch_id] = division.id

    res.teacher_blocked = {t.id: set() for t in config.teachers}
    res.batch_blocked = {b: set() for b in res.batch_division}
    res.room_blocked = {r.id: set() for r in config.rooms}

    # Teacher unavailability -> hard blocked slots.
    for teacher in config.teachers:
        blocked = res.teacher_blocked[teacher.id]
        for slot in grid.slots:
            if slot.day in teacher.unavailable_days:
                blocked.add(slot.index)
        for ref in teacher.unavailable_slots:
            slot = grid.get(ref.day, ref.period)
            if slot is not None:
                blocked.add(slot.index)

    # Pinned events consume teacher, student and room capacity up front.
    for event in config.pinned:
        start = grid.get(event.day, event.period)
        if start is None:
            continue
        run = grid.run(start, max(1, event.slots_per_session))
        if run is None:
            continue
        indices = {s.index for s in run}
        for teacher_id in event.teachers:
            if teacher_id in res.teacher_blocked:
                res.teacher_blocked[teacher_id] |= indices
        for target in event.targets:
            for batch_id in res.batches_for(target):
                res.batch_blocked[batch_id] |= indices
        if event.room and event.room in res.room_blocked:
            res.room_blocked[event.room] |= indices

    return res
