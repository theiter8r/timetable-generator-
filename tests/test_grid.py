"""The time grid: how days split into slots, halves, and legal double periods."""

from __future__ import annotations

from timetable.grid import Grid, derive_sessions, main_break_index
from timetable.models import Period

import fixtures


def test_slots_count_only_teaching_periods():
    grid = Grid(fixtures.base(days=["Mon", "Tue"], n_periods=4, break_after=2))
    assert len(grid) == 8
    assert grid.slots_per_day == 4
    assert grid.get("Mon", "lunch") is None


def test_morning_and_afternoon_split_at_the_break():
    grid = Grid(fixtures.base(n_periods=4, break_after=2))
    assert [grid.sessions[p] for p in ("p1", "p2")] == ["morning", "morning"]
    assert [grid.sessions[p] for p in ("p3", "p4")] == ["afternoon", "afternoon"]


def test_longest_break_wins_over_the_first_one():
    """A short tea break shouldn't be mistaken for lunch: 'after the break'
    means after the long one."""
    periods = [
        Period(id="p1", start="09:00", end="10:00"),
        Period(id="tea", start="10:00", end="10:15", kind="break"),
        Period(id="p2", start="10:15", end="11:15"),
        Period(id="p3", start="11:15", end="12:15"),
        Period(id="lunch", start="12:15", end="13:00", kind="break"),
        Period(id="p4", start="13:00", end="14:00"),
    ]
    assert periods[main_break_index(periods)].id == "lunch"
    sessions = derive_sessions(periods)
    assert sessions == {"p1": "morning", "p2": "morning", "p3": "morning", "p4": "afternoon"}


def test_explicit_session_overrides_the_derivation():
    periods = [
        Period(id="p1", start="09:00", end="10:00", session="afternoon"),
        Period(id="lunch", start="10:00", end="10:45", kind="break"),
        Period(id="p2", start="10:45", end="11:45"),
    ]
    assert derive_sessions(periods)["p1"] == "afternoon"


def test_no_break_falls_back_to_midday():
    periods = [
        Period(id="p1", start="09:00", end="10:00"),
        Period(id="p2", start="13:00", end="14:00"),
    ]
    assert derive_sessions(periods) == {"p1": "morning", "p2": "afternoon"}


def test_run_refuses_to_cross_a_break():
    grid = Grid(fixtures.base(n_periods=4, break_after=2))
    assert [s.period for s in grid.run(grid.get("Mon", "p1"), 2)] == ["p1", "p2"]
    assert grid.run(grid.get("Mon", "p2"), 2) is None, "p2+p3 straddles the break"
    assert [s.period for s in grid.run(grid.get("Mon", "p3"), 2)] == ["p3", "p4"]


def test_run_refuses_to_cross_a_day_boundary():
    grid = Grid(fixtures.base(days=["Mon", "Tue"], n_periods=2))
    assert grid.run(grid.get("Mon", "p2"), 2) is None


def test_segments_split_on_breaks():
    grid = Grid(fixtures.base(n_periods=4, break_after=2))
    assert [[s.period for s in seg] for seg in grid.segments("Mon")] == \
        [["p1", "p2"], ["p3", "p4"]]


def test_max_disjoint_runs_is_tighter_than_overlapping_runs():
    """Six periods split 3+3 offer four *overlapping* double periods a day but
    only two that can actually be used at once."""
    grid = Grid(fixtures.base(days=["Mon"], n_periods=6, break_after=3))
    assert len(grid.runs(2)) == 4
    assert grid.max_disjoint_runs(2) == 2
    assert grid.max_disjoint_runs(1) == 6
    assert grid.max_disjoint_runs(3) == 2
    assert grid.max_disjoint_runs(4) == 0
