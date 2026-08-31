"""Regenerate data/sample_config.json.

A realistic two-year engineering department: six divisions, three batches each,
five theory subjects and three practicals per year, staffed by a pool of
teachers with genuine morning/afternoon preferences and days off.

Run with:  uv run python scripts/make_sample.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from timetable.models import (  # noqa: E402
    Assignment,
    Batch,
    Config,
    Division,
    Period,
    PinnedEvent,
    Room,
    SlotRef,
    Subject,
    Target,
    Teacher,
)

DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]

PERIODS = [
    Period(id="p1", start="09:00", end="10:00"),
    Period(id="p2", start="10:00", end="11:00"),
    Period(id="p3", start="11:00", end="12:00"),
    Period(id="lunch", start="12:00", end="12:45", kind="break", label="Lunch"),
    Period(id="p4", start="12:45", end="13:45"),
    Period(id="p5", start="13:45", end="14:45"),
    Period(id="p6", start="14:45", end="15:45"),
]

# (id, name, short) for theory; practicals derive from the theory subject.
SE_THEORY = [
    ("SE-DSA", "Data Structures & Algorithms", "DSA"),
    ("SE-OOP", "Object Oriented Programming", "OOP"),
    ("SE-CG", "Computer Graphics", "CG"),
    ("SE-DELD", "Digital Electronics & Logic Design", "DELD"),
    ("SE-M3", "Engineering Mathematics III", "M3"),
]
SE_LABS = ["SE-DSA", "SE-OOP", "SE-CG"]

TE_THEORY = [
    ("TE-DBMS", "Database Management Systems", "DBMS"),
    ("TE-CN", "Computer Networks", "CN"),
    ("TE-TOC", "Theory of Computation", "TOC"),
    ("TE-SEPM", "Software Engineering & Project Mgmt", "SEPM"),
    ("TE-IS", "Information Systems & Security", "IS"),
]
TE_LABS = ["TE-DBMS", "TE-CN", "TE-SEPM"]

TEACHER_NAMES = [
    "S. Kale", "A. Deshmukh", "R. Iyer", "P. Nair", "M. Joshi", "V. Rane",
    "K. Bhosale", "N. Sathe", "D. Kulkarni", "T. Menon", "G. Pawar", "H. Shaikh",
    "J. Fernandes", "L. Chavan", "B. Gokhale", "C. Patil", "F. Ansari",
    "U. Wagh", "Y. Salvi", "Z. Merchant", "Q. Dsouza", "E. Bhat",
]

# Preferences spread across the pool so the objective has something to chew on:
# a third want mornings, a sixth want after the break, the rest are easy-going.
PREFS: dict[int, tuple[str, int]] = {
    0: ("morning", 5), 1: ("morning", 4), 2: ("afternoon", 5), 3: ("morning", 3),
    4: ("afternoon", 4), 5: ("morning", 5), 6: ("morning", 2), 8: ("afternoon", 3),
    9: ("morning", 4), 11: ("morning", 3), 13: ("afternoon", 5), 15: ("morning", 4),
    17: ("afternoon", 2), 19: ("morning", 5),
}

# A few genuine hard constraints: research days, visiting faculty, etc.
DAYS_OFF: dict[int, list[str]] = {2: ["Sat"], 7: ["Wed"], 12: ["Sat"], 18: ["Mon"]}


def build() -> Config:
    divisions: list[Division] = []
    batches: list[Batch] = []
    subjects: list[Subject] = []
    assignments: list[Assignment] = []

    # --- rooms ---------------------------------------------------------
    rooms = [Room(id=f"C-{201 + i}", name=f"Classroom {201 + i}", type="classroom", capacity=70)
             for i in range(6)]
    # Nine labs is not padding. Practicals run for all three batches of a
    # division at once, so each lab block consumes three rooms; nine lets three
    # divisions be in practicals simultaneously, which is what makes 18 lab
    # blocks fit into the 12 non-overlapping double periods the week offers.
    rooms += [Room(id=f"L-{101 + i}", name=f"Computer Lab {i + 1}", type="computer_lab",
                   capacity=30) for i in range(9)]

    # --- teachers ------------------------------------------------------
    teachers = []
    for i, name in enumerate(TEACHER_NAMES):
        pref, weight = PREFS.get(i, ("none", 3))
        teachers.append(
            Teacher(
                id=f"T{i + 1:02d}",
                name=f"Prof. {name}",
                short=name.split(". ")[-1][:6],
                session_preference=pref,
                preference_weight=weight,
                unavailable_days=DAYS_OFF.get(i, []),
                max_per_day=5,
            )
        )

    # --- subjects ------------------------------------------------------
    for theory, labs in ((SE_THEORY, SE_LABS), (TE_THEORY, TE_LABS)):
        for sid, name, short in theory:
            subjects.append(Subject(id=sid, name=name, short=short, kind="theory",
                                    room_type="classroom"))
        for sid in labs:
            base = next(t for t in theory if t[0] == sid)
            subjects.append(Subject(id=f"{sid}-LAB", name=f"{base[1]} Lab",
                                    short=f"{base[2]} Lab", kind="practical",
                                    room_type="computer_lab"))

    # --- divisions, batches, workload ----------------------------------
    # Round-robin the staff across the workload, keeping each teacher's weekly
    # load in the low teens so the sample solves comfortably.
    cursor = 0

    def next_teacher() -> str:
        nonlocal cursor
        tid = teachers[cursor % len(teachers)].id
        cursor += 1
        return tid

    room_cursor = 0
    for year, theory, labs in (("SE", SE_THEORY, SE_LABS), ("TE", TE_THEORY, TE_LABS)):
        for letter in ("A", "B", "C"):
            div_id = f"{year}-{letter}"
            home = rooms[room_cursor].id
            room_cursor += 1
            divisions.append(Division(id=div_id, name=div_id, year=year, strength=72,
                                      home_room=home))

            for n in (1, 2, 3):
                batches.append(Batch(id=f"{div_id}{n}", division=div_id,
                                     name=f"{div_id}{n}", strength=24))

            for sid, _, _ in theory:
                assignments.append(
                    Assignment(
                        id=f"{div_id}-{sid}",
                        subject=sid,
                        target=Target(kind="division", id=div_id),
                        teachers=[next_teacher()],
                        sessions_per_week=3,
                        slots_per_session=1,
                        # Theory happens in the division's home classroom, which
                        # also keeps the room half of the model small.
                        allowed_rooms=[home],
                    )
                )

            for sid in labs:
                for n in (1, 2, 3):
                    batch_id = f"{div_id}{n}"
                    assignments.append(
                        Assignment(
                            id=f"{batch_id}-{sid}-LAB",
                            subject=f"{sid}-LAB",
                            target=Target(kind="batch", id=batch_id),
                            teachers=[next_teacher()],
                            sessions_per_week=1,
                            slots_per_session=2,
                            allowed_rooms=[],  # any free computer lab
                        )
                    )

    # --- a pinned event ------------------------------------------------
    pinned = [
        PinnedEvent(
            id="pin-mentoring",
            name="Mentoring",
            day="Sat",
            period="p6",
            slots_per_session=1,
            targets=[Target(kind="division", id=d.id) for d in divisions],
            teachers=[],
            room=None,
        )
    ]

    return Config(
        days=DAYS,
        periods=PERIODS,
        divisions=divisions,
        batches=batches,
        subjects=subjects,
        rooms=rooms,
        teachers=teachers,
        assignments=assignments,
        pinned=pinned,
    )


def main() -> None:
    config = build()
    out = ROOT / "data" / "sample_config.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(config.model_dump(mode="json"), indent=2) + "\n")

    theory_hours = sum(a.weekly_slots for a in config.assignments
                       if a.target.kind == "division")
    lab_hours = sum(a.weekly_slots for a in config.assignments if a.target.kind == "batch")
    print(f"wrote {out.relative_to(ROOT)}")
    print(f"  {len(config.divisions)} divisions, {len(config.batches)} batches, "
          f"{len(config.teachers)} teachers, {len(config.assignments)} assignments")
    print(f"  {theory_hours} theory teacher-hours, {lab_hours} practical teacher-hours")


if __name__ == "__main__":
    main()
