"""Turning a solution into the grids people actually read.

The same set of placed sessions is projected three ways -- by division (the
class timetable), by teacher, and by room. :func:`audit` re-checks the result
for clashes independently of the solver, so the UI can claim a timetable is
clash-free on the strength of a second opinion rather than the solver's word.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from .grid import Grid
from .models import Config, ScheduledSession, Solution
from .resources import Resources, is_synthetic


def _teacher_info(config: Config, teacher_id: str) -> dict[str, str]:
    teacher = config.teacher(teacher_id)
    if teacher is None:
        return {"id": teacher_id, "name": teacher_id, "short": teacher_id}
    return {
        "id": teacher.id,
        "name": teacher.name or teacher.id,
        "short": teacher.short or (teacher.name or teacher.id).split()[-1],
    }


def _entry(config: Config, session: ScheduledSession, *, continuation: bool) -> dict[str, Any]:
    subject = config.subject(session.subject)
    room = config.room(session.room) if session.room else None
    return {
        "assignment": session.assignment,
        "subject": session.subject,
        "subject_name": subject.name if subject else session.subject,
        "subject_short": (subject.short or subject.name or subject.id) if subject
                         else session.subject,
        "kind": subject.kind if subject else "theory",
        "teachers": [_teacher_info(config, t) for t in session.teachers],
        "room": session.room,
        "room_name": (room.name or room.id) if room else None,
        "target": session.target.id,
        "target_kind": session.target.kind,
        "span": len(session.slots),
        "continuation": continuation,
    }


def _period_rows(grid: Grid) -> list[dict[str, Any]]:
    """Every period including breaks, so printed grids show the lunch row."""
    return [
        {
            "id": p.id,
            "kind": p.kind,
            "start": p.start,
            "end": p.end,
            "label": p.label or f"{p.start}-{p.end}",
            "session": grid.sessions.get(p.id),
        }
        for p in grid.periods
    ]


def _empty_cells(config: Config, grid: Grid) -> dict[str, dict[str, list]]:
    return {day: {p.id: [] for p in grid.periods} for day in config.days}


def _build(config: Config, grid: Grid, kind: str, ident: str, title: str,
           sessions: list[tuple[ScheduledSession, bool]]) -> dict[str, Any]:
    cells = _empty_cells(config, grid)
    for session, _ in sessions:
        for position, ref in enumerate(session.slots):
            if ref.day in cells and ref.period in cells[ref.day]:
                cells[ref.day][ref.period].append(
                    _entry(config, session, continuation=position > 0)
                )
    return {
        "kind": kind,
        "id": ident,
        "title": title,
        "days": list(config.days),
        "periods": _period_rows(grid),
        "cells": cells,
    }


def division_view(config: Config, grid: Grid, res: Resources, solution: Solution,
                  division_id: str) -> dict[str, Any]:
    """The class timetable: the division's own lectures plus its batches' labs."""
    batches = set(res.division_batches.get(division_id, []))
    picked = [
        (s, True)
        for s in solution.sessions
        if (s.target.kind == "division" and s.target.id == division_id)
        or (s.target.kind == "batch" and s.target.id in batches)
    ]
    division = config.division(division_id)
    title = (division.name or division.id) if division else division_id
    view = _build(config, grid, "division", division_id, title, picked)
    view["batches"] = [b for b in sorted(batches) if not is_synthetic(b)]
    return view


def batch_view(config: Config, grid: Grid, res: Resources, solution: Solution,
               batch_id: str) -> dict[str, Any]:
    """One batch's week: its own practicals plus every lecture of its division."""
    division_id = res.batch_division.get(batch_id)
    picked = [
        (s, True)
        for s in solution.sessions
        if (s.target.kind == "batch" and s.target.id == batch_id)
        or (s.target.kind == "division" and s.target.id == division_id)
    ]
    return _build(config, grid, "batch", batch_id, batch_id, picked)


def teacher_view(config: Config, grid: Grid, solution: Solution,
                 teacher_id: str) -> dict[str, Any]:
    picked = [(s, True) for s in solution.sessions if teacher_id in s.teachers]
    teacher = config.teacher(teacher_id)
    title = (teacher.name or teacher.id) if teacher else teacher_id
    view = _build(config, grid, "teacher", teacher_id, title, picked)
    view["load"] = sum(len(s.slots) for s, _ in picked)
    if teacher:
        view["session_preference"] = teacher.session_preference
        view["honoured"] = _preference_score(grid, teacher.session_preference,
                                             [s for s, _ in picked])
    return view


def room_view(config: Config, grid: Grid, solution: Solution, room_id: str) -> dict[str, Any]:
    picked = [(s, True) for s in solution.sessions if s.room == room_id]
    room = config.room(room_id)
    title = (room.name or room.id) if room else room_id
    view = _build(config, grid, "room", room_id, title, picked)
    view["load"] = sum(len(s.slots) for s, _ in picked)
    return view


