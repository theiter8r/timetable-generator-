"""Domain entities for the timetable generator.

Everything the user configures lives in :class:`Config`, which is what gets
serialised to ``data/config.json``. The solver never sees anything else.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Model(BaseModel):
    """Base for every entity.

    ``validate_assignment`` matters here: configs get built and tweaked in
    Python as well as loaded from JSON, and without it assigning a plain dict to
    a model field succeeds quietly and then fails much later inside the solver
    with a baffling AttributeError. This turns that into an immediate, clear
    error at the point of the mistake.
    """

    model_config = ConfigDict(validate_assignment=True)


PeriodKind = Literal["teaching", "break"]
Session = Literal["morning", "afternoon"]
SubjectKind = Literal["theory", "practical"]
TargetKind = Literal["division", "batch"]
SessionPreference = Literal["morning", "afternoon", "none"]


class Period(Model):
    """One row of the daily time grid, shared by every day of the week."""

    id: str
    start: str  # "09:00"
    end: str  # "10:00"
    kind: PeriodKind = "teaching"
    label: str = ""
    # Which half of the day this period belongs to. Left as None it is derived
    # from the period's position relative to the main break (see grid.py); set
    # it explicitly to override that.
    session: Session | None = None


class SlotRef(Model):
    """A concrete (day, period) cell, used for pins and unavailability."""

    day: str
    period: str

    def key(self) -> tuple[str, str]:
        return (self.day, self.period)


class Division(Model):
    """A class of students taught theory together, e.g. SE-B."""

    id: str
    name: str = ""
    year: str = ""
    strength: int = 70
    home_room: str | None = None


class Batch(Model):
    """A sub-group of a division that does practicals separately, e.g. SE-B1."""

    id: str
    division: str
    name: str = ""
    strength: int = 25


class Subject(Model):
    id: str
    name: str = ""
    kind: SubjectKind = "theory"
    # Rooms of this type are eligible. Free-form so institutions can invent
    # their own ("drawing_hall", "workshop"); it just has to match Room.type.
    room_type: str = "classroom"
    short: str = ""


class Room(Model):
    id: str
    name: str = ""
    type: str = "classroom"
    capacity: int = 70


class Teacher(Model):
    id: str
    name: str = ""
    short: str = ""
    # Hard: the solver will never place this teacher here.
    unavailable_days: list[str] = Field(default_factory=list)
    unavailable_slots: list[SlotRef] = Field(default_factory=list)
    # Soft: rewarded in the objective, scaled by preference_weight.
    session_preference: SessionPreference = "none"
    preference_weight: int = 3
    # Optional hard cap purely to stop a degenerate schedule stacking a whole
    # week onto one day. None means unlimited; it is not an objective term.
    max_per_day: int | None = None


class Target(Model):
    """Who is taught: a whole division, or one of its batches."""

    kind: TargetKind
    id: str


class Assignment(Model):
    """One line of the workload table: who teaches what, to whom, how often.

    The admin allocates teachers here; the solver only decides *when* and
    *where* each session happens.
    """

    id: str
    subject: str
    target: Target
    teachers: list[str] = Field(default_factory=list)
    sessions_per_week: int = 1
    slots_per_session: int = 1
    # Empty means "any room whose type matches the subject and that is big
    # enough for the audience".
    allowed_rooms: list[str] = Field(default_factory=list)

    @property
    def weekly_slots(self) -> int:
        return self.sessions_per_week * self.slots_per_session


class PinnedEvent(Model):
    """A fixed, immovable block: mentoring, sports, a guest lecture.

    Pins consume teacher, student and room capacity before the solver runs, so
    nothing else can be scheduled on top of them.
    """

    id: str
    name: str
    day: str
    period: str  # starting period
    slots_per_session: int = 1
    targets: list[Target] = Field(default_factory=list)
    teachers: list[str] = Field(default_factory=list)
    room: str | None = None


class Weights(Model):
    """Objective weights, exposed so they are tunable without touching code."""

    session_preference: int = 10
    even_spread: int = 4
    student_gap: int = 2


class SolverOptions(Model):
    max_seconds: float = 30.0
    # Stop the same subject appearing twice in one day for a division. Relaxed
    # automatically when a subject genuinely needs more sessions than there are
    # days to put them in.
    one_session_per_day: bool = True
    # Every batch of a division does its practical at the same time, or none of
    # them do -- so a lab block occupies the whole division and no batch is left
    # sitting idle. Requires all batches of a division to have equal practical
    # load; validate.py checks that and reports it if not.
    parallel_batch_labs: bool = True
    random_seed: int = 0


class Config(Model):
    days: list[str] = Field(default_factory=lambda: ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat"])
    periods: list[Period] = Field(default_factory=list)
    divisions: list[Division] = Field(default_factory=list)
    batches: list[Batch] = Field(default_factory=list)
    subjects: list[Subject] = Field(default_factory=list)
    rooms: list[Room] = Field(default_factory=list)
    teachers: list[Teacher] = Field(default_factory=list)
    assignments: list[Assignment] = Field(default_factory=list)
    pinned: list[PinnedEvent] = Field(default_factory=list)
    weights: Weights = Field(default_factory=Weights)
    options: SolverOptions = Field(default_factory=SolverOptions)

    # --- lookups -------------------------------------------------------

    @model_validator(mode="after")
    def _check_unique_ids(self) -> Config:
        for label, items in (
            ("period", self.periods),
            ("division", self.divisions),
            ("batch", self.batches),
            ("subject", self.subjects),
            ("room", self.rooms),
            ("teacher", self.teachers),
            ("assignment", self.assignments),
            ("pinned event", self.pinned),
        ):
            seen: set[str] = set()
            for item in items:
                if item.id in seen:
                    raise ValueError(f"duplicate {label} id: {item.id!r}")
                seen.add(item.id)
        return self

    def division(self, div_id: str) -> Division | None:
        return next((d for d in self.divisions if d.id == div_id), None)

    def batch(self, batch_id: str) -> Batch | None:
        return next((b for b in self.batches if b.id == batch_id), None)

    def subject(self, subject_id: str) -> Subject | None:
        return next((s for s in self.subjects if s.id == subject_id), None)

    def room(self, room_id: str) -> Room | None:
        return next((r for r in self.rooms if r.id == room_id), None)

    def teacher(self, teacher_id: str) -> Teacher | None:
        return next((t for t in self.teachers if t.id == teacher_id), None)

    def batches_of(self, div_id: str) -> list[Batch]:
        return [b for b in self.batches if b.division == div_id]


# --- validation ---------------------------------------------------------


class Issue(Model):
    """A pre-flight finding. ``error`` means the config cannot be satisfied."""

    level: Literal["error", "warning"]
    code: str
    message: str
    entity: str = ""


# --- solver output ------------------------------------------------------


class ScheduledSession(Model):
    """One placed session. ``slots`` lists every (day, period) it occupies."""

    assignment: str
    subject: str
    target: Target
    teachers: list[str]
    room: str | None
    day: str
    slots: list[SlotRef]

    @property
    def start_period(self) -> str:
        return self.slots[0].period


class Unplaced(Model):
    """A session the relaxed solve could not fit, with a human explanation."""

    assignment: str
    requested: int
    placed: int
    reason: str


class Solution(Model):
    status: str  # "OPTIMAL" | "FEASIBLE" | "INFEASIBLE" | "UNKNOWN"
    sessions: list[ScheduledSession] = Field(default_factory=list)
    unplaced: list[Unplaced] = Field(default_factory=list)
    objective: float | None = None
    solve_seconds: float = 0.0
    # Populated when we could not honour everything; human-readable.
    messages: list[str] = Field(default_factory=list)
