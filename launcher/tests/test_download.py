from __future__ import annotations

import hashlib
import threading
from functools import partial
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path

import pytest

from launcher.download import download


class _QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *args) -> None:  # keep test output clean
        pass


@pytest.fixture
def server(tmp_path: Path):
    serve = tmp_path / "serve"
    serve.mkdir()
    handler = partial(_QuietHandler, directory=str(serve))
    httpd = HTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield serve, f"http://127.0.0.1:{httpd.server_address[1]}"
    finally:
        httpd.shutdown()
        thread.join(timeout=5)


def test_download_writes_file_and_reports_progress(server, tmp_path: Path) -> None:
    serve, base = server
    payload = b"x" * 200_000
    (serve / "blob.bin").write_bytes(payload)

    events: list[tuple[int, int | None]] = []
    dest = tmp_path / "out" / "blob.bin"
    download(f"{base}/blob.bin", dest, on_progress=lambda d, t: events.append((d, t)))

    assert dest.read_bytes() == payload
    assert events and events[-1][0] == len(payload)
    assert events[-1][1] == len(payload)


def test_download_checksum_mismatch_raises(server, tmp_path: Path) -> None:
    serve, base = server
    (serve / "blob.bin").write_bytes(b"hello")
    with pytest.raises(ValueError, match="checksum mismatch"):
        download(f"{base}/blob.bin", tmp_path / "b.bin", expected_sha256="0" * 64)
    assert not (tmp_path / "b.bin").exists()


def test_download_uses_cache_when_checksum_matches(server, tmp_path: Path) -> None:
    serve, base = server
    payload = b"cached-content"
    (serve / "c.bin").write_bytes(payload)
    digest = hashlib.sha256(payload).hexdigest()
    dest = tmp_path / "c.bin"

    download(f"{base}/c.bin", dest, expected_sha256=digest)
    (serve / "c.bin").write_bytes(b"different-on-the-server")  # must be ignored
    download(f"{base}/c.bin", dest, expected_sha256=digest)

    assert dest.read_bytes() == payload


def test_download_recovers_from_a_partial_file(server, tmp_path: Path) -> None:
    serve, base = server
    payload = bytes(i % 256 for i in range(100_000))
    (serve / "r.bin").write_bytes(payload)

    dest = tmp_path / "r.bin"
    dest.with_name(dest.name + ".part").write_bytes(payload[:40_000])  # interrupted

    download(f"{base}/r.bin", dest)
    assert dest.read_bytes() == payload


def test_download_refetches_without_a_checksum(server, tmp_path: Path) -> None:
    serve, base = server
    (serve / "f.bin").write_bytes(b"new")
    dest = tmp_path / "f.bin"
    dest.write_bytes(b"stale")

    download(f"{base}/f.bin", dest)
    assert dest.read_bytes() == b"new"
