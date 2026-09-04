"""Local registry of worlds this machine knows about.

A world is identified by a UUID that doubles as the R2 object prefix
(``worlds/<id>/lease.json`` + ``worlds/<id>/world.tar.gz``). The registry is
just the launcher's address book — anyone who shares the same world id and has
bucket access is playing the same world. No accounts, no server-side state.
"""

from __future__ import annotations

import json
import os
import tempfile
import uuid
from pathlib import Path


def new_world_id() -> str:
    return str(uuid.uuid4())


def _default_registry() -> dict[str, dict]:
    return {"worlds": {}}


class WorldRegistry:
    """Persistent JSON file: world id -> {name, minecraft_version}."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> dict[str, dict]:
        if not self.path.exists():
            return _default_registry()
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return _default_registry()
        if not isinstance(data, dict) or not isinstance(data.get("worlds"), dict):
            return _default_registry()
        return data

    def list_worlds(self) -> list[dict]:
        registry = self.load()
        worlds = []
        for world_id, meta in registry["worlds"].items():
            worlds.append(
                {
                    "id": world_id,
                    "name": meta.get("name", world_id),
                    "minecraft_version": meta.get("minecraft_version", "1.21.1"),
                }
            )
        return sorted(worlds, key=lambda w: w["name"].lower())

    def get(self, world_id: str) -> dict | None:
        meta = self.load()["worlds"].get(str(world_id))
        if meta is None:
            return None
        return {"id": str(world_id), **meta}

    def add(self, name: str, minecraft_version: str) -> dict:
        world_id = new_world_id()
        registry = self.load()
        registry["worlds"][world_id] = {
            "name": name,
            "minecraft_version": minecraft_version,
        }
        self._save(registry)
        return {"id": world_id, "name": name, "minecraft_version": minecraft_version}

    def add_with_id(self, world_id: str, name: str, minecraft_version: str) -> dict:
        """Register an existing shared world (a friend gave you the id)."""
        registry = self.load()
        registry["worlds"][str(world_id)] = {
            "name": name,
            "minecraft_version": minecraft_version,
        }
        self._save(registry)
        return {"id": str(world_id), "name": name, "minecraft_version": minecraft_version}

    def rename(self, world_id: str, name: str) -> None:
        registry = self.load()
        if str(world_id) in registry["worlds"]:
            registry["worlds"][str(world_id)]["name"] = name
            self._save(registry)

    def remove(self, world_id: str) -> None:
        registry = self.load()
        registry["worlds"].pop(str(world_id), None)
        self._save(registry)

    def _save(self, data: dict) -> None:
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
