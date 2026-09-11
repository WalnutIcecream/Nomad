from __future__ import annotations

import json
import tarfile
from pathlib import Path

from launcher.manifest import (
    DEFAULT_MANIFEST_NAME,
    ServerManifest,
    create_archive,
    describe,
    is_included,
    iter_included,
    load_manifest,
    save_manifest,
)


def _make_server_dir(root: Path) -> Path:
    server = root / "srv"
    (server / "Worlds").mkdir(parents=True)
    (server / "Worlds" / "world1.wld").write_bytes(b"world-data")
    (server / "Worlds" / "world1.wld.bak").write_bytes(b"backup")
    (server / "config.json").write_text("{}")
    (server / "server.log").write_text("noise")
    (server / "logs").mkdir()
    (server / "logs" / "today.log").write_text("noise")
    (server / "core").write_bytes(b"crashdump")
    return server


def test_include_empty_syncs_everything_except_excludes(tmp_path: Path) -> None:
    server = _make_server_dir(tmp_path)
    manifest = ServerManifest(include=[], exclude=["*.log", "logs/", "core"])
    rels = {p.as_posix() for p in iter_included(server, manifest)}
    assert "Worlds/world1.wld" in rels
    assert "config.json" in rels
    assert "server.log" not in rels
    assert "logs/today.log" not in rels
    assert "core" not in rels


def test_directory_pattern_includes_subtree(tmp_path: Path) -> None:
    server = _make_server_dir(tmp_path)
    manifest = ServerManifest(include=["Worlds"], exclude=[])
    rels = {p.as_posix() for p in iter_included(server, manifest)}
    assert "Worlds/world1.wld" in rels
    assert "Worlds/world1.wld.bak" in rels
    assert "config.json" not in rels


def test_glob_pattern_matches_any_depth(tmp_path: Path) -> None:
    server = _make_server_dir(tmp_path)
    manifest = ServerManifest(include=["*.wld"], exclude=[])
    rels = {p.as_posix() for p in iter_included(server, manifest)}
    assert "Worlds/world1.wld" in rels
    assert "Worlds/world1.wld.bak" not in rels


def test_exclude_beats_include(tmp_path: Path) -> None:
    manifest = ServerManifest(include=["Worlds"], exclude=["*.bak"])
    assert is_included(Path("Worlds/world1.wld"), manifest) is True
    assert is_included(Path("Worlds/world1.wld.bak"), manifest) is False


def test_manifest_always_included_and_artifacts_never(tmp_path: Path) -> None:
    manifest = ServerManifest(include=["config.json"], exclude=[])
    # The pointer file travels with the world.
    assert is_included(Path(DEFAULT_MANIFEST_NAME), manifest) is True
    # Launcher runtime artifacts never do.
    assert is_included(Path("nomad.pid"), manifest) is False
    assert is_included(Path("upload.tar.gz"), manifest) is False


def test_round_trip_save_and_load(tmp_path: Path) -> None:
    server = tmp_path / "srv"
    server.mkdir()
    manifest = ServerManifest(
        name="Terraria",
        include=["Worlds/", "config.json"],
        exclude=["*.log"],
        server_command=["./TerrariaServer", "-config", "config.json"],
        stop_command="exit",
        port=7777,
    )
    save_manifest(server, manifest)

    loaded = load_manifest(server)
    assert loaded is not None
    assert loaded.name == "Terraria"
    assert loaded.include == ["Worlds/", "config.json"]
    assert loaded.exclude == ["*.log"]
    assert loaded.server_command == ["./TerrariaServer", "-config", "config.json"]
    assert loaded.stop_command == "exit"
    assert loaded.port == 7777
    assert loaded.is_generic is True


def test_load_missing_or_invalid_returns_none(tmp_path: Path) -> None:
    assert load_manifest(tmp_path) is None
    (tmp_path / DEFAULT_MANIFEST_NAME).write_text("{not json")
    assert load_manifest(tmp_path) is None


def test_create_archive_round_trip(tmp_path: Path) -> None:
    server = _make_server_dir(tmp_path)
    manifest = ServerManifest(include=["Worlds"], exclude=["*.bak"])
    archive = tmp_path / "out.tar.gz"
    count = create_archive(server, manifest, archive)
    assert count == 1  # only world1.wld (bak excluded)

    dest = tmp_path / "extract"
    dest.mkdir()
    with tarfile.open(archive) as tar:
        names = tar.getnames()
    assert names == ["Worlds/world1.wld"]


def test_describe_reports_counts(tmp_path: Path) -> None:
    server = _make_server_dir(tmp_path)
    manifest = ServerManifest(include=[], exclude=["*.log", "logs/", "core"])
    summary = describe(server, manifest)
    # Worlds/world1.wld, Worlds/world1.wld.bak, config.json
    assert summary.files == 3
    assert summary.excluded >= 3
    assert summary.bytes > 0


def test_process_runtime_selected_for_generic_manifest(tmp_path: Path) -> None:
    """A manifest with server.command makes the agent use ProcessRuntime."""
    from launcher.agent import HostAgent
    from launcher.minecraft.vanilla import VanillaMinecraftRuntime
    from launcher.process_runtime import ProcessRuntime

    manifest = ServerManifest(name="T", server_command=["./srv"])

    class _Agent(HostAgent):
        def __init__(self):  # bypass the real constructor
            self.runtime = None
            self.manifest = None

    agent = _Agent()
    agent.manifest = None
    assert isinstance(agent._select_runtime(), VanillaMinecraftRuntime)

    agent.manifest = manifest
    assert isinstance(agent._select_runtime(), ProcessRuntime)

    # An explicit runtime override always wins.
    override = VanillaMinecraftRuntime()
    agent.runtime = override
    assert agent._select_runtime() is override


def test_process_runtime_stop_uses_stop_command_or_terminate(tmp_path: Path) -> None:
    from launcher.process_runtime import ProcessRuntime

    called: dict = {}
    handle = type(
        "H",
        (),
        {
            "is_running": lambda self: True,
            "send_command": lambda self, cmd: called.update(command=cmd),
            "terminate": lambda self: called.update(terminated=True),
        },
    )()

    graceful = ProcessRuntime(["./srv"], stop_command="exit")
    graceful.stop(handle)
    assert called.get("command") == "exit"

    called.clear()
    forceful = ProcessRuntime(["./srv"])
    forceful.stop(handle)
    assert called.get("terminated") is True