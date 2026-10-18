"""Safe extraction of world archives.

A hostile or corrupt ``world.tar.gz`` must not be able to escape the target
directory or fill the disk, so extraction enforces three limits (entries, total
uncompressed bytes, and per-file bytes) and refuses symlinks and hardlinks. The
host agent pulls worlds uploaded by other players, so this is a trust boundary.
"""

from __future__ import annotations

import shutil
import tarfile
from pathlib import Path

DEFAULT_MAX_ENTRIES = 200_000
DEFAULT_MAX_BYTES = 20 * 1024**3  # 20 GiB uncompressed
DEFAULT_MAX_FILE_BYTES = 8 * 1024**3


class ArchiveError(Exception):
    """Raised when an archive is unsafe or exceeds the configured limits."""


def extract_tar(
    archive: Path,
    dest: Path,
    *,
    max_entries: int = DEFAULT_MAX_ENTRIES,
    max_bytes: int = DEFAULT_MAX_BYTES,
    max_file_bytes: int = DEFAULT_MAX_FILE_BYTES,
) -> int:
    """Extract a ``.tar.gz`` into ``dest``. Returns the number of entries written.

    Raises :class:`ArchiveError` on a path escape, an oversized archive, or a
    member that would exceed a limit. Symlinks and hardlinks are skipped.
    """
    archive = Path(archive)
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    root = dest.resolve()

    entries = 0
    total = 0
    with tarfile.open(archive, "r:gz") as tar:
        for member in tar.getmembers():
            if member.issym() or member.islnk():
                continue
            entries += 1
            if entries > max_entries:
                raise ArchiveError(f"archive has more than {max_entries} entries")

            target = (dest / member.name).resolve()
            if target != root and root not in target.parents:
                raise ArchiveError(f"unsafe archive member: {member.name}")

            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
            elif member.isfile():
                if member.size > max_file_bytes:
                    raise ArchiveError(
                        f"archive member too large: {member.name} ({member.size} bytes)"
                    )
                total += member.size
                if total > max_bytes:
                    raise ArchiveError(f"archive exceeds {max_bytes} bytes uncompressed")
                target.parent.mkdir(parents=True, exist_ok=True)
                source = tar.extractfile(member)
                if source is None:
                    continue
                with source, target.open("wb") as out:
                    shutil.copyfileobj(source, out)
    return entries
