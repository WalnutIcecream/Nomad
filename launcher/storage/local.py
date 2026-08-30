"""Filesystem-backed WorldStorage.

Layout::

    <root>/
      v1/
        world.tar.gz   # deterministic archive
        version.json   # manifest with sha256
      v2/
        ...

Versions are immutable: pushing an existing version number raises
``FileExistsError``, restoring a missing one raises ``FileNotFoundError``,
and a corrupted archive is rejected by verifying its sha256 against the
manifest before extraction.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from shared.protocol.models import VersionMetadata

from launcher.storage.archives import (
    compute_sha256,
    create_deterministic_archive,
    extract_archive_safely,
    read_version_manifest,
    remove_file,
    write_version_manifest,
)


def _version_number(name: str) -> int | None:
    if name.startswith("v") and name[1:].isdigit():
        return int(name[1:])
    return None


class LocalStorage:
    """Versioned directories on disk, one ``v<N>`` folder per snapshot."""

    def __init__(self, root: Path) -> None:
        self.storage_dir = root

    # --- internals -----------------------------------------------------

    def _version_dir(self, version: int) -> Path:
        return self.storage_dir / f"v{version}"

    def _archive_path(self, version: int) -> Path:
        return self._version_dir(version) / "world.tar.gz"

    def _manifest_path(self, version: int) -> Path:
        return self._version_dir(version) / "version.json"

    # --- WorldStorage --------------------------------------------------

    def list_versions(self) -> list[VersionMetadata]:
        if not self.storage_dir.is_dir():
            return []
        versions: list[tuple[int, VersionMetadata]] = []
        for child in self.storage_dir.iterdir():
            number = _version_number(child.name)
            if number is None or not child.is_dir():
                continue
            manifest = self._manifest_path(number)
            if manifest.is_file():
                versions.append((number, read_version_manifest(manifest)))
        versions.sort(key=lambda pair: pair[0])
        return [meta for _, meta in versions]

    def get_latest_version(self) -> VersionMetadata | None:
        versions = self.list_versions()
        return versions[-1] if versions else None

    def push_snapshot(
        self, version: int, src_dir: Path, meta: VersionMetadata
    ) -> VersionMetadata:
        version_dir = self._version_dir(version)
        if version_dir.exists():
            raise FileExistsError(f"snapshot already exists: v{version}")
        version_dir.mkdir(parents=True, exist_ok=False)

        archive = create_deterministic_archive(src_dir)
        target = self._archive_path(version)
        try:
            shutil.move(str(archive), str(target))
        finally:
            remove_file(archive)

        meta = meta.model_copy(
            update={"storage_key": f"v{version}", "sha256": compute_sha256(target)}
        )
        write_version_manifest(meta, self._manifest_path(version))
        return meta

    def pull_snapshot(self, storage_key: str, dest_dir: Path) -> None:
        version_dir = self.storage_dir / storage_key
        manifest = version_dir / "version.json"
        archive = version_dir / "world.tar.gz"
        if not manifest.is_file():
            raise FileNotFoundError(f"version not found: {storage_key}")

        metadata = read_version_manifest(manifest)
        actual = compute_sha256(archive)
        if metadata.sha256 and actual != metadata.sha256:
            raise OSError(
                f"checksum mismatch for {storage_key}: expected {metadata.sha256}, got {actual}"
            )
        extract_archive_safely(archive, dest_dir)

    def restore_version(self, version: int, dest_dir: Path) -> None:
        metadata = next(
            (m for m in self.list_versions() if m.version == version), None
        )
        if metadata is None:
            raise FileNotFoundError(f"version not found: v{version}")
        self.pull_snapshot(metadata.storage_key, dest_dir)