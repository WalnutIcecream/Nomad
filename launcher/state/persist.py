from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

from shared.protocol.enums import LauncherState

DEFAULT_STATE: dict[str, Any] = {
    "state": LauncherState.IDLE.value,
    "world_id": None,
    "lease_id": None,
    "base_version": None,
    "local_world_dir": None,
    "updated_at": None,
}


class StateStore:
    """Persists launcher state so a crash mid-flow can be resumed or rolled back."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> dict[str, Any]:
        if not self.path.exists():
            return dict(DEFAULT_STATE)
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return dict(DEFAULT_STATE)
        return {**DEFAULT_STATE, **data}

    def save(self, data: dict[str, Any]) -> None:
        data["updated_at"] = datetime.now().isoformat()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(dir=self.path.parent, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(data, handle, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_path, self.path)
        except BaseException:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
            raise

    def clear(self) -> None:
        self.save(dict(DEFAULT_STATE))
