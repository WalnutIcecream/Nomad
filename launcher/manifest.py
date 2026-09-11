"""Inclusion manifest: a pointer file that lists what to sync.

``nomad.json`` is the inclusion counterpart to ``.gitignore``: it names the
files and directories that make up a server's persistent state. This decouples
Nomad from any single game — the manifest says what to upload and, optionally,
how to launch the server, so any dedicated game server (Minecraft, Terraria,
Factorio, Valheim, ...) can be hosted the same way.

Semantics::

    {
      "version": 1,
      "name": "My Terraria Server",
      "include": ["Worlds/", "config.json", "players/*.plr"],
      "exclude": ["*.log", "logs/", "core"],
      "server": {
        "command": ["./TerrariaServer", "-config", "config.json"],
        "stop_command": "exit",
        "port": 7777
      }
    }

* ``include`` — glob patterns relative to the manifest's directory. A pattern
  naming a directory includes its whole subtree. Empty/absent means "everything".
* ``exclude`` — glob patterns removed from the included set. Exclude always wins.
* ``server.command`` — optional argv to launch the server with cwd = the synced
  directory. When absent, the built-in vanilla Minecraft runtime is used.
* ``server.stop_command`` — optional line written to the server's stdin for a
  graceful shutdown (e.g. ``stop``). When absent the process is terminated.

Glob matching uses :func:`fnmatch.fnmatchcase`, where ``*`` also crosses path
separators, so ``*.log`` matches at any depth and ``saves`` matches the whole
``saves`` subtree.
"""

from __future__ import annotations

import fnmatch
import json
import os
import tarfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

DEFAULT_MANIFEST_NAME = "nomad.json"

# Launcher runtime artifacts that never belong in a shared archive, regardless
# of what the manifest says.
_ALWAYS_EXCLUDE = frozenset({"nomad.pid", "nomad.stop", "upload.tar.gz"})


@dataclass
class ServerManifest:
    """Parsed ``nomad.json``. Absent fields fall back to Minecraft defaults."""

    version: int = 1
    name: str = "server"
    include: list[str] = field(default_factory=list)
    exclude: list[str] = field(default_factory=list)
    server_command: list[str] | None = None
    stop_command: str | None = None
    port: int | None = None

    @property
    def is_generic(self) -> bool:
        """True when the manifest declares its own launch command."""
        return bool(self.server_command)


def load_manifest(root: Path) -> ServerManifest | None:
    """Load ``nomad.json`` from ``root``, or return None when absent/invalid."""
    path = Path(root) / DEFAULT_MANIFEST_NAME
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    if not isinstance(data, dict):
        return None

    server = data.get("server") if isinstance(data.get("server"), dict) else {}
    command = server.get("command")
    if isinstance(command, str):
        command = [command]
    if not isinstance(command, list) or not all(isinstance(part, str) for part in command):
        command = None

    return ServerManifest(
        version=int(data.get("version", 1)),
        name=str(data.get("name", "server")),
        include=[str(p) for p in data.get("include", []) if isinstance(p, str)],
        exclude=[str(p) for p in data.get("exclude", []) if isinstance(p, str)],
        server_command=command,
        stop_command=server.get("stop_command") if isinstance(server.get("stop_command"), str) else None,
        port=int(server["port"]) if isinstance(server.get("port"), int) else None,
    )


def save_manifest(root: Path, manifest: ServerManifest) -> Path:
    """Write ``nomad.json`` into ``root`` and return its path."""
    payload: dict = {
        "version": manifest.version,
        "name": manifest.name,
        "include": manifest.include,
        "exclude": manifest.exclude,
    }
    if manifest.server_command or manifest.stop_command or manifest.port:
        payload["server"] = {
            "command": manifest.server_command,
            "stop_command": manifest.stop_command,
            "port": manifest.port,
        }
    path = Path(root) / DEFAULT_MANIFEST_NAME
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return path


def _matches(rel: str, patterns: list[str]) -> bool:
    """True if the POSIX-style relative path matches any pattern.

    A pattern naming a directory matches the whole subtree via ancestor checks,
    so ``saves`` matches ``saves/x.dat`` without needing ``saves/**``.
    """
    parts = rel.split("/")
    for raw in patterns:
        pattern = raw.strip().strip("/")
        if not pattern:
            continue
        if fnmatch.fnmatchcase(rel, pattern):
            return True
        for i in range(1, len(parts)):
            if fnmatch.fnmatchcase("/".join(parts[:i]), pattern):
                return True
    return False


def is_included(rel: Path, manifest: ServerManifest) -> bool:
    """Decide whether a relative path belongs in the archive."""
    posix = rel.as_posix()
    if rel.name in _ALWAYS_EXCLUDE:
        return False
    # The manifest travels with the world so the next host can boot the game.
    if posix == DEFAULT_MANIFEST_NAME:
        return True
    if _matches(posix, manifest.exclude):
        return False
    if manifest.include and not _matches(posix, manifest.include):
        return False
    return True


def iter_included(root: Path, manifest: ServerManifest) -> Iterator[Path]:
    """Yield relative paths of every file the manifest selects."""
    root = Path(root)
    for dirpath, _dirnames, filenames in os.walk(root):
        rel_dir = Path(dirpath).relative_to(root)
        for name in sorted(filenames):
            rel = rel_dir / name if str(rel_dir) != "." else Path(name)
            if is_included(rel, manifest):
                yield rel


def create_archive(root: Path, manifest: ServerManifest, dest: Path) -> int:
    """Archive the included files into ``dest``. Returns the file count."""
    root = Path(root)
    count = 0
    with tarfile.open(dest, "w:gz") as tar:
        for rel in iter_included(root, manifest):
            tar.add(root / rel, arcname=rel.as_posix())
            count += 1
    return count


@dataclass
class ManifestSummary:
    files: int
    bytes: int
    excluded: int
    sample: list[str]


def describe(root: Path, manifest: ServerManifest) -> ManifestSummary:
    """Summarise what the manifest would sync (for ``nomad manifest check``)."""
    root = Path(root)
    files = 0
    total = 0
    excluded = 0
    sample: list[str] = []
    for dirpath, _dirnames, filenames in os.walk(root):
        rel_dir = Path(dirpath).relative_to(root)
        for name in sorted(filenames):
            rel = rel_dir / name if str(rel_dir) != "." else Path(name)
            if is_included(rel, manifest):
                files += 1
                total += (root / rel).stat().st_size
                if len(sample) < 20:
                    sample.append(rel.as_posix())
            else:
                excluded += 1
    return ManifestSummary(files=files, bytes=total, excluded=excluded, sample=sample)