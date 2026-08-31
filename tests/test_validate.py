"""Pre-flight checks: an impossible config must explain itself, not just fail."""

from __future__ import annotations

from timetable.grid import Grid
from timetable.resources import build_resources
from timetable.solver import solve, solve_with_fallback
from timetable.store import load_sample
from timetable.validate import validate

import fixtures


def codes(config):
    return {i.code for i in validate(config)}


def message_for(config, code):
    return next(i.message for i in validate(config) if i.code == code)


def test_sample_dataset_is_clean():
    assert validate(load_sample()) == []


def test_more_class_than_the_week_can_hold():
    config = fixtures.oversubscribed()
    assert "student-overload" in codes(config)
    message = message_for(config, "student-overload")
    assert "SE-A" in message and "5" in message and "3" in message


def test_teacher_assigned_more_than_they_have_time_for():
    config = fixtures.base(days=["Mon"], n_periods=2)
    config.divisions = [fixtures.Division(id=f"D{i}", strength=60) for i in (1, 2, 3)]
    config.subjects = [fixtures.theory("S1")]
    config.rooms = [fixtures.classroom(f"C{i}") for i in (1, 2, 3)]
    config.teachers = [fixtures.teacher("T1")]
    config.assignments = [
        fixtures.assign(f"a{i}", "S1", "division", f"D{i}", ["T1"]) for i in (1, 2, 3)
    ]
    assert "teacher-overload" in codes(config)
    assert "Prof. T1" in message_for(config, "teacher-overload")


def test_days_off_count_against_a_teachers_availability():
    config = fixtures.base(days=["Mon", "Tue"], n_periods=2)
    config.divisions = [fixtures.Division(id=f"D{i}", strength=60) for i in (1, 2, 3)]
    config.subjects = [fixtures.theory("S1")]
    config.rooms = [fixtures.classroom(f"C{i}") for i in (1, 2, 3)]
    config.teachers = [fixtures.teacher("T1", unavailable_days=["Tue"])]
    config.assignments = [
        fixtures.assign(f"a{i}", "S1", "division", f"D{i}", ["T1"]) for i in (1, 2, 3)
    ]
    # 3 sessions, but Monday's 2 slots are all that is left.
    assert "teacher-overload" in codes(config)
    assert "blocked by days off" in message_for(config, "teacher-overload")


def test_daily_cap_that_cannot_fit_the_load():
    config = fixtures.base(days=["Mon", "Tue"], n_periods=3)
    config.divisions = [fixtures.Division(id=f"D{i}", strength=60) for i in range(1, 5)]
    config.subjects = [fixtures.theory("S1")]
    config.rooms = [fixtures.classroom(f"C{i}") for i in range(1, 5)]
    config.teachers = [fixtures.teacher("T1", max_per_day=1)]
    config.assignments = [
        fixtures.assign(f"a{i}", "S1", "division", f"D{i}", ["T1"]) for i in range(1, 5)
    ]
    assert "teacher-daily-cap" in codes(config)


def test_not_enough_rooms():
    config = fixtures.base(days=["Mon"], n_periods=2)
    config.divisions = [fixtures.Division(id=f"D{i}", strength=60) for i in (1, 2, 3)]
    config.subjects = [fixtures.theory("S1")]
    config.rooms = [fixtures.classroom("C1")]
    config.teachers = [fixtures.teacher(f"T{i}") for i in (1, 2, 3)]
    config.assignments = [
        fixtures.assign(f"a{i}", "S1", "division", f"D{i}", [f"T{i}"]) for i in (1, 2, 3)
    ]
    assert "room-overload" in codes(config)


def test_no_room_big_enough():
    config = fixtures.base(days=["Mon"], n_periods=2)
    config.divisions = [fixtures.Division(id="D1", strength=200)]
    config.subjects = [fixtures.theory("S1")]
    config.rooms = [fixtures.classroom("C1", capacity=30)]
    config.teachers = [fixtures.teacher("T1")]
    config.assignments = [fixtures.assign("a1", "S1", "division", "D1", ["T1"])]
    assert "no-room" in codes(config)
    assert "200 students" in message_for(config, "no-room")


def test_practical_too_long_for_any_unbroken_stretch():
    config = fixtures.practical_needing_double_slot()
    config.assignments[0].slots_per_session = 3  # segments are only 2 long
    assert "no-placement" in codes(config)


def test_dangling_references_are_caught():
    config = fixtures.two_divisions_one_teacher()
    config.assignments[0].subject = "NOPE"
    config.assignments[1].teachers = ["GHOST"]
    issues = validate(config)
    assert {i.code for i in issues} == {"bad-ref"}
    assert any("NOPE" in i.message for i in issues)
    assert any("GHOST" in i.message for i in issues)


def test_uneven_batch_load_blocks_parallel_labs():
    config = fixtures.division_with_batches()
    config.options.parallel_batch_labs = True
    config.assignments.append(
        fixtures.assign("extra", "PHYLAB", "batch", "SE-A1", ["T4"])
    )
    assert "uneven-batch-load" in codes(config)
    assert "SE-A1=2" in message_for(config, "uneven-batch-load")


def test_lab_block_capacity_matches_actual_solvability():
    """The structural bound must agree with what the solver can really do.

    Three batches per division means a lab block needs three labs at once, so
    two labs let *no* division run practicals in parallel.
    """
    config = fixtures.division_with_batches()
    config.options.parallel_batch_labs = True
    config.rooms = [fixtures.classroom("C1")] + [fixtures.lab(f"L{i}", 25) for i in (1, 2)]

    assert "lab-block-overload" in codes(config)

    grid = Grid(config)
    res = build_resources(config, grid)
    assert solve(config, grid=grid, res=res).status == "INFEASIBLE", \
        "the validator claimed impossible; the solver must agree"


def test_spread_relaxation_is_only_a_warning():
    config = fixtures.base(days=["Mon", "Tue"], n_periods=4)
    config.options.one_session_per_day = True
    config.divisions = [fixtures.Division(id="D1", strength=60)]
    config.subjects = [fixtures.theory("S1")]
    config.rooms = [fixtures.classroom("C1")]
    config.teachers = [fixtures.teacher("T1")]
    config.assignments = [fixtures.assign("a1", "S1", "division", "D1", ["T1"], sessions=3)]

    issues = validate(config)
    assert [i.level for i in issues] == ["warning"]
    assert issues[0].code == "spread-relaxed"
    # And it really does still solve, three sessions across two days.
    assert len(solve(config).sessions) == 3


# --- graceful degradation ---------------------------------------------


def test_fallback_reports_what_could_not_be_placed():
    """When nothing satisfies everything, we return the closest fit plus a
    reason -- not an empty page."""
    config = fixtures.oversubscribed()   # 5 sessions into 3 slots
    solution = solve_with_fallback(config)

    assert solution.status == "PARTIAL"
    assert len(solution.sessions) == 3
    assert len(solution.unplaced) == 1

    unplaced = solution.unplaced[0]
    assert unplaced.assignment == "a1"
    assert (unplaced.requested, unplaced.placed) == (5, 3)
    assert unplaced.reason, "an unplaced session must come with an explanation"
    assert solution.messages
