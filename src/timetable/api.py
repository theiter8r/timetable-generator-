"""FastAPI app: config CRUD, solve, and the rendered timetable views.

Solutions are held in memory for the life of the process; the *config* is the
thing worth persisting, and a timetable can always be regenerated from it.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from . import export, views
from .diagnose import utilisation
from .grid import Grid
from .models import Config, Solution
from .resources import build_resources
from .solver import solve_with_fallback
from .store import ROOT, load_config, reset_config, save_config
from .validate import validate

WEB_DIR = ROOT / "web"

app = FastAPI(title="Timetable Generator")

# The most recent solve, so the view endpoints have something to render.
_state: dict[str, Any] = {"solution": None, "config": None}


def _context(config: Config | None = None):
    config = config or load_config()
    grid = Grid(config)
    return config, grid, build_resources(config, grid)


def _current() -> tuple[Config, Grid, Any, Solution]:
    solution = _state.get("solution")
    if solution is None:
        raise HTTPException(404, "No timetable generated yet -- POST /api/solve first.")
    config = _state.get("config") or load_config()
    grid = Grid(config)
    return config, grid, build_resources(config, grid), solution


# --- config ------------------------------------------------------------


@app.get("/api/config")
def get_config() -> dict[str, Any]:
    return load_config().model_dump(mode="json")


@app.put("/api/config")
def put_config(payload: Config) -> dict[str, Any]:
    save_config(payload)
    grid = Grid(payload)
    return {
        "ok": True,
        "issues": [i.model_dump() for i in validate(payload, grid)],
    }


@app.post("/api/config/reset")
def post_reset() -> dict[str, Any]:
    config = reset_config()
    _state["solution"] = None
    return config.model_dump(mode="json")


# --- validate & solve --------------------------------------------------


@app.post("/api/validate")
def post_validate() -> dict[str, Any]:
    config, grid, res = _context()
    issues = validate(config, grid, res)
    return {
        "issues": [i.model_dump() for i in issues],
        "errors": sum(1 for i in issues if i.level == "error"),
        "warnings": sum(1 for i in issues if i.level == "warning"),
        "utilisation": utilisation(config, grid, res),
    }


@app.post("/api/solve")
def post_solve() -> dict[str, Any]:
    config, grid, res = _context()
    issues = validate(config, grid, res)
    blocking = [i for i in issues if i.level == "error"]
    if blocking:
        # Don't burn 30s in the solver on a config we already know is impossible.
        return {
            "solved": False,
            "issues": [i.model_dump() for i in issues],
            "summary": None,
        }

    solution = solve_with_fallback(config)
    _state["solution"] = solution
    _state["config"] = config
    return {
        "solved": bool(solution.sessions),
        "issues": [i.model_dump() for i in issues],
        "summary": views.summary(config, grid, res, solution),
    }


@app.get("/api/solution")
def get_solution() -> dict[str, Any]:
    config, grid, res, solution = _current()
    return {"solved": True, "summary": views.summary(config, grid, res, solution)}


# --- rendered views ----------------------------------------------------


@app.get("/api/timetable/division/{division_id}")
def get_division(division_id: str) -> dict[str, Any]:
    config, grid, res, solution = _current()
    if config.division(division_id) is None:
        raise HTTPException(404, f"Unknown division {division_id!r}")
    return views.division_view(config, grid, res, solution, division_id)


@app.get("/api/timetable/batch/{batch_id}")
def get_batch(batch_id: str) -> dict[str, Any]:
    config, grid, res, solution = _current()
    if batch_id not in res.batch_division:
        raise HTTPException(404, f"Unknown batch {batch_id!r}")
    return views.batch_view(config, grid, res, solution, batch_id)


@app.get("/api/timetable/teacher/{teacher_id}")
def get_teacher(teacher_id: str) -> dict[str, Any]:
    config, grid, _res, solution = _current()
    if config.teacher(teacher_id) is None:
        raise HTTPException(404, f"Unknown teacher {teacher_id!r}")
    return views.teacher_view(config, grid, solution, teacher_id)


@app.get("/api/timetable/room/{room_id}")
def get_room(room_id: str) -> dict[str, Any]:
    config, grid, _res, solution = _current()
    if config.room(room_id) is None:
        raise HTTPException(404, f"Unknown room {room_id!r}")
    return views.room_view(config, grid, solution, room_id)


# --- export ------------------------------------------------------------


@app.get("/api/export/all.html", response_class=HTMLResponse)
def export_all() -> str:
    config, grid, res, solution = _current()
    return export.all_timetables_html(config, grid, res, solution)


@app.get("/api/export/all.csv", response_class=PlainTextResponse)
def export_csv() -> str:
    config, grid, res, solution = _current()
    return export.sessions_csv(config, res, solution)


# --- static UI ---------------------------------------------------------

if WEB_DIR.exists():
    app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")

    @app.get("/", response_class=HTMLResponse)
    def index() -> FileResponse:
        return FileResponse(WEB_DIR / "index.html")