def _preference_score(grid: Grid, preference: str,
                      sessions: list[ScheduledSession]) -> dict[str, int]:
    """How much of a teacher's week landed in the half of the day they wanted."""
    if preference == "none":
        return {"matched": 0, "total": 0}
    total = matched = 0
    for session in sessions:
        for ref in session.slots:
            slot = grid.get(ref.day, ref.period)
            if slot is None:
                continue
            total += 1
            if slot.session == preference:
                matched += 1
    return {"matched": matched, "total": total}


def audit(config: Config, grid: Grid, res: Resources, solution: Solution) -> list[str]:
    """Independently re-check the solution for clashes.

    The solver's constraints should make every one of these impossible; this
    verifies the produced timetable rather than trusting that they did. An empty
    list means the schedule really is clash-free.
    """
    problems: list[str] = []
    teacher_at: dict[tuple[str, str, str], list[str]] = defaultdict(list)
    batch_at: dict[tuple[str, str, str], list[str]] = defaultdict(list)
    room_at: dict[tuple[str, str, str], list[str]] = defaultdict(list)

    for session in solution.sessions:
        for ref in session.slots:
            for teacher_id in session.teachers:
                teacher_at[(teacher_id, ref.day, ref.period)].append(session.assignment)
            for batch_id in res.batches_for(session.target):
                batch_at[(batch_id, ref.day, ref.period)].append(session.assignment)
            if session.room:
                room_at[(session.room, ref.day, ref.period)].append(session.assignment)

    for (teacher_id, day, period), items in sorted(teacher_at.items()):
        if len(items) > 1:
            name = _teacher_info(config, teacher_id)["name"]
            problems.append(
                f"CLASH: {name} is double-booked on {day} {period} ({', '.join(items)})."
            )
    for (batch_id, day, period), items in sorted(batch_at.items()):
        if len(items) > 1:
            label = batch_id.split("::")[0] if is_synthetic(batch_id) else batch_id
            problems.append(
                f"CLASH: {label} is double-booked on {day} {period} ({', '.join(items)})."
            )
    for (room_id, day, period), items in sorted(room_at.items()):
        if len(items) > 1:
            problems.append(
                f"CLASH: room {room_id} is double-booked on {day} {period} "
                f"({', '.join(items)})."
            )

    # Contiguity: a multi-slot session must be an unbroken run on one day.
    for session in solution.sessions:
        slots = [grid.get(r.day, r.period) for r in session.slots]
        if any(s is None for s in slots):
            problems.append(f"BROKEN: {session.assignment} refers to a non-teaching slot.")
            continue
        if len({s.day for s in slots}) > 1:
            problems.append(f"BROKEN: {session.assignment} is split across days.")
            continue
        indices = sorted(s.period_index for s in slots)
        if any(b - a != 1 for a, b in zip(indices, indices[1:])):
            problems.append(
                f"BROKEN: {session.assignment} is not back-to-back (a break splits it)."
            )

    # Unavailability: nothing may land on a teacher's blocked slots.
    for session in solution.sessions:
        for ref in session.slots:
            slot = grid.get(ref.day, ref.period)
            if slot is None:
                continue
            for teacher_id in session.teachers:
                if slot.index in res.teacher_blocked.get(teacher_id, set()):
                    name = _teacher_info(config, teacher_id)["name"]
                    problems.append(
                        f"UNAVAILABLE: {name} is scheduled on {ref.day} {ref.period} but is "
                        f"marked unavailable then ({session.assignment})."
                    )
    return problems


def summary(config: Config, grid: Grid, res: Resources, solution: Solution) -> dict[str, Any]:
    """Headline numbers plus the preference report card."""
    honoured = 0
    total = 0
    per_teacher = []
    for teacher in config.teachers:
        sessions = [s for s in solution.sessions if teacher.id in s.teachers]
        load = sum(len(s.slots) for s in sessions)
        score = _preference_score(grid, teacher.session_preference, sessions)
        honoured += score["matched"]
        total += score["total"]
        per_teacher.append({
            "id": teacher.id,
            "name": teacher.name or teacher.id,
            "load": load,
            "preference": teacher.session_preference,
            "matched": score["matched"],
            "of": score["total"],
        })

    requested = sum(a.sessions_per_week for a in config.assignments)
    return {
        "status": solution.status,
        "solve_seconds": solution.solve_seconds,
        "objective": solution.objective,
        "sessions_placed": len(solution.sessions),
        "sessions_requested": requested,
        "slots_filled": sum(len(s.slots) for s in solution.sessions),
        "preference_matched": honoured,
        "preference_total": total,
        "teachers": sorted(per_teacher, key=lambda t: -t["load"]),
        "clashes": audit(config, grid, res, solution),
        "messages": list(solution.messages),
        "unplaced": [u.model_dump() for u in solution.unplaced],
    }
