from __future__ import annotations

import threading
import time
from pathlib import Path

STOP_FILE_NAME = "nomad.stop"


class StopFileWatcher:
    """Cross-platform stop signaling via a marker file.

    ``nomad stop`` cannot rely on POSIX signals on Windows (CTRL_C_EVENT only
    reaches processes sharing a console), so the stop command writes a marker
    file instead. The running launcher polls for it and shuts down gracefully.
    """

    def __init__(self, world_dir: Path, interval: float = 0.3) -> None:
        self.path = world_dir / STOP_FILE_NAME
        self.interval = interval
        self.stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> "StopFileWatcher":
        """Clear any stale marker and begin watching."""
        self.path.unlink(missing_ok=True)
        self._thread = threading.Thread(target=self._watch, daemon=True)
        self._thread.start()
        return self

    def _watch(self) -> None:
        while not self.stop_event.is_set():
            if self.path.exists():
                self.stop_event.set()
                break
            time.sleep(self.interval)

    def request_stop(self) -> None:
        """Called by ``nomad stop``: write the marker the watcher polls."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.touch()

    def cleanup(self) -> None:
        self.path.unlink(missing_ok=True)
