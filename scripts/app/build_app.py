"""Build a self-contained desktop app for Nomad.

Produces a portable ``dist/Nomad/`` folder (works on Windows / Linux / macOS):

    dist/Nomad/
        Nomad(.exe)          double-click launcher/gui
        nomad(.exe)          the command-line tool (renamed to ``nomad-cli``
                             on Windows because Windows cannot hold both
                             Nomad.exe and nomad.exe in the same folder)
        java/                embedded Temurin JRE 21 (downloaded once, cached)
        data/                runtime data (worlds, registry, settings)

The app is version-agnostic for Minecraft: it fetches the vanilla server.jar
for the configured ``NOMAD_MC_VERSION`` at runtime, so no jar is baked in. Only
the Java runtime is embedded.

Usage (from the repo root, with PyInstaller installed):

    python scripts/app/build_app.py                    # build + embed JRE
    python scripts/app/build_app.py --no-java          # skip the JRE download
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
CACHE = ROOT / "scripts" / "app" / ".cache"
WORK = ROOT / "build" / "app"
SPEC = ROOT / "scripts" / "app" / "spec"
DIST = ROOT / "dist"
OUT = DIST / "Nomad"

# Interpreters: prefer the repo venv, then the active python.
_PY = ROOT / ".venv" / "Scripts" / "python.exe"
if not _PY.exists():
    _PY = ROOT / ".venv" / "bin" / "python"
if not _PY.exists():
    _PY = Path(sys.executable)

_IS_WINDOWS = os.name == "nt"
JAVA_PLATFORM = "windows" if _IS_WINDOWS else ("mac" if sys.platform == "darwin" else "linux")
_JAVA_ARCH = "x64" if sys.maxsize > 2**32 else "x86_64"
JAVA_URL = os.environ.get(
    "NOMAD_JAVA_ZIP_URL",
    f"https://api.adoptium.net/v3/binary/latest/21/ga/{JAVA_PLATFORM}/{_JAVA_ARCH}/jre/hotspot/normal/eclipse",
)
JAVA_ZIP = CACHE / "temurin-jre-21.zip"

# Modules the CLI imports lazily (inside functions) that PyInstaller's static
# analysis cannot see; bundle them explicitly for both executables.
_HIDDEN = [
    "launcher.agent",
    "launcher.cloud",
    "launcher.registry",
    "launcher.config",
    "launcher.server_properties",
    "launcher.minecraft.vanilla",
    "launcher.minecraft.base",
    "launcher.minecraft.process",
    "launcher.sync.worldfolder",
    "launcher.storage",
    "launcher.storage.r2",
    "launcher.storage.git",
    "launcher.storage.vps",
]

# Extra data PySide6 needs that PyInstaller's hooks sometimes miss.
_COLLECT: dict[str, list[str]] = {}
for pkg in ("PySide6", "shiboken6"):
    _COLLECT.setdefault(pkg, [])

# --- helpers -------------------------------------------------------------

def _run(cmd: list[str], cwd: Path | None = None) -> None:
    print("+ " + " ".join(str(c) for c in cmd), flush=True)
    subprocess.run(cmd, cwd=cwd, check=True)


def _download(url: str, dest: Path) -> None:
    if dest.exists():
        print(f"using cached {dest}", flush=True)
        return
    CACHE.mkdir(parents=True, exist_ok=True)
    print(f"downloading {dest.name}…", flush=True)
    req = urllib.request.Request(url, headers={"User-Agent": "nomad-app-build/1.0"})
    with urllib.request.urlopen(req, timeout=600) as src, dest.open("wb") as dst:  # noqa: S310 - pinned
        shutil.copyfileobj(src, dst)
    print("downloaded.", flush=True)


def _extract_java(dest: Path) -> None:
    """Extract the JRE zip and hoist its single top-level dir into ``dest``."""
    if (dest / "bin" / ("java.exe" if _IS_WINDOWS else "java")).exists():
        print(f"bundle already has Java at {dest}.", flush=True)
        return
    with zipfile.ZipFile(JAVA_ZIP) as zf:
        names = zf.namelist()
        top = next(iter({n.split("/", 1)[0] for n in names if "/" in n}), None)
        if top is None:
            raise SystemExit("unexpected JRE archive layout")
        print(f"extracting {top}/ to {dest}…", flush=True)
        dest.mkdir(parents=True, exist_ok=True)
        for name in names:
            if not name.startswith(top + "/"):
                continue
            rel = name[len(top) + 1 :]
            target = dest / rel
            if name.endswith("/"):
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(name) as src, target.open("wb") as dst:
                    shutil.copyfileobj(src, dst)


def _pyi(name: str, script: Path, *, windowed: bool = False, collect: bool = False, onefile: bool = False) -> None:
    cmd = [
        str(_PY),
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--name",
        name,
        "--distpath",
        str(DIST),
        "--workpath",
        str(WORK),
        "--specpath",
        str(SPEC),
    ]
    if collect:
        for pkg in _COLLECT:
            cmd += ["--collect-all", pkg]
    for mod in _HIDDEN:
        cmd += ["--hidden-import", mod]
    if onefile:
        cmd.append("--onefile")
    if windowed:
        cmd.append("--windowed")
    cmd.append(str(script))
    _run(cmd)


def build_apps() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    # Name the cli differently on Windows (NOMAD vs nomad collision in one dir).
    cli_name = "nomad-cli" if _IS_WINDOWS else "nomad"
    # GUI onedir -> lands in dist/Nomad/ (our OUT). The onedir COLLECT step
    # clears OUT, so run it before moving the CLI in afterward.
    _pyi("Nomad", ROOT / "launcher" / "ui" / "__main__.py", windowed=True, collect=True)
    # CLI onefile -> lands directly in dist/, then moved into OUT.
    _pyi(cli_name, ROOT / "launcher" / "cli" / "__main__.py", onefile=True)
    shutil.move(str(DIST / ("nomad-cli.exe" if _IS_WINDOWS else "nomad")), str(OUT))


def assemble() -> None:
    (OUT / "java").mkdir(parents=True, exist_ok=True)
    (OUT / "data" / "logs").mkdir(parents=True, exist_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--no-java", action="store_true", help="skip downloading/embedding Java")
    args = parser.parse_args()

    for p in (DIST, WORK, SPEC):
        shutil.rmtree(p, ignore_errors=True)
    print(f"building app bundle: {OUT}", flush=True)

    if not args.no_java:
        _download(JAVA_URL, JAVA_ZIP)
    build_apps()
    if not args.no_java:
        _extract_java(OUT / "java")
    assemble()

    size_mb = sum(
        p.stat().st_size for p in OUT.rglob("*") if p.is_file()
    ) // (1024 * 1024)
    print(f"\napp ready at {OUT} (~{size_mb} MB)", flush=True)
    print("set R2 credentials via env vars or data/settings.json, then run Nomad(.exe).", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())