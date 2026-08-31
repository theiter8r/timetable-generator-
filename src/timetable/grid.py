"""The time grid: slot indexing, contiguity, and morning/afternoon derivation.

A *slot* is one (day, teaching-period) cell. Slots are numbered ``0..N-1`` and
that index is what the CP-SAT model reasons about. Everything here is pure
derivation from :class:`~timetable.models.Config` -- no solving.
"""

from __future__ import annotations

from dataclasses import dataclass

from .models import Config, Period, Session, SlotRef

MIDDAY_MINUTES = 12 * 60


def parse_time(value: str) -> int:
    """``"09:30"`` -> minutes since midnight. Returns -1 if unparseable."""
    try:
        hours, _, minutes = value.partition(":")
        return int(hours) * 60 + int(minutes)
    except (ValueError, AttributeError):
        return -1


def duration(period: Period) -> int:
    start, end = parse_time(period.start), parse_time(period.end)
    if start < 0 or end < 0:
        return 0
    return max(0, end - start)


def main_break_index(periods: list[Period]) -> int | None:
    """Index of the break that divides morning from afternoon.

    Colleges often have a short tea break *and* a long lunch break, and "after
    the break" always means the long one -- so we pick the longest break rather
    than the first. Ties go to whichever break sits closest to the middle of the
    teaching day. Returns None when there are no breaks at all.
    """
    breaks = [(i, p) for i, p in enumerate(periods) if p.kind == "break"]
    if not breaks:
        return None
    midpoint = len(periods) / 2
    return max(breaks, key=lambda ip: (duration(ip[1]), -abs(ip[0] - midpoint)))[0]


def derive_sessions(periods: list[Period]) -> dict[str, Session]:
    """Map each teaching period id to "morning" or "afternoon".

    An explicit ``Period.session`` always wins. Otherwise periods before the
    main break are morning and the rest are afternoon; with no break at all we
    fall back to splitting at midday.
    """
    split = main_break_index(periods)
    result: dict[str, Session] = {}
    for i, period in enumerate(periods):
        if period.kind != "teaching":
            continue
        if period.session is not None:
            result[period.id] = period.session
        elif split is not None:
            result[period.id] = "morning" if i < split else "afternoon"
        else:
            start = parse_time(period.start)
            result[period.id] = "morning" if 0 <= start < MIDDAY_MINUTES else "afternoon"
    return result


@dataclass(frozen=True)
class Slot:
    index: int
    day: str
    day_index: int
    period: str
    # Position in the full periods list, breaks included. Two teaching slots are
    # back-to-back only if these differ by exactly 1, which is what stops a lab
    # straddling the lunch break.
    period_index: int
    session: Session
    start: str
    end: str
    label: str

    @property
    def key(self) -> tuple[str, str]:
        return (self.day, self.period)

    def ref(self) -> SlotRef:
        return SlotRef(day=self.day, period=self.period)


class Grid:
    """Indexed view of the configured days x periods."""

    def __init__(self, config: Config) -> None:
        self.days = list(config.days)
        self.periods = list(config.periods)
        self.teaching_periods = [p for p in self.periods if p.kind == "teaching"]
        self.sessions = derive_sessions(self.periods)

        self.slots: list[Slot] = []
        for day_index, day in enumerate(self.days):
            for period_index, period in enumerate(self.periods):
                if period.kind != "teaching":
                    continue
                self.slots.append(
                    Slot(
                        index=len(self.slots),
                        day=day,
                        day_index=day_index,
                        period=period.id,
                        period_index=period_index,
                        session=self.sessions[period.id],
                        start=period.start,
                        end=period.end,
                        label=period.label or f"{period.start}-{period.end}",
                    )
                )

        self.by_key: dict[tuple[str, str], Slot] = {s.key: s for s in self.slots}
        self.by_day: dict[str, list[Slot]] = {d: [] for d in self.days}
        for slot in self.slots:
            self.by_day[slot.day].append(slot)

    def __len__(self) -> int:
        return len(self.slots)

    @property
    def slots_per_day(self) -> int:
        return len(self.teaching_periods)

    def get(self, day: str, period: str) -> Slot | None:
        return self.by_key.get((day, period))

    def run(self, start: Slot, length: int) -> list[Slot] | None:
        """The ``length`` back-to-back slots beginning at ``start``.

        Returns None unless every slot is a teaching period on the same day with
        nothing (no break, no day boundary) in between -- this is what makes a
        two-slot practical a genuine double period.
        """
        if length < 1:
            return None
        run = [start]
        day_slots = self.by_day[start.day]
        position = day_slots.index(start)
        for offset in range(1, length):
            if position + offset >= len(day_slots):
                return None
            nxt = day_slots[position + offset]
            if nxt.period_index != run[-1].period_index + 1:
                return None  # a break sits between them
            run.append(nxt)
        return run

    def runs(self, length: int) -> list[list[Slot]]:
        """Every legal placement of a ``length``-slot session in the week."""
        out = []
        for slot in self.slots:
            run = self.run(slot, length)
            if run is not None:
                out.append(run)
        return out

    def segments(self, day: str) -> list[list[Slot]]:
        """The day's teaching slots grouped into unbroken stretches.

        A break ends a stretch, so a day of 3 periods + lunch + 3 periods gives
        two segments of three.
        """
        out: list[list[Slot]] = []
        for slot in self.by_day.get(day, []):
            if out and slot.period_index == out[-1][-1].period_index + 1:
                out[-1].append(slot)
            else:
                out.append([slot])
        return out

    def max_disjoint_runs(self, length: int) -> int:
        """How many ``length``-slot sessions can run back-to-back-free in a week
        without overlapping each other.

        This is a much tighter bound than ``len(self.runs(length))``, which
        counts overlapping placements. With a lunch break splitting six periods
        into 3+3, only *one* double period fits either side -- two a day, not
        four -- and that ceiling is what actually decides whether a set of
        practicals can be scheduled.
        """
        if length < 1:
            return 0
        return sum(
            len(segment) // length
            for day in self.days
            for segment in self.segments(day)
        )
