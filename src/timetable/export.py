"""Printable output: one HTML document with every timetable, and a flat CSV."""

from __future__ import annotations

import csv
import html
import io
from typing import Any

from .grid import Grid
from .models import Config, Solution
from .resources import Resources, is_synthetic
from .views import batch_view, division_view, room_view, teacher_view

PRINT_CSS = """
/* Served from the app, so the webfont resolves; a saved-off copy falls back to
   the local serifs without breaking the layout. */
@font-face { font-family: "Anthropic Serif Display";
  src: url("/static/fonts/AnthropicSerif-Display-Regular.otf") format("opentype");
  font-weight: 400; font-display: swap; }
@font-face { font-family: "Anthropic Serif Display";
  src: url("/static/fonts/AnthropicSerif-Display-Medium.otf") format("opentype");
  font-weight: 500; font-display: swap; }
:root { color-scheme: light;
  --display: "Anthropic Serif Display", Didot, "Bodoni 72", Palatino, Georgia, serif; }
* { box-sizing: border-box; }
body { font: 13px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
       margin: 26px; color: #16150f; background: #fff; }
h1 { font-family: var(--display);
     font-weight: 500; font-size: 31px; margin: 34px 0 6px; letter-spacing: .005em; }
h1:first-of-type { margin-top: 0; }
h2 { font-family: var(--display);
     font-weight: 400; font-size: 20px; margin: 0 0 10px; page-break-before: always; }
h2:first-of-type { page-break-before: avoid; }
.meta { color: #817d71; font-size: 11px; margin-bottom: 22px; text-transform: uppercase;
        letter-spacing: .09em; }
table { border-collapse: collapse; width: 100%; margin-bottom: 30px;
        page-break-inside: avoid; }
th, td { border: 1px solid #e4e0d4; padding: 7px 8px; text-align: left;
         vertical-align: top; font-size: 11px; }
th { background: #efece4; color: #817d71; font-weight: 650; font-size: 9.5px;
     text-transform: uppercase; letter-spacing: .09em; }
td.time { white-space: nowrap; color: #817d71; width: 94px; font-variant-numeric: tabular-nums; }
tr.break td { background: #efece4; color: #817d71; text-align: center; font-style: italic;
              letter-spacing: .04em; }
.entry { margin-bottom: 7px; padding-left: 7px; border-left: 2.5px solid #7b5bd6; }
.entry:last-child { margin-bottom: 0; }
.entry.practical { border-left-color: #35c04a; }
.subj { font-weight: 650; }
.who, .where { color: #817d71; display: block; font-size: 10.5px; }
.dbl { display: block; color: #817d71; font-size: 9px; text-transform: uppercase;
       letter-spacing: .07em; }
.tag { display: inline-block; background: #e7e3d8; border-radius: 999px; padding: 1px 6px;
       margin-right: 4px; font-size: 8.5px; font-weight: 700; text-transform: uppercase;
       letter-spacing: .06em; }
.practical .subj { color: #1f8c39; }
@media print { body { margin: 10mm; } h2 { page-break-before: always; } }
"""


def _cell_html(entries: list[dict[str, Any]]) -> str:
    if not entries:
        return ""
    out = []
    for entry in entries:
        if entry["continuation"]:
            continue  # rendered on the first slot of the run
        who = ", ".join(t["name"] for t in entry["teachers"]) or "—"
        where = entry["room_name"] or entry["room"] or ""
        klass = "entry practical" if entry["kind"] == "practical" else "entry"
        tag = ""
        if entry["target_kind"] == "batch":
            tag = f'<span class="tag">{html.escape(entry["target"])}</span>'
        span = '<span class="dbl">double period</span>' if entry["span"] > 1 else ""
        out.append(
            f'<div class="{klass}">{tag}<span class="subj">'
            f'{html.escape(entry["subject_short"])}</span>{span}'
            f'<span class="who">{html.escape(who)}</span>'
            + (f'<span class="where">{html.escape(where)}</span>' if where else "")
            + "</div>"
        )
    return "".join(out)


def view_html(view: dict[str, Any]) -> str:
    days = view["days"]
    rows = []
    for period in view["periods"]:
        if period["kind"] == "break":
            label = html.escape(period["label"] or "Break")
            rows.append(
                f'<tr class="break"><td class="time">{html.escape(period["start"])}–'
                f'{html.escape(period["end"])}</td>'
                f'<td colspan="{len(days)}">{label}</td></tr>'
            )
            continue
        cells = "".join(
            f"<td>{_cell_html(view['cells'][day][period['id']])}</td>" for day in days
        )
        rows.append(
            f'<tr><td class="time">{html.escape(period["start"])}–'
            f'{html.escape(period["end"])}</td>{cells}</tr>'
        )

    header = "".join(f"<th>{html.escape(d)}</th>" for d in days)
    return (
        f'<h2>{html.escape(view["title"])}</h2>'
        f"<table><thead><tr><th>Time</th>{header}</tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table>"
    )


def all_timetables_html(config: Config, grid: Grid, res: Resources,
                        solution: Solution) -> str:
    parts: list[str] = []

    parts.append("<h1>Class timetables</h1>")
    for division in config.divisions:
        parts.append(view_html(division_view(config, grid, res, solution, division.id)))
    for batch_id in sorted(res.batch_division):
        if is_synthetic(batch_id):
            continue
        parts.append(view_html(batch_view(config, grid, res, solution, batch_id)))

    parts.append("<h1>Teacher timetables</h1>")
    for teacher in config.teachers:
        parts.append(view_html(teacher_view(config, grid, solution, teacher.id)))

    parts.append("<h1>Room timetables</h1>")
    for room in config.rooms:
        parts.append(view_html(room_view(config, grid, solution, room.id)))

    placed = len(solution.sessions)
    requested = sum(a.sessions_per_week for a in config.assignments)
    meta = (
        f"{placed} of {requested} sessions placed · solver status {solution.status} · "
        f"{solution.solve_seconds}s"
    )
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<title>Timetables</title>"
        f"<style>{PRINT_CSS}</style></head><body>"
        f"<div class='meta'>{html.escape(meta)}</div>"
        f"{''.join(parts)}</body></html>"
    )


def sessions_csv(config: Config, res: Resources, solution: Solution) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(
        ["day", "start_period", "slots", "target_kind", "target", "division",
         "subject", "subject_name", "kind", "teachers", "room"]
    )
    for session in solution.sessions:
        subject = config.subject(session.subject)
        division = (
            session.target.id
            if session.target.kind == "division"
            else res.batch_division.get(session.target.id, "")
        )
        writer.writerow([
            session.day,
            session.start_period,
            len(session.slots),
            session.target.kind,
            session.target.id,
            division,
            session.subject,
            subject.name if subject else "",
            subject.kind if subject else "",
            "; ".join(
                (config.teacher(t).name if config.teacher(t) else t) for t in session.teachers
            ),
            session.room or "",
        ])
    return buf.getvalue()
