"""Post-install smoke test, run by build.sh and build.bat.

An install that finished without an error message can still be broken -- the
usual culprit is an OR-Tools wheel that does not match the platform, which only
shows up when something actually asks the solver to do work. So this imports
every dependency, builds a one-variable CP-SAT model, and reads the shipped
sample dataset. If this prints, the app runs.
"""

from __future__ import annotations

import sys


def main() -> int:
    try:
        import fastapi  # noqa: F401
        import pydantic  # noqa: F401
        import uvicorn  # noqa: F401
        from ortools.sat.python import cp_model

        from timetable.store import load_sample
    except Exception as exc:  # noqa: BLE001 -- the message is the whole point
        print(f"  FAILED: {exc.__class__.__name__}: {exc}", file=sys.stderr)
        return 1

    model = cp_model.CpModel()
    model.NewBoolVar("x")
    solver = cp_model.CpSolver()
    if solver.Solve(model) not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        print("  FAILED: OR-Tools is installed but could not solve a trivial model.",
              file=sys.stderr)
        return 1

    config = load_sample()
    print(f"  python:  {sys.version.split()[0]}")
    print("  solver:  OR-Tools CP-SAT ready")
    print(f"  sample:  {len(config.divisions)} divisions, {len(config.teachers)} teachers, "
          f"{len(config.assignments)} commitments")
    return 0


if __name__ == "__main__":
    sys.exit(main())
