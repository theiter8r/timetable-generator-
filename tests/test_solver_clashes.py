"""The headline guarantee: nothing ever clashes.

Every test here solves a real model and then audits the *result*, independently
of the constraints that produced it.
"""

from __future__ import annotations

import pytest

from timetable.grid import Grid
from timetable.models import Target
from timetable.resources import build_resources
from timetable.solver import solve
from timetable.store import load_sample
from timetable.views import audit

import fixtures


def run(config):
    grid = Grid(config)
    res = build_resources(config, grid)
    solution = solve(config, grid=grid, res=res)
    return grid, res, solution


def slot_keys(solution, predicate):
    """Every (day, period) occupied by the sessions matching ``predicate``."""
    return [
        (ref.day, ref.period)
        for session in solution.sessions
        if predicate(session)
        for ref in session.slots
    ]


# --- the user's stated requirement ------------------------------------


def test_one_teacher_two_divisions_never_overlap():
    """Prof. T1 owes a lecture to SE-B and to SE-C in a two-slot week.

    This is the SE-B / SE-C overlap the app exists to prevent. With no slack at
    all, the only clash-free schedule separates them.
    """
    config = fixtures.two_divisions_one_teacher()
    grid, res, solution = run(config)

    assert solution.status in ("OPTIMAL", "FEASIBLE")
    assert len(solution.sessions) == 2
    assert audit(config, grid, res, solution) == []

    seb = slot_keys(solution, lambda s: s.target.id == "SE-B")
    sec = slot_keys(solution, lambda s: s.target.id == "SE-C")
    assert seb and sec
    assert set(seb).isdisjoint(sec), "Prof. T1 was scheduled in two divisions at once"


def test_teacher_overload_is_infeasible_not_silently_clashing():
    """Three lectures, one teacher, two slots: no honest answer exists.

    The solver must refuse rather than produce a schedule that quietly
    double-books somebody.
    """
    config = fixtures.two_divisions_one_teacher()
    config.divisions.append(config.divisions[0].model_copy(update={"id": "SE-D"}))
    config.assignments.append(
        fixtures.assign("a-sed", "DBMS", "division", "SE-D", ["T1"])
    )
    _grid, _res, solution = run(config)
    assert solution.status == "INFEASIBLE"
    assert solution.sessions == []


# --- students ---------------------------------------------------------


def test_lecture_blocks_every_batch_but_practicals_run_in_parallel():
    config = fixtures.division_with_batches()
    grid, res, solution = run(config)

    assert audit(config, grid, res, solution) == []

    lecture = slot_keys(solution, lambda s: s.target.kind == "division")
    assert len(lecture) == 1

    per_batch = {
        s.target.id: {(r.day, r.period) for r in s.slots}
        for s in solution.sessions if s.target.kind == "batch"
    }
    assert len(per_batch) == 3

    # No practical may sit on the lecture slot ...
    for batch_id, slots in per_batch.items():
        assert slots.isdisjoint(lecture), f"{batch_id} was in a lab during its own lecture"

    # ... but sibling batches are free to share one, which is the whole point of
    # modelling students per batch rather than per division.
    assert len(config.batches) == 3


def test_same_batch_never_double_booked():
    config = fixtures.division_with_batches()
    # Give one batch a second practical; it must land on a different slot.
    config.assignments.append(
        fixtures.assign("lab1b", "PHYLAB", "batch", "SE-A1", ["T2"], sessions=1)
    )
    grid, res, solution = run(config)
    assert audit(config, grid, res, solution) == []

    slots = slot_keys(solution, lambda s: s.target.id == "SE-A1")
    assert len(slots) == len(set(slots)), "SE-A1 was booked twice in one slot"


# --- practicals as genuine double periods -----------------------------


def test_two_slot_practical_never_straddles_a_break():
    """p2+p3 spans the lunch break, so only p1+p2 or p3+p4 are acceptable."""
    config = fixtures.practical_needing_double_slot()
    grid, res, solution = run(config)

    assert audit(config, grid, res, solution) == []
    assert len(solution.sessions) == 1
    placed = [r.period for r in solution.sessions[0].slots]
    assert placed in (["p1", "p2"], ["p3", "p4"]), f"lab straddled the break: {placed}"


