"""The guided first-run setup: what each step needs, and how far you have got.

A head of department filling this in for the first time is not editing a
configuration file -- they are answering questions about their department, in an
order where each answer is possible to give. Rooms have to exist before a
subject can point at a room type; teachers have to exist before the workload
table can allocate them. :data:`STEPS` is that order.

:func:`step_report` grades the config against every step, so the wizard can show
what is still missing *per step* rather than dumping the whole pre-flight report
on someone who has only filled in two of eight tables. Nothing here solves or
schedules; it is all cheap arithmetic over the config and safe to run on every
keystroke-triggered save.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import Field

from .grid import Grid, parse_time
from .models import Config, Model
from .resources import build_resources
from .validate import validate

TIME_RE = re.compile(r"^\d{1,2}:[0-5]\d$")


class Step(Model):
    id: str
    title: str
    # One line under the step title in the wizard: what this step is *for*.
    lede: str
    # Optional steps have sensible defaults and never block the Next button.
    optional: bool = False


STEPS: list[Step] = [
    Step(id="welcome", title="Welcome",
         lede="Name the department and choose what to start from."),
    Step(id="grid", title="Time grid",
         lede="The working days, and the periods in a day."),
    Step(id="classes", title="Divisions & batches",
         lede="The groups you teach: a division for lectures, its batches for practicals."),
    Step(id="rooms", title="Rooms",
         lede="Every classroom and lab available to the department."),
    Step(id="subjects", title="Subjects",
         lede="What is taught, and the kind of room each subject needs."),
    Step(id="teachers", title="Teachers",
         lede="The staff list, with days off and half-day preferences."),
    Step(id="workload", title="Workload",
         lede="Who teaches what, to whom, and how often each week."),
    Step(id="pinned", title="Pinned events", optional=True,
         lede="Fixed blocks the timetable must work around."),
    Step(id="options", title="Rules", optional=True,
         lede="The hard rules and the preference weights."),
    Step(id="review", title="Review", optional=True,
         lede="A last look at everything before the first timetable."),
]

STEP_IDS = [s.id for s in STEPS]
FIRST_STEP = STEP_IDS[0]


class OnboardingState(Model):
    """Where the HOD is up to. Saved beside the config, on every step."""

    # The step being edited right now; the wizard reopens here.
    step: str = FIRST_STEP
    # Steps the user has moved past, in any order. Not the same as "valid" --
    # step_report() recomputes validity from the config every time.
    visited: list[str] = Field(default_factory=list)
    # Set once the wizard has been finished (or skipped) -- until then the app
    # opens straight into setup.
    finished: bool = False
    started_from: Literal["blank", "sample", ""] = ""
    updated_at: str = ""

    def touch(self) -> OnboardingState:
        self.updated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        return self


# --- per-step grading ---------------------------------------------------


class _Report:
    """Collects the findings for one step."""

    def __init__(self) -> None:
        self.errors: list[str] = []
        self.warnings: list[str] = []

    def error(self, message: str) -> None:
        self.errors.append(message)

    def warn(self, message: str) -> None:
        self.warnings.append(message)


def _blank_ids(items: list[Any], label: str, report: _Report) -> None:
    if any(not (item.id or "").strip() for item in items):
        report.error(f"Every {label} needs an ID -- one row is still blank.")


def _check_grid(config: Config, report: _Report) -> None:
    if not config.days:
        report.error("Pick at least one working day.")
    teaching = [p for p in config.periods if p.kind == "teaching"]
    if not config.periods:
        report.error("Add the periods of a normal day.")
    elif not teaching:
        report.error("Every period is marked as a break -- at least one must be teaching.")
    _blank_ids(config.periods, "period", report)

    for period in config.periods:
        for field in ("start", "end"):
            value = getattr(period, field)
            if not TIME_RE.match(value or ""):
                report.error(f"Period {period.id or '(unnamed)'} has no valid "
                             f"{field} time -- use 24-hour HH:MM, e.g. 09:00.")
                break
        else:
            if parse_time(period.end) <= parse_time(period.start):
                report.error(f"Period {period.id} ends at or before it starts.")

    if config.periods and not any(p.kind == "break" for p in config.periods):
        report.warn("No break in the day. Breaks split morning from afternoon and stop a "
                    "double practical running across lunch -- most departments want one.")


def _check_classes(config: Config, report: _Report) -> None:
    if not config.divisions:
        report.error("Add at least one division -- the group taught theory together.")
    _blank_ids(config.divisions, "division", report)
    _blank_ids(config.batches, "batch", report)

    known = {d.id for d in config.divisions}
    for batch in config.batches:
        if batch.division not in known:
            report.error(f"Batch {batch.id} is not attached to a division.")
    for division in config.divisions:
        if division.strength <= 0:
            report.warn(f"Division {division.id} has no student strength set; room "
                        f"capacity cannot be checked for it.")
        if not config.batches_of(division.id):
            report.warn(f"Division {division.id} has no batches. That is fine for a purely "
                        f"theory year -- add batches if it has practicals.")


def _check_rooms(config: Config, report: _Report) -> None:
    if not config.rooms:
        report.error("Add at least one room.")
    _blank_ids(config.rooms, "room", report)
    for room in config.rooms:
        if not (room.type or "").strip():
            report.error(f"Room {room.id} has no type. The type is what a subject matches "
                         f"against, e.g. classroom or computer_lab.")
        if room.capacity <= 0:
            report.warn(f"Room {room.id} has no capacity set; it will be treated as too "
                        f"small for every class.")


def _check_subjects(config: Config, report: _Report) -> None:
    if not config.subjects:
        report.error("Add at least one subject.")
    _blank_ids(config.subjects, "subject", report)

    room_types = {r.type for r in config.rooms}
    for subject in config.subjects:
        if not (subject.name or subject.short):
            report.warn(f"Subject {subject.id} has no name; timetables will show its ID.")
        if room_types and subject.room_type not in room_types:
            report.error(f"Subject {subject.id} needs a {subject.room_type!r} room and no "
                         f"room of that type exists. Add one, or point the subject at a "
                         f"type you have: {', '.join(sorted(room_types))}.")
    if any(s.kind == "practical" for s in config.subjects) and not config.batches:
        report.warn("There are practical subjects but no batches, so practicals will be "
                    "taught to whole divisions at once.")


def _check_teachers(config: Config, report: _Report) -> None:
    if not config.teachers:
        report.error("Add the teaching staff.")
    _blank_ids(config.teachers, "teacher", report)
    for teacher in config.teachers:
        if not (teacher.name or "").strip():
            report.warn(f"Teacher {teacher.id} has no name; timetables will show the ID.")
        if config.days and set(teacher.unavailable_days) >= set(config.days):
            report.error(f"{teacher.name or teacher.id} is marked unavailable on every "
                         f"working day and cannot be given any class.")


def _check_workload(config: Config, grid: Grid, report: _Report) -> None:
    if not config.assignments:
        report.error("Add the teaching commitments -- one row per subject taught to a group.")
    _blank_ids(config.assignments, "workload row", report)

    subjects = {s.id for s in config.subjects}
    divisions = {d.id for d in config.divisions}
    batches = {b.id for b in config.batches}
    teachers = {t.id for t in config.teachers}
    rooms = {r.id for r in config.rooms}

    for a in config.assignments:
        where = a.id or "(unnamed row)"
        if a.subject not in subjects:
            report.error(f"{where}: pick a subject.")
        known = divisions if a.target.kind == "division" else batches
        if a.target.id not in known:
            report.error(f"{where}: pick the {a.target.kind} being taught.")
        for teacher_id in a.teachers:
            if teacher_id not in teachers:
                report.error(f"{where}: teacher {teacher_id!r} no longer exists.")
        for room_id in a.allowed_rooms:
            if room_id not in rooms:
                report.error(f"{where}: room {room_id!r} no longer exists.")
        if not a.teachers:
            report.warn(f"{where} has nobody teaching it. It will still be scheduled, but no "
                        f"teacher is booked and it will not appear on any teacher's timetable.")
        if a.sessions_per_week < 1 or a.slots_per_session < 1:
            report.error(f"{where}: sessions per week and slots each must both be at least 1.")

    covered = {(a.subject, a.target.kind, a.target.id) for a in config.assignments}
    for subject in config.subjects:
        if not any(key[0] == subject.id for key in covered):
            report.warn(f"{subject.short or subject.name or subject.id} is not taught to "
                        f"anyone yet.")

    if config.assignments and len(grid):
        for division in config.divisions:
            demand = sum(
                a.weekly_slots for a in config.assignments
                if (a.target.kind == "division" and a.target.id == division.id)
            )
            if demand > len(grid):
                report.error(f"{division.id} is given {demand} slots of lecture but the week "
                             f"only has {len(grid)}.")


def _counts(config: Config, grid: Grid) -> dict[str, str]:
    teaching = [p for p in config.periods if p.kind == "teaching"]
    breaks = len(config.periods) - len(teaching)
    hours = sum(a.weekly_slots for a in config.assignments)
    return {
        "welcome": "",
        "grid": f"{len(config.days)} days · {len(teaching)} periods"
                + (f" · {breaks} break{'s' if breaks != 1 else ''}" if breaks else "")
                + f" · {len(grid)} slots a week",
        "classes": f"{len(config.divisions)} divisions · {len(config.batches)} batches",
        "rooms": f"{len(config.rooms)} rooms",
        "subjects": f"{len(config.subjects)} subjects",
        "teachers": f"{len(config.teachers)} teachers",
        "workload": f"{len(config.assignments)} commitments · {hours} slots a week",
        "pinned": f"{len(config.pinned)} pinned events",
        "options": "",
        "review": "",
    }


def step_report(config: Config, grid: Grid | None = None) -> list[dict[str, Any]]:
    """Grade every step against the config as it currently stands."""
    grid = grid or Grid(config)
    counts = _counts(config, grid)
    reports: dict[str, _Report] = {step.id: _Report() for step in STEPS}

    _check_grid(config, reports["grid"])
    _check_classes(config, reports["classes"])
    _check_rooms(config, reports["rooms"])
    _check_subjects(config, reports["subjects"])
    _check_teachers(config, reports["teachers"])
    _check_workload(config, grid, reports["workload"])

    # Review is the only step that pays for the full pre-flight: by then every
    # table has content, so its cross-cutting arithmetic can actually run.
    review = reports["review"]
    if not any(reports[s].errors for s in ("grid", "classes", "rooms", "subjects",
                                           "teachers", "workload")):
        try:
            res = build_resources(config, grid)
            for issue in validate(config, grid, res):
                (review.error if issue.level == "error" else review.warn)(issue.message)
        except Exception as exc:  # noqa: BLE001 -- never fail an autosave
            review.warn(f"The full feasibility check could not run: {exc}")
    else:
        review.warn("Some earlier steps are incomplete; the full feasibility check runs "
                    "once they are filled in.")

    if not (config.department or config.institution):
        reports["welcome"].warn("No department name yet. It heads the printable timetables, "
                                "so a sheet handed to a student says where it came from.")

    required_ids = [s.id for s in STEPS if not s.optional]
    incomplete = any(reports[s].errors for s in required_ids)

    out = []
    for step in STEPS:
        report = reports[step.id]
        has_content = bool(counts[step.id]) or step.id in ("welcome", "options")
        if step.id == "review":
            has_content = not incomplete
        out.append({
            "id": step.id,
            "title": step.title,
            "lede": step.lede,
            "optional": step.optional,
            "summary": counts[step.id],
            "errors": report.errors,
            "warnings": report.warnings,
            "done": not report.errors and has_content,
        })
    return out


def blank_config() -> Config:
    """An empty department: a working week, and nothing filled in yet.

    Deliberately not *entirely* empty -- a Monday-to-Saturday week and a
    seven-period day are what nearly every college starts from, and starting the
    wizard with a grid on screen is far less daunting than starting with a blank
    table and no idea what a period row looks like.
    """
    periods = [
        {"id": "P1", "start": "09:00", "end": "10:00"},
        {"id": "P2", "start": "10:00", "end": "11:00"},
        {"id": "TEA", "start": "11:00", "end": "11:15", "kind": "break", "label": "Tea"},
        {"id": "P3", "start": "11:15", "end": "12:15"},
        {"id": "P4", "start": "12:15", "end": "13:15"},
        {"id": "LUNCH", "start": "13:15", "end": "14:00", "kind": "break", "label": "Lunch"},
        {"id": "P5", "start": "14:00", "end": "15:00"},
        {"id": "P6", "start": "15:00", "end": "16:00"},
    ]
    return Config.model_validate({
        "days": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat"],
        "periods": periods,
    })
