"""The storage abstraction every backend implements.

A snapshot is a deterministic ``world.tar.gz`` plus a ``version.json``
manifest recording the archive's sha256 and the version metadata. Backends
keep that on-disk contract so the sync layer treats local and git storage
identically.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable

from shared.protocol.models import VersionMetadata


@runtime_checkable
class WorldStorage(Protocol):
    """Opaque snapshot/blob persistence, backend-agnostic."""

    def list_versions(self) -> list[VersionMetadata]: ...

    def get_latest_version(self) -> VersionMetadata | None: ...

    def pull_snapshot(self, storage_key: str, dest_dir: Path) -> None: ...

    def push_snapshot(
        self, version: int, src_dir: Path, meta: VersionMetadata
    ) -> VersionMetadata: ...

    def restore_version(self, version: int, dest_dir: Path) -> None: ...