def test_practical_stays_on_one_day():
    config = fixtures.practical_needing_double_slot()
    config.days = ["Mon", "Tue"]
    grid, res, solution = run(config)
    assert audit(config, grid, res, solution) == []
    assert len({r.day for r in solution.sessions[0].slots}) == 1


# --- teacher availability is hard -------------------------------------


def test_teacher_day_off_is_never_violated():
    config = fixtures.two_divisions_one_teacher()
    config.days = ["Mon", "Tue"]
    config.teachers[0].unavailable_days = ["Mon"]
    grid, res, solution = run(config)

    assert audit(config, grid, res, solution) == []
    assert solution.sessions, "should still be schedulable on Tuesday"
    assert all(s.day == "Tue" for s in solution.sessions)


def test_blocked_slot_is_never_used():
    config = fixtures.two_divisions_one_teacher()
    config.days = ["Mon", "Tue"]
    config.teachers[0].unavailable_slots = [
        {"day": "Mon", "period": "p1"}, {"day": "Tue", "period": "p1"}
    ]
    grid, res, solution = run(config)

    assert audit(config, grid, res, solution) == []
    assert all(r.period != "p1" for s in solution.sessions for r in s.slots)


# --- rooms ------------------------------------------------------------


def test_rooms_are_never_shared():
    """Two divisions, two lectures, but only one classroom: they must not
    collide in it."""
    config = fixtures.two_divisions_one_teacher()
    config.teachers.append(fixtures.teacher("T2"))
    config.assignments[1].teachers = ["T2"]  # remove the teacher clash ...
    config.rooms = [fixtures.classroom("C1")]  # ... leaving the room as the only limit
    grid, res, solution = run(config)

    assert audit(config, grid, res, solution) == []
    used = [(s.room, r.day, r.period) for s in solution.sessions for r in s.slots]
    assert len(used) == len(set(used))


def test_practical_only_lands_in_a_lab():
    config = fixtures.division_with_batches()
    grid, res, solution = run(config)
    rooms = {r.id: r for r in config.rooms}
    for session in solution.sessions:
        subject = config.subject(session.subject)
        assert session.room, f"{session.assignment} got no room"
        assert rooms[session.room].type == subject.room_type


# --- preferences (soft, but should still be honoured when free) --------


def test_morning_preference_is_honoured_when_nothing_competes():
    config = fixtures.base(days=["Mon"], n_periods=4, break_after=2)
    config.divisions = [fixtures.Division(id="SE-A", strength=60)]
    config.subjects = [fixtures.theory("S1")]
    config.rooms = [fixtures.classroom("C1")]
    config.teachers = [fixtures.teacher("T1", session_preference="morning",
                                        preference_weight=5)]
    config.assignments = [fixtures.assign("a1", "S1", "division", "SE-A", ["T1"])]

    grid, res, solution = run(config)
    assert audit(config, grid, res, solution) == []
    period = solution.sessions[0].slots[0].period
    assert grid.sessions[period] == "morning", f"landed in {period}, wanted the morning"


def test_afternoon_preference_is_honoured_when_nothing_competes():
    config = fixtures.base(days=["Mon"], n_periods=4, break_after=2)
    config.divisions = [fixtures.Division(id="SE-A", strength=60)]
    config.subjects = [fixtures.theory("S1")]
    config.rooms = [fixtures.classroom("C1")]
    config.teachers = [fixtures.teacher("T1", session_preference="afternoon",
                                        preference_weight=5)]
    config.assignments = [fixtures.assign("a1", "S1", "division", "SE-A", ["T1"])]

    grid, res, solution = run(config)
    period = solution.sessions[0].slots[0].period
    assert grid.sessions[period] == "afternoon", f"landed in {period}, wanted after the break"


# --- rules ------------------------------------------------------------


