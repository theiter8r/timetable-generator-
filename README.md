# Timetable Generator

Generates clash-free class and teacher timetables for a college, using
[OR-Tools CP-SAT](https://developers.google.com/optimization/cp/cp_solver) — a
constraint solver. No LLM is involved in generating a timetable.

The central guarantee: **if Prof. A is teaching SE-B at Monday 09:00, she cannot
also appear in SE-C at Monday 09:00.** That is encoded as a hard constraint, so
the solver either returns a schedule with no clashes anywhere, or reports that
your configuration cannot be satisfied. It never returns a broken timetable.

## Running it

```bash
uv sync
uv run timetable serve          # http://localhost:8000
```

Other commands:

```bash
uv run timetable validate                 # pre-flight checks only
uv run timetable solve                    # solve and print a report
uv run timetable solve --out sheets.html  # ...and write every timetable
uv run timetable reset                    # restore the sample dataset
uv run pytest                             # the test suite
```

Configuration lives in `data/config.json`, seeded on first run from
`data/sample_config.json` — a realistic department of 6 divisions, 18 batches,
22 teachers and 84 teaching commitments. Edit it in the browser, or regenerate
the sample with `uv run python scripts/make_sample.py`.

## What you configure

| Tab | What it holds |
|---|---|
| Time grid | Working days, and the periods in a day. Mark lunch/tea as a **break**. |
| Divisions & batches | SE-B is a division; SE-B1/B2/B3 are its batches. |
| Subjects | Theory (taught to a division) or practical (taught to a batch). |
| Rooms | Classrooms, labs, capacities. |
| Teachers | Days off, blocked slots, morning/after-break preference. |
| Workload | One row per commitment: who teaches what, to whom, how often. |
| Pinned events | Fixed blocks the solver must schedule around. |
| Rules | The hard scheduling rules and the preference weights. |

**You allocate teachers** in the Workload tab; the solver decides only *when*
and *in which room* each session happens.

Breaks do real work: they split the day into morning and afternoon (that is what
"after the break" means for preferences), and a two-slot practical can never be
placed across one.

## What is guaranteed vs. preferred

Hard — always true of any timetable produced:

- No teacher is in two places at once, across every year and division.
- No batch is in two places at once. A division's lecture blocks all of its
  batches; sibling batches may still run practicals in parallel.
- No room hosts two sessions at once, and a practical only lands in a room of
  the right type and capacity.
- A multi-slot practical occupies genuinely back-to-back slots on one day.
- A teacher's days off and blocked slots are never used.
- Every assignment gets exactly the number of sessions you asked for.
- Optional: one session of a subject per day; all batches of a division in
  practicals together; a per-teacher daily cap.

Soft — maximised once every hard rule holds, with weights you can tune:

- Teacher morning / after-break preference, scaled by each teacher's weight.
- Even weekly spread of a batch's classes.
- Avoiding gaps in a student's day.

On the sample dataset the solver places all 144 sessions optimally in about 4
seconds and honours ~95% of teacher session preferences.

## When it cannot be done

An `INFEASIBLE` with no explanation is useless, so impossible configurations are
diagnosed instead:

1. **Pre-flight** (`validate.py`) runs before the solver and reports things like
   *"SE-B is assigned 40 slots of class but only 36 are free for it"*, or
   *"18 parallel lab blocks are required but at most 12 fit: 5 computer_lab
   rooms let only 1 division run practicals at a time, and the week has 12
   non-overlapping 2-slot blocks."*
2. **Fallback** — if it is still infeasible, the model is re-solved with the
   session-count requirement softened, so you get the closest possible timetable
   plus a per-assignment explanation of what did not fit and why.

## Layout

## Typography

The UI uses **Anthropic Serif**, self-hosted from `web/fonts/` — no external
requests. Both optical cuts are used for what they were drawn for:

| Cut | Weight | Used for |
|---|---|---|
| Display | Light 300 | The ring figure and the stats flanking it (42–66px) |
| Display | Regular 400 | Section headings, panel titles, metric values |
| Display | Medium 500 | The wordmark, and headings in the printable export |
| Text | Regular 400 | The editorial lead-in paragraph under each page title |

Everything else — tables, inputs, buttons, labels — stays in the system sans.
Display cuts are drawn for large sizes and get fragile in dense table copy, and
a data-entry grid reads faster in a sans face.

Only the four cuts in use are copied into `web/fonts/` (272 KB total). The full
family sits in `Anthropic Serif-fontiko/`; that folder is not served and can be
removed once you are happy with the selection. Fallbacks are Didot and Palatino,
so the page still renders correctly before the webfont lands.

## Layout

```
src/timetable/
  models.py      Pydantic entities; Config is the whole configuration
  grid.py        Slots, contiguous runs, morning/afternoon derivation
  resources.py   Who is blocked when; which rooms and students a session uses
  validate.py    Pre-flight feasibility checks
  solver.py      The CP-SAT model
  diagnose.py    Explains what could not be placed
  views.py       Solution -> division/batch/teacher/room grids, plus audit()
  export.py      Printable HTML and CSV
  api.py, cli.py Web API and command line
web/             Dependency-free vanilla JS UI (no build step)
tests/           Clash guarantees, grid rules, validation, API
```

`views.audit()` re-checks a finished timetable for clashes *independently of the
solver*, which is what the green "Verified clash-free" badge in the UI reports —
a second opinion rather than the solver's own word. The test suite uses the same
function.
