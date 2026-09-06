#!/usr/bin/env bash
#
# Run the timetable app on macOS or Linux.
#
#   ./start.sh                 start on the first free port from 8000, open a browser
#   ./start.sh 8080            ...on this port instead
#   ./start.sh --no-browser    don't open a browser
#   ./start.sh --lan           also reachable from other machines on the network
#
# Stop the app with Ctrl-C. Nothing is lost: everything you enter is saved to
# data/config.json as you go.
#
set -euo pipefail

cd "$(dirname "$0")"

PORT="${PORT:-8000}"
HOST="127.0.0.1"
OPEN_BROWSER=1

for arg in "$@"; do
  case "$arg" in
    --no-browser) OPEN_BROWSER=0 ;;
    --lan) HOST="0.0.0.0" ;;
    -h|--help) sed -n '2,11p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    ''|*[!0-9]*) echo "Unknown option: $arg (try --help)" >&2; exit 2 ;;
    *) PORT="$arg" ;;
  esac
done

if [ ! -x .venv/bin/python ]; then
  echo "The app is not installed yet. Run this first:" >&2
  echo "    ./build.sh" >&2
  exit 1
fi

# Port 8000 is popular; rather than dying with "address already in use", walk up
# until something is free.
PORT="$(.venv/bin/python scripts/pick_port.py "$PORT")" || exit 1

URL="http://localhost:${PORT}"

if [ "$OPEN_BROWSER" = "1" ]; then
  opener=""
  command -v open >/dev/null 2>&1 && opener="open"                 # macOS
  [ -z "$opener" ] && command -v xdg-open >/dev/null 2>&1 && opener="xdg-open"
  if [ -n "$opener" ]; then
    # A moment behind the server, so the first request doesn't hit a closed port.
    ( sleep 2; "$opener" "$URL" ) >/dev/null 2>&1 &
  fi
fi

printf '\033[1mTimetable Generator\033[0m  →  %s\n' "$URL"
[ "$HOST" = "0.0.0.0" ] && echo "Reachable from other machines on this network."
echo "Press Ctrl-C to stop."
echo

exec .venv/bin/python -m timetable.cli serve --host "$HOST" --port "$PORT"