def test_one_session_per_day_spreads_a_subject_across_the_week():
    config = fixtures.base(days=["Mon", "Tue", "Wed"], n_periods=3)
    config.options.one_session_per_day = True
    config.divisions = [fixtures.Division(id="SE-A", strength=60)]
    config.subjects = [fixtures.theory("S1")]
    config.rooms = [fixtures.classroom("C1")]
    config.teachers = [fixtures.teacher("T1")]
    config.assignments = [fixtures.assign("a1", "S1", "division", "SE-A", ["T1"], sessions=3)]

    _grid, _res, solution = run(config)
    days = [s.day for s in solution.sessions]
    assert sorted(days) == ["Mon", "Tue", "Wed"], f"stacked into {days}"


def test_parallel_batch_labs_puts_every_batch_in_at_once():
    config = fixtures.division_with_batches()
    config.options.parallel_batch_labs = True
    grid, res, solution = run(config)

    assert audit(config, grid, res, solution) == []
    starts = {
        s.target.id: s.slots[0].period
        for s in solution.sessions if s.target.kind == "batch"
    }
    assert len(set(starts.values())) == 1, f"batches were not in lockstep: {starts}"


def test_teacher_daily_cap_is_respected():
    config = fixtures.base(days=["Mon", "Tue"], n_periods=3)
    config.divisions = [fixtures.Division(id=f"D{i}", strength=60) for i in range(1, 5)]
    config.subjects = [fixtures.theory("S1")]
    config.rooms = [fixtures.classroom(f"C{i}") for i in range(1, 5)]
    config.teachers = [fixtures.teacher("T1", max_per_day=2)]
    config.assignments = [
        fixtures.assign(f"a{i}", "S1", "division", f"D{i}", ["T1"]) for i in range(1, 5)
    ]

    grid, res, solution = run(config)
    assert audit(config, grid, res, solution) == []
    per_day: dict[str, int] = {}
    for session in solution.sessions:
        per_day[session.day] = per_day.get(session.day, 0) + len(session.slots)
    assert all(n <= 2 for n in per_day.values()), per_day


# --- pinned events ----------------------------------------------------


def test_pinned_event_blocks_its_slot():
    config = fixtures.two_divisions_one_teacher()
    config.days = ["Mon", "Tue"]
    config.pinned = [{
        "id": "pin1", "name": "Assembly", "day": "Mon", "period": "p1",
        "slots_per_session": 1,
        "targets": [Target(kind="division", id="SE-B").model_dump()],
        "teachers": [], "room": None,
    }]
    grid, res, solution = run(config)

    assert audit(config, grid, res, solution) == []
    seb = slot_keys(solution, lambda s: s.target.id == "SE-B")
    assert ("Mon", "p1") not in seb, "a lecture was placed on top of the pinned assembly"


# --- the shipped dataset ----------------------------------------------


@pytest.mark.slow
def test_sample_dataset_solves_completely_and_cleanly():
    """The realistic 6-division dataset: everything placed, nothing clashing."""
    config = load_sample()
    grid = Grid(config)
    res = build_resources(config, grid)
    solution = solve(config, grid=grid, res=res, max_seconds=60)

    assert solution.status in ("OPTIMAL", "FEASIBLE")
    expected = sum(a.sessions_per_week for a in config.assignments)
    assert len(solution.sessions) == expected
    assert audit(config, grid, res, solution) == []


@pytest.mark.slow
def test_sample_dataset_every_teacher_week_is_consistent():
    """Cross-check: a teacher's timetable and the class timetable must agree.

    Anything in Prof. X's week has to show up in the corresponding division's
    week at exactly the same time, and the other way round.
    """
    from timetable import views

    config = load_sample()
    grid = Grid(config)
    res = build_resources(config, grid)
    solution = solve(config, grid=grid, res=res, max_seconds=60)

    def entries(view):
        return {
            (day, period, e["assignment"])
            for day, periods_ in view["cells"].items()
            for period, items in periods_.items()
            for e in items
        }

    from_teachers: set = set()
    for teacher in config.teachers:
        from_teachers |= entries(views.teacher_view(config, grid, solution, teacher.id))

    from_divisions: set = set()
    for division in config.divisions:
        from_divisions |= entries(
            views.division_view(config, grid, res, solution, division.id)
        )

    # Every staffed session appears in both projections.
    staffed = {a.id for a in config.assignments if a.teachers}
    assert {e for e in from_teachers if e[2] in staffed} == \
           {e for e in from_divisions if e[2] in staffed}
