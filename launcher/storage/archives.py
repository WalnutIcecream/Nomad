"""Deterministic snapshot archives and safe extraction.

The archive contract used by every storage backend:

* ``world.tar.gz`` — a deterministic gzip tar of the world directory's
  contents (top-level entries, so extraction into an empty target yields the
  world directly). Deterministic means the same directory always produces
  byte-identical output: entries are walked in sorted order, ownership and
  timestamps are zeroed, and the gzip header has a fixed mtime.
* ``version.json`` — the ``VersionMetadata`` manifest, including the sha256
  of the archive.

Extraction is traversal-safe: path components that would escape the target
directory raise, and symlinks/hard links are never created.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import tarfile
import tempfile
import time
from pathlib import Path

from shared.protocol.models import VersionMetadata

_ARCHIVE_PREFIX = "nomad-archive-"
_CHUNK_SIZE = 1024 * 1024


def remove_file(path: Path, *, attempts: int = 10, delay: float = 0.1) -> None:
    """Delete a file, briefly retrying on Windows file-lock races.

    Antivirus/scanner processes briefly hold freshly-written archives, which
    otherwise turns a deterministic unlink into a flaky PermissionError.
    """
    for attempt in range(attempts):
        try:
            path.unlink(missing_ok=True)
            return
        except PermissionError:
            if attempt == attempts - 1:
                raise
            time.sleep(delay)


def compute_sha256(path: Path) -> str:
    """SHA-256 hex digest of a file on disk."""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(_CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()


def create_deterministic_archive(src_dir: Path) -> Path:
    """Archive ``src_dir``'s contents into a deterministic temporary tar.gz.

    The transient ``nomad.pid`` stop marker never leaves the local machine.
    Returns the path to the finished archive (caller owns cleanup).
    """
    if not src_dir.is_dir():
        raise FileNotFoundError(f"world directory not found: {src_dir}")

    fd, tmp_name = tempfile.mkstemp(prefix=_ARCHIVE_PREFIX, suffix=".tar.gz")
    os.close(fd)
    tmp = Path(tmp_name)
    try:
        with tarfile.open(tmp, "w:gz", format=tarfile.PAX_FORMAT, compresslevel=9) as tar:
            for rel in _walk_deterministic(src_dir):
                full = src_dir / rel
                info = tar.gettarinfo(str(full), arcname=rel.as_posix())
                info.uid = 0
                info.gid = 0
                info.uname = ""
                info.gname = ""
                info.mtime = 0
                info.pax_headers = {}
                if info.isfile():
                    with open(full, "rb") as handle:
                        tar.addfile(info, handle)
                elif info.isdir():
                    tar.addfile(info)
    except BaseException:
        remove_file(tmp)
        raise
    return tmp


def _walk_deterministic(src_dir: Path) -> list[Path]:
    result: list[Path] = []

    def walk(current: Path) -> None:
        for entry in sorted(current.iterdir(), key=lambda p: p.name):
            if entry.name == "nomad.pid":
                continue
            result.append(entry.relative_to(src_dir))
            if entry.is_dir():
                walk(entry)

    walk(src_dir)
    return result


def extract_archive_safely(archive: Path, dest_dir: Path) -> None:
    """Extract a snapshot archive into ``dest_dir``.

    Any member that would escape ``dest_dir`` raises ``ValueError``; symbolic
    and hard links are skipped. Malformed archives propagate their read error.
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    root = os.path.abspath(str(dest_dir))
    with tarfile.open(str(archive), "r:gz") as tar:
        for member in tar.getmembers():
            if member.issym() or member.islnk():
                continue
            target = _safe_target(root, member.name)
            if member.isdir():
                os.makedirs(target, exist_ok=True)
                continue
            if not member.isfile():
                continue
            os.makedirs(os.path.dirname(target), exist_ok=True)
            source = tar.extractfile(member)
            if source is None:
                raise OSError(f"cannot read archive member: {member.name}")
            with source, open(target, "wb") as out:
                shutil.copyfileobj(source, out)


def _safe_target(root: str, name: str) -> str:
    if os.path.isabs(name):
        target = os.path.abspath(name)
    else:
        target = os.path.abspath(os.path.join(root, name))
    if not (target == root or target.startswith(root + os.sep)):
        raise ValueError(f"unsafe archive path: {name!r}")
    return target


def write_version_manifest(metadata: VersionMetadata, dest: Path) -> None:
    """Persist a version manifest next to its archive."""
    dest.write_text(metadata.model_dump_json(), encoding="utf-8")


def parse_version_manifest(raw: str) -> VersionMetadata:
    """Parse a version manifest from its JSON text (e.g. ``git show v1:version.json``)."""
    return VersionMetadata.model_validate_json(raw)


def read_version_manifest(manifest: Path) -> VersionMetadata:
    """Load a version manifest from disk."""
    return parse_version_manifest(manifest.read_text(encoding="utf-8"))