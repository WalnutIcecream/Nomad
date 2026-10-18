from __future__ import annotations

import io
import tarfile
from pathlib import Path

import pytest

from launcher.archive import ArchiveError, extract_tar


def _write_tar(path: Path, members: list[tuple[str, bytes | None, str]]) -> Path:
    """Build a .tar.gz; each member is (name, data_or_None_for_dir, type)."""
    with tarfile.open(path, "w:gz") as tar:
        for name, data, kind in members:
            info = tarfile.TarInfo(name)
            if kind == "dir":
                info.type = tarfile.DIRTYPE
                tar.addfile(info)
            elif kind == "symlink":
                info.type = tarfile.SYMTYPE
                info.linkname = "/etc/passwd"
                tar.addfile(info)
            else:
                assert data is not None
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))
    return path


def test_extracts_a_normal_archive(tmp_path: Path) -> None:
    archive = _write_tar(
        tmp_path / "w.tar.gz",
        [
            ("level.dat", b"data", "file"),
            ("region", None, "dir"),
            ("region/r.0.0.mca", b"x", "file"),
        ],
    )
    dest = tmp_path / "out"
    count = extract_tar(archive, dest)

    assert (dest / "level.dat").read_bytes() == b"data"
    assert (dest / "region" / "r.0.0.mca").read_bytes() == b"x"
    assert count == 3


def test_rejects_path_escape(tmp_path: Path) -> None:
    archive = _write_tar(tmp_path / "evil.tar.gz", [("../escape", b"bad", "file")])
    with pytest.raises(ArchiveError, match="unsafe"):
        extract_tar(archive, tmp_path / "out")


def test_rejects_too_many_entries(tmp_path: Path) -> None:
    archive = _write_tar(
        tmp_path / "many.tar.gz",
        [("a", b"1", "file"), ("b", b"2", "file"), ("c", b"3", "file")],
    )
    with pytest.raises(ArchiveError, match="entries"):
        extract_tar(archive, tmp_path / "out", max_entries=2)


def test_rejects_oversized_total(tmp_path: Path) -> None:
    archive = _write_tar(
        tmp_path / "big.tar.gz",
        [("a", b"x" * 100, "file"), ("b", b"y" * 100, "file")],
    )
    with pytest.raises(ArchiveError, match="exceeds"):
        extract_tar(archive, tmp_path / "out", max_bytes=150)


def test_rejects_oversized_single_file(tmp_path: Path) -> None:
    archive = _write_tar(tmp_path / "one.tar.gz", [("a", b"x" * 100, "file")])
    with pytest.raises(ArchiveError, match="too large"):
        extract_tar(archive, tmp_path / "out", max_file_bytes=50)


def test_skips_symlinks(tmp_path: Path) -> None:
    archive = _write_tar(
        tmp_path / "link.tar.gz",
        [("real.txt", b"ok", "file"), ("link", None, "symlink")],
    )
    dest = tmp_path / "out"
    extract_tar(archive, dest)
    assert (dest / "real.txt").exists()
    assert not (dest / "link").exists()
