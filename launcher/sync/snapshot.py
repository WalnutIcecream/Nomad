from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

from shared.protocol.models import VersionMetadata

from launcher.controller import ControllerClient
from launcher.storage.base import WorldStorage
from launcher.storage.archives import (
    create_deterministic_archive,
    extract_archive_safely,
    remove_file,
)

logger = logging.getLogger(__name__)


def create_snapshot(
    storage: WorldStorage,
    world_dir: Path,
    minecraft_version: str,
    created_by: UUID,
    base_version: int | None,
) -> VersionMetadata:
    """Capture a snapshot of a stopped world directory.

    The world must be fully flushed and the server stopped before calling this.
    The snapshot becomes ``base_version + 1`` and is stored via the given
    WorldStorage backend (local dir or git).
    """
    latest = storage.get_latest_version()
    if base_version is not None and latest is not None and latest.version != base_version:
        raise ValueError(
            f"version conflict: local base is v{base_version}, storage is v{latest.version}"
        )

    version = (latest.version + 1) if latest else 1
    metadata = VersionMetadata(
        version=version,
        created_at=datetime.now(timezone.utc),
        created_by=created_by,
        minecraft_version=minecraft_version,
        storage_key=f"v{version}",
    )
    stored = storage.push_snapshot(version, world_dir, metadata)
    logger.info("created snapshot v%d from %s", version, world_dir)
    return stored


def restore_snapshot(
    storage: WorldStorage,
    world_dir: Path,
    version: int,
) -> VersionMetadata:
    """Restore a snapshot into a world directory, replacing existing content."""
    versions = storage.list_versions()
    target = next((m for m in versions if m.version == version), None)
    if target is None:
        raise FileNotFoundError(f"version not found: v{version}")

    if world_dir.exists():
        logger.warning("removing existing world directory %s before restore", world_dir)
        _remove_tree(world_dir)

    storage.pull_snapshot(target.storage_key, world_dir)
    logger.info("restored snapshot v%d into %s", version, world_dir)
    return target


def _remove_tree(path: Path) -> None:
    import shutil

    shutil.rmtree(path)


def pull_latest_world(client: ControllerClient, world_id: UUID, dest_dir: Path) -> int:
    """Download the latest cloud snapshot and extract it into dest_dir.

    Returns the restored version number, or 0 if the cloud has no versions yet.
    """
    status = client.get_world_status(world_id)
    latest = status.get("latest_version")
    if latest is None:
        dest_dir.mkdir(parents=True, exist_ok=True)
        return 0

    version = int(latest)
    archive = _download_archive(client, world_id, version)
    try:
        if dest_dir.exists():
            _remove_tree(dest_dir)
        dest_dir.mkdir(parents=True, exist_ok=True)
        extract_archive_safely(archive, dest_dir)
    finally:
        remove_file(archive)
    logger.info("pulled world v%d into %s", version, dest_dir)
    return version


def _download_archive(client: ControllerClient, world_id: UUID, version: int) -> Path:
    import tempfile

    fd, tmp_path = tempfile.mkstemp(suffix=".tar.gz")
    os.close(fd)
    dest = Path(tmp_path)
    client.download_version(world_id, version, dest)
    return dest


def push_world_snapshot(
    client: ControllerClient,
    world_id: UUID,
    lease_id: UUID,
    world_dir: Path,
    base_version: int,
    minecraft_version: str,
) -> int:
    """Two-phase upload of a stopped world's snapshot.

    Returns the new version number. Raises ControllerError on conflict or an
    invalid lease; the cloud latest is untouched on any failure.
    """
    prepared = client.snapshot_prepare(world_id, lease_id, base_version)
    upload_id = UUID(prepared["upload_id"])

    archive = create_deterministic_archive(world_dir)
    try:
        client.upload_snapshot(world_id, upload_id, archive)
    finally:
        remove_file(archive)

    completed = client.snapshot_complete(
        world_id, lease_id, upload_id, minecraft_version=minecraft_version
    )
    logger.info("uploaded world snapshot v%d", completed["version_number"])
    return int(completed["version_number"])
