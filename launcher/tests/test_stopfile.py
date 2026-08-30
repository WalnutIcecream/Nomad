from __future__ import annotations

import time
from pathlib import Path

from launcher.stopfile import StopFileWatcher


def test_watcher_detects_marker(tmp_path: Path) -> None:
    world_dir = tmp_path / "world"
    world_dir.mkdir()
    watcher = StopFileWatcher(world_dir, interval=0.05).start()
    assert not watcher.stop_event.is_set()

    watcher.request_stop()
    deadline = time.time() + 5
    while not watcher.stop_event.is_set() and time.time() < deadline:
        time.sleep(0.05)
    assert watcher.stop_event.is_set()


def test_watcher_cleans_marker(tmp_path: Path) -> None:
    world_dir = tmp_path / "world"
    world_dir.mkdir()
    watcher = StopFileWatcher(world_dir, interval=0.05).start()
    watcher.request_stop()
    watcher.cleanup()
    assert not watcher.path.exists()


def test_watcher_clears_stale_marker_on_start(tmp_path: Path) -> None:
    world_dir = tmp_path / "world"
    world_dir.mkdir()
    stale = world_dir / StopFileWatcher(world_dir).path.name
    stale.touch()
    watcher = StopFileWatcher(world_dir, interval=0.05).start()
    assert not watcher.path.exists()
    watcher.cleanup()
