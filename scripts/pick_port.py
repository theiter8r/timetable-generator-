"""Print the first free TCP port at or after the one given.

Used by start.sh and start.bat. Port 8000 is popular enough that a second copy
of something else already has it more often than not, and "address already in
use" is a poor greeting for someone who has just installed the app.
"""

from __future__ import annotations

import socket
import sys

TRIES = 25


def is_free(port: int) -> bool:
    with socket.socket() as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind(("127.0.0.1", port))
        except OSError:
            return False
        return True


def main(argv: list[str]) -> int:
    start = int(argv[1]) if len(argv) > 1 else 8000
    for port in range(start, start + TRIES):
        if is_free(port):
            print(port)
            return 0
    print(f"No free port between {start} and {start + TRIES - 1}.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
