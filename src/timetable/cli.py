"""Command line entry point: ``timetable serve`` and ``timetable solve``."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import export, views
from .grid import Grid
from .resources import build_resources
from .solver import solve_with_fallback
from .store import load_config, reset_config
from .validate import validate


def _print_issues(issues) -> int:
    errors = sum(1 for i in issues if i.level == "error")
    for issue in issues:
        marker = "ERROR  " if issue.level == "error" else "warning"
        where = f" [{issue.entity}]" if issue.entity else ""
        print(f"  {marker}{where} {issue.message}")
    return errors


def cmd_solve(args: argparse.Namespace) -> int:
    config = load_config()
    grid = Grid(config)
    res = build_resources(config, grid)

    issues = validate(config, grid, res)
    if issues:
        print(f"Pre-flight found {len(issues)} issue(s):")
        if _print_issues(issues):
            print("\nFix the errors above; the configuration cannot be satisfied as it stands.")
            return 1
        print()

    print(f"Solving: {len(config.assignments)} assignments over {len(grid)} slots ...")
    solution = solve_with_fallback(config, max_seconds=args.max_seconds)
    summary = views.summary(config, grid, res, solution)

    print(f"  status            {summary['status']} in {summary['solve_seconds']}s")
    print(f"  sessions placed   {summary['sessions_placed']}/{summary['sessions_requested']}")
    print(f"  slots filled      {summary['slots_filled']}")
    if summary["preference_total"]:
        pct = 100 * summary["preference_matched"] / summary["preference_total"]
        print(f"  preferences met   {summary['preference_matched']}/"
              f"{summary['preference_total']} slots ({pct:.0f}%)")

    for message in summary["messages"]:
        print(f"  note: {message}")
    for item in summary["unplaced"]:
        print(f"  UNPLACED {item['assignment']}: {item['placed']}/{item['requested']} "
              f"placed. {item['reason']}")

    clashes = summary["clashes"]
    if clashes:
        print(f"\n  {len(clashes)} CLASH(ES) FOUND -- this is a bug in the solver:")
        for clash in clashes[:10]:
            print(f"    {clash}")
        return 2
    if solution.sessions:
        print("  verified          no teacher, student or room clashes")

    if args.out:
        target = Path(args.out)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.suffix == ".csv":
            target.write_text(export.sessions_csv(config, res, solution))
        else:
            target.write_text(export.all_timetables_html(config, grid, res, solution))
        print(f"  wrote             {target}")

    return 0 if solution.sessions else 1


def cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    print(f"Timetable app on http://{args.host}:{args.port}")
    uvicorn.run("timetable.api:app", host=args.host, port=args.port, reload=args.reload)
    return 0


def cmd_validate(_args: argparse.Namespace) -> int:
    config = load_config()
    issues = validate(config)
    if not issues:
        print("Configuration is consistent; nothing to report.")
        return 0
    print(f"{len(issues)} issue(s):")
    return 1 if _print_issues(issues) else 0


def cmd_reset(_args: argparse.Namespace) -> int:
    reset_config()
    print("Configuration reset to the shipped sample dataset.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="timetable", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    serve = sub.add_parser("serve", help="run the web app")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    serve.add_argument("--reload", action="store_true")
    serve.set_defaults(func=cmd_serve)

    run = sub.add_parser("solve", help="generate timetables from the saved config")
    run.add_argument("--max-seconds", type=float, default=None)
    run.add_argument("--out", help="write all timetables to this .html or .csv file")
    run.set_defaults(func=cmd_solve)

    check = sub.add_parser("validate", help="run the pre-flight checks only")
    check.set_defaults(func=cmd_validate)

    back = sub.add_parser("reset", help="restore the sample configuration")
    back.set_defaults(func=cmd_reset)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
