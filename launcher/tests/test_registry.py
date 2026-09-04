from __future__ import annotations

from pathlib import Path

from launcher.registry import WorldRegistry


def test_registry_add_and_list(tmp_path: Path) -> None:
    registry = WorldRegistry(tmp_path / "registry.json")
    world = registry.add("Survival", "1.21.1")
    assert world["name"] == "Survival"
    assert world["minecraft_version"] == "1.21.1"

    worlds = registry.list_worlds()
    assert len(worlds) == 1
    assert worlds[0]["name"] == "Survival"


def test_registry_persists_and_joins_existing(tmp_path: Path) -> None:
    registry = WorldRegistry(tmp_path / "registry.json")
    world = registry.add("Survival", "1.21.1")
    # A second instance (e.g. another machine) sees the same file.
    again = WorldRegistry(tmp_path / "registry.json")
    assert again.get(world["id"]) is not None

    # Joining a friend's world by id.
    joined = registry.add_with_id("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", "Shared", "1.20.4")
    assert joined["id"] == "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    assert registry.get("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")["name"] == "Shared"


def test_registry_remove_and_rename(tmp_path: Path) -> None:
    registry = WorldRegistry(tmp_path / "registry.json")
    world = registry.add("Temp", "1.21.1")
    registry.rename(world["id"], "Renamed")
    assert registry.get(world["id"])["name"] == "Renamed"
    registry.remove(world["id"])
    assert registry.get(world["id"]) is None


def test_registry_corrupt_file(tmp_path: Path) -> None:
    path = tmp_path / "registry.json"
    path.write_text("{not json")
    registry = WorldRegistry(path)
    assert registry.list_worlds() == []
