"""Resumable, checksum-aware HTTP downloads with progress reporting.

Used for the JRE, the Minecraft server jar and any other large fetch, so a
dropped connection resumes instead of starting over and callers can show
progress.
"""

from __future__ import annotations

import hashlib
import logging
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path

logger = logging.getLogger(__name__)

# (bytes_done, total_bytes_or_None)
ProgressFn = Callable[[int, int | None], None]

_CHUNK = 1 << 16


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def download(
    url: str,
    dest: Path,
    *,
    on_progress: ProgressFn | None = None,
    expected_sha256: str | None = None,
    timeout: float = 60,
) -> Path:
    """Download ``url`` to ``dest``, resuming a partial file when possible.

    * If ``dest`` exists and its ``expected_sha256`` matches, it is returned as
      is; without a checksum the URL is always (re)fetched.
    * A leftover ``<dest>.part`` is resumed with an HTTP ``Range`` request.
    * When ``expected_sha256`` is given, the finished bytes are verified and a
      mismatch deletes the partial and raises rather than leaving a bad file.
    """
    dest = Path(dest)
    if dest.exists() and expected_sha256 is not None and sha256_file(dest) == expected_sha256:
        logger.debug("download skipped, %s already present", dest)
        return dest

    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    resume_from = part.stat().st_size if part.exists() else 0

    headers = {"Range": f"bytes={resume_from}-"} if resume_from else {}
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            if resume_from and getattr(response, "status", 200) != 206:
                resume_from = 0  # server ignored the range; restart cleanly
            total: int | None = None
            length = response.headers.get("Content-Length")
            if length is not None and length.isdigit():
                total = resume_from + int(length)
            done = resume_from
            with part.open("ab" if resume_from else "wb") as handle:
                while True:
                    block = response.read(_CHUNK)
                    if not block:
                        break
                    handle.write(block)
                    done += len(block)
                    if on_progress is not None:
                        on_progress(done, total)
    except urllib.error.HTTPError as exc:
        if exc.code != 416:  # 416 = the partial we hold is already the whole file
            raise

    if expected_sha256 is not None and sha256_file(part) != expected_sha256:
        part.unlink(missing_ok=True)
        raise ValueError(f"checksum mismatch for {url}")

    part.replace(dest)
    return dest
