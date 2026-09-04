from __future__ import annotations

from pathlib import Path


def make_world_folder(root: Path, name: str = "New World") -> Path:
    """Create a minimal valid Minecraft world save layout under ``root``."""
    folder = root / name
    (folder / "region").mkdir(parents=True, exist_ok=True)
    (folder / "level.dat").write_bytes(b"mock-level-dat")
    (folder / "level.dat_old").write_bytes(b"mock-level-dat-old")
    (folder / "session.lock").touch()
    (folder / "region" / "r.0.0.mca").write_bytes(b"\x00" * 16)
    return folder


def make_cloud_world(root: Path) -> Path:
    """Create a Nomad world-dir layout (server-style, level under ``world/``)."""
    world = root / "world"
    (world / "region").mkdir(parents=True, exist_ok=True)
    (world / "level.dat").write_bytes(b"cloud-level")
    (world / "region" / "r.0.0.mca").write_bytes(b"cloud-region")
    return root
