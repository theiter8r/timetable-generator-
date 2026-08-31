"""Small, fast configs for the tests.

Deliberately tiny: the point is to make each rule the *only* thing that can
decide the answer, so a passing test proves that rule holds rather than proving
the solver got lucky in a roomy schedule.
"""

from __future__ import annotations

from timetable.models import (
    Assignment,
    Batch,
    Config,
    Division,
    Period,
    Room,
    Subject,
    Target,
    Teacher,
)


def periods(n: int = 4, *, break_after: int | None = None) -> list[Period]:
    """``n`` one-hour teaching periods, optionally split by a break."""
    out: list[Period] = []
    hour = 9
    for i in range(n):
        out.append(Period(id=f"p{i + 1}", start=f"{hour:02d}:00", end=f"{hour + 1:02d}:00"))
        hour += 1
        if break_after is not None and i + 1 == break_after:
            out.append(Period(id="lunch", start=f"{hour:02d}:00", end=f"{hour:02d}:30",
                              kind="break", label="Lunch"))
            hour += 1
    return out


def base(days: list[str] | None = None, n_periods: int = 4,
         break_after: int | None = None) -> Config:
    return Config(
        days=days or ["Mon"],
        periods=periods(n_periods, break_after=break_after),
        options={"parallel_batch_labs": False, "one_session_per_day": False, "max_seconds": 10},
    )


def theory(subject_id: str = "S1") -> Subject:
    return Subject(id=subject_id, name=subject_id, short=subject_id, kind="theory",
                   room_type="classroom")


def practical(subject_id: str = "L1") -> Subject:
    return Subject(id=subject_id, name=subject_id, short=subject_id, kind="practical",
                   room_type="lab")


def classroom(room_id: str, capacity: int = 100) -> Room:
    return Room(id=room_id, name=room_id, type="classroom", capacity=capacity)


def lab(room_id: str, capacity: int = 100) -> Room:
    return Room(id=room_id, name=room_id, type="lab", capacity=capacity)


def teacher(teacher_id: str, **kwargs) -> Teacher:
    return Teacher(id=teacher_id, name=f"Prof. {teacher_id}", **kwargs)


def assign(assignment_id: str, subject: str, kind: str, target: str,
           teachers: list[str], sessions: int = 1, slots: int = 1,
           rooms: list[str] | None = None) -> Assignment:
    return Assignment(
        id=assignment_id,
        subject=subject,
        target=Target(kind=kind, id=target),
        teachers=teachers,
        sessions_per_week=sessions,
        slots_per_session=slots,
        allowed_rooms=rooms or [],
    )


def two_divisions_one_teacher() -> Config:
    """The exact situation the user described.

    One teacher owes a lecture to SE-B *and* a lecture to SE-C, and the week is
    only two slots long. There is no slack: the only clash-free answer puts the
    two lectures in different slots. A scheduler that ignored teacher clashes
    would happily stack both into slot 1.
    """
    config = base(days=["Mon"], n_periods=2)
    config.divisions = [Division(id="SE-B", strength=60), Division(id="SE-C", strength=60)]
    config.subjects = [theory("DBMS")]
    config.rooms = [classroom("C1"), classroom("C2")]
    config.teachers = [teacher("T1")]
    config.assignments = [
        assign("a-seb", "DBMS", "division", "SE-B", ["T1"]),
        assign("a-sec", "DBMS", "division", "SE-C", ["T1"]),
    ]
    return config


def division_with_batches() -> Config:
    """One division, three batches, a lecture and a practical each.

    Exercises the student rule from both sides: the lecture must block every
    batch, and the three practicals may overlap each other but not the lecture.
    """
    config = base(days=["Mon"], n_periods=4)
    config.divisions = [Division(id="SE-A", strength=60)]
    config.batches = [Batch(id=f"SE-A{i}", division="SE-A", strength=20) for i in (1, 2, 3)]
    config.subjects = [theory("MATH"), practical("PHYLAB")]
    config.rooms = [classroom("C1")] + [lab(f"L{i}", capacity=25) for i in (1, 2, 3)]
    config.teachers = [teacher(f"T{i}") for i in range(1, 5)]
    config.assignments = [
        assign("lec", "MATH", "division", "SE-A", ["T4"], sessions=1),
        *[assign(f"lab{i}", "PHYLAB", "batch", f"SE-A{i}", [f"T{i}"], sessions=1)
          for i in (1, 2, 3)],
    ]
    return config


def practical_needing_double_slot() -> Config:
    """A two-slot practical on a day split by a break after period 2.

    Only p1+p2 and p3+p4 are legal; p2+p3 straddles the break and must never be
    chosen.
    """
    config = base(days=["Mon"], n_periods=4, break_after=2)
    config.divisions = [Division(id="SE-A", strength=60)]
    config.batches = [Batch(id="SE-A1", division="SE-A", strength=20)]
    config.subjects = [practical("PHYLAB")]
    config.rooms = [lab("L1", capacity=25)]
    config.teachers = [teacher("T1")]
    config.assignments = [assign("lab1", "PHYLAB", "batch", "SE-A1", ["T1"], slots=2)]
    return config


def oversubscribed() -> Config:
    """More class than the week can physically hold: 5 sessions, 3 slots."""
    config = base(days=["Mon"], n_periods=3)
    config.divisions = [Division(id="SE-A", strength=60)]
    config.subjects = [theory("S1")]
    config.rooms = [classroom("C1")]
    config.teachers = [teacher("T1")]
    config.assignments = [assign("a1", "S1", "division", "SE-A", ["T1"], sessions=5)]
    return config
