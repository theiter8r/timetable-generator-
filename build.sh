#!/usr/bin/env bash
#
# Install the timetable app on macOS or Linux.
#
# Everything lands in a .venv/ inside this folder -- nothing is installed
# system-wide and nothing outside this directory is touched, so uninstalling is
# `rm -rf` on the folder. Safe to run again at any time; that is also how you
# upgrade after pulling new code.
#
#   ./build.sh              install (or repair) the app
#   ./build.sh --clean      throw away .venv first and install from scratch
#   ./build.sh --test       ...and run the test suite afterwards
#
set -euo pipefail

cd "$(dirname "$0")"

CLEAN=0
RUN_TESTS=0
for arg in "$@"; do
  case "$arg" in
    --clean) CLEAN=1 ;;
    --test|--tests) RUN_TESTS=1 ;;
    -h|--help) sed -n '2,13p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "Unknown option: $arg (try --help)" >&2; exit 2 ;;
  esac
done

say()  { printf '\033[1m%s\033[0m\n' "$*"; }
step() { printf '  %s\n' "$*"; }
die()  { printf '\n\033[31mInstall failed:\033[0m %s\n' "$*" >&2; exit 1; }

say "Timetable Generator — install"
echo

# --- 1. find a Python we can use ---------------------------------------
#
# 3.11 is the floor: the code uses `X | Y` unions at runtime and OR-Tools
# publishes no wheels for anything older.

PYTHON=""
for candidate in python3.13 python3.12 python3.11 python3 python; do
  if command -v "$candidate" >/dev/null 2>&1; then
    if "$candidate" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
      PYTHON="$candidate"
      break
    fi
  fi
done

if [ -z "$PYTHON" ]; then
  found="$(command -v python3 >/dev/null 2>&1 && python3 --version 2>&1 || echo 'none found')"
  die "Python 3.11 or newer is required (you have: $found).
       macOS:  brew install python@3.12       -- or download from python.org
       Ubuntu: sudo apt install python3.12 python3.12-venv"
fi

step "Python:  $("$PYTHON" --version) at $(command -v "$PYTHON")"

# --- 2. clean, if asked -------------------------------------------------

if [ "$CLEAN" = "1" ] && [ -d .venv ]; then
  step "Removing the existing .venv"
  rm -rf .venv
fi

# --- 3. install ---------------------------------------------------------
#
# uv, if it is here, installs the exact versions pinned in uv.lock and is a good
# deal faster. Without it, a plain venv + pip gets the same packages resolved
# fresh; both give a working app, so uv is a nicety rather than a requirement.

echo
if command -v uv >/dev/null 2>&1; then
  say "Installing dependencies with uv (locked versions)"
  uv sync || die "uv sync failed. Try again with: ./build.sh --clean"
else
  say "Installing dependencies with pip into .venv/"
  step "(install 'uv' for a faster, version-locked install: https://docs.astral.sh/uv/)"
  if [ ! -d .venv ]; then
    "$PYTHON" -m venv .venv || die "could not create a virtual environment.
       On Debian/Ubuntu this usually means: sudo apt install python3-venv"
  fi
  .venv/bin/python -m pip install --quiet --upgrade pip
  .venv/bin/python -m pip install --quiet -e . || die "pip could not install the dependencies.
       This step needs a working internet connection."
fi

[ -x .venv/bin/python ] || die "no .venv/bin/python after install -- try: ./build.sh --clean"

# --- 4. prove it actually works ----------------------------------------

echo
say "Checking the install"
.venv/bin/python scripts/check_install.py \
  || die "the app is installed but does not run. Try: ./build.sh --clean"

chmod +x start.sh 2>/dev/null || true

if [ "$RUN_TESTS" = "1" ]; then
  echo
  say "Running the test suite"
  .venv/bin/python -m pytest -q || die "the tests did not pass."
fi

echo
say "Done."
cat <<'TXT'

  Start the app with:   ./start.sh
  It opens http://localhost:8000 in your browser and walks you
  through setup the first time.

  Other commands:
    .venv/bin/python -m timetable.cli solve      generate from the saved config
    .venv/bin/python -m timetable.cli validate   pre-flight checks only
    .venv/bin/python -m timetable.cli reset      restore the sample dataset

TXT
