"""Provision a Java runtime when the machine has none.

Minecraft 1.21.x needs Java 21. Rather than sending the user off to install one,
Nomad downloads a Temurin JRE 21 into the data directory (once, cached) and uses
it — so a fresh machine can host with no manual setup.
"""

from __future__ import annotations

import logging
import os
import platform
import re
import shutil
import subprocess
import tarfile
import zipfile
from pathlib import Path

from launcher.download import ProgressFn, download

logger = logging.getLogger(__name__)

_JAVA_MAJOR = 21
_URL = "https://api.adoptium.net/v3/binary/latest/{v}/ga/{os}/{arch}/jre/hotspot/normal/eclipse"

# Our own binary inside a provisioned runtime (both layouts expose bin/).
_JAVA_EXE = "java.exe" if os.name == "nt" else "java"


def _platform() -> tuple[str, str]:
    if os.name == "nt":
        os_name = "windows"
    elif platform.system() == "Darwin":
        os_name = "mac"
    else:
        os_name = "linux"
    machine = platform.machine().lower()
    arch = "aarch64" if machine in ("aarch64", "arm64") else "x64"
    return os_name, arch


def _archive_suffix(os_name: str) -> str:
    return ".zip" if os_name == "windows" else ".tar.gz"


def java_in(directory: Path) -> Path:
    return Path(directory) / "bin" / _JAVA_EXE


def runtime_dir(settings) -> Path:
    return Path(settings.data_dir) / "runtime" / f"java-{_JAVA_MAJOR}"


def provisioned_java(settings) -> Path | None:
    """The Java binary Nomad provisioned earlier, if it is present."""
    candidate = java_in(runtime_dir(settings))
    return candidate if candidate.exists() else None


def java_major(java_path: str) -> int | None:
    """Major version of a Java executable, or None if it cannot be resolved."""
    if not isinstance(java_path, str) or not java_path:
        return None
    resolved = shutil.which(java_path)
    if resolved is None and java_path and Path(java_path).exists():
        resolved = java_path
    if resolved is None:
        return None
    try:
        result = subprocess.run([resolved, "-version"], capture_output=True, text=True, check=False)
    except OSError:
        return None
    output = result.stderr or result.stdout
    match = re.search(r'version "(\d+)', output)
    return int(match.group(1)) if match else None


def ensure_java(settings, on_progress: ProgressFn | None = None) -> Path:
    """Return a path to a Java 21 binary, downloading Temurin once if needed."""
    existing = provisioned_java(settings)
    if existing is not None:
        return existing

    os_name, arch = _platform()
    dest = runtime_dir(settings)
    archive = (
        Path(settings.data_dir)
        / "runtime"
        / (f"temurin-{_JAVA_MAJOR}-{os_name}-{arch}{_archive_suffix(os_name)}")
    )
    url = _URL.format(v=_JAVA_MAJOR, os=os_name, arch=arch)

    logger.info("no Java %s found; downloading a runtime once from %s", _JAVA_MAJOR, url)
    download(url, archive, on_progress=on_progress)
    _extract(archive, dest)
    archive.unlink(missing_ok=True)

    binary = java_in(dest)
    if not binary.exists():
        raise RuntimeError(f"Java archive did not contain {binary}")
    return binary


# --- archive helpers -------------------------------------------------------


def _top_dir(names: list[str]) -> str | None:
    tops = {name.split("/", 1)[0] for name in names if "/" in name}
    return tops.pop() if len(tops) == 1 else None


def _extract(archive: Path, dest: Path) -> None:
    """Extract ``archive`` into ``dest``, hoisting its single top-level dir."""
    dest.mkdir(parents=True, exist_ok=True)
    if archive.name.endswith(".zip"):
        with zipfile.ZipFile(archive) as zf:
            names = zf.namelist()
            top = _top_dir(names)
            for name in names:
                rel = _relative(name, top)
                if rel is None:
                    continue
                target = dest / rel
                if name.endswith("/"):
                    target.mkdir(parents=True, exist_ok=True)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with zf.open(name) as src, target.open("wb") as out:
                        shutil.copyfileobj(src, out)
    else:
        with tarfile.open(archive, "r:gz") as tf:
            members = tf.getmembers()
            top = _top_dir([m.name for m in members])
            for member in members:
                rel = _relative(member.name, top)
                if rel is None:
                    continue
                target = dest / rel
                if member.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                elif member.isfile():
                    target.parent.mkdir(parents=True, exist_ok=True)
                    extracted = tf.extractfile(member)
                    if extracted is None:
                        continue
                    with extracted, target.open("wb") as out:
                        shutil.copyfileobj(extracted, out)


def _relative(name: str, top: str | None) -> str | None:
    """Path relative to the hoisted top dir, or None to skip the entry."""
    if top is None:
        return name or None
    if not name.startswith(top):
        return None
    rel = name[len(top) :].lstrip("/")
    return rel or None
