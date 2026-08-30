"""Headless mode for the bundled application.

Used by the build pipeline to verify a bundle end-to-end and useful for
running the bundled stack on a schedule or from scripts. Reports component
state on stdout (guarded for windowed builds where stdout is a null writer)
and exits cleanly when a stop flag file appears, ``--quit-after`` expires, or
the stack fails.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from nomad_app.server import ServerManager


def _out(message: str) -> None:
    if sys.stdout is not None:
        print(message, flush=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="Nomad --serve", description="Headless bundled Nomad")
    parser.add_argument("--stop-flag", default=None, help="shut down when this file appears")
    parser.add_argument("--quit-after", type=float, default=None, help="exit after N seconds")
    args, _ = parser.parse_known_args(argv)

    manager = ServerManager(on_log=_out)
    stop_flag = Path(args.stop_flag) if args.stop_flag else manager.data_dir / "stop.flag"
    manager.start()

    deadline = time.monotonic() + args.quit_after if args.quit_after else None
    last_line = ""
    exit_code = 0
    try:
        while True:
            status = manager.status()
            components = status["components"]
            line = (
                f"[{status['state']}] {status['message']} "
                f"pg={components['postgres']} ctrl={components['controller']} relay={components['relay']}"
            )
            if line != last_line:
                _out(line)
                last_line = line
            if status["state"] == "error":
                exit_code = 2
                break
            if stop_flag.exists():
                _out("stop flag seen; shutting down")
                break
            if deadline is not None and time.monotonic() > deadline:
                _out("quit-after reached")
                break
            time.sleep(0.5)
    finally:
        manager.stop()
    return exit_code


if __name__ == "__main__":
    sys.exit(main())