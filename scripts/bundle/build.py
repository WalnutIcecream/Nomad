"""Build the standalone ``dist/Nomad`` application bundle (Windows x64).

What this produces::

    dist/Nomad/
        Nomad.exe                  the hub — double-click to run everything
        _internal/                 bundled Python, controller, launcher, UI
        nomad-cli.exe              onefile CLI (snapshot list, host, …)
        nomad-controller.exe       onefile controller (for API-only use)
        nomad-relay.exe            onefile relay server
        postgresql/bin/…           embedded PostgreSQL (downloads on first run)
        java/                      embedded Java 21 runtime (Temurin JRE)
        data/                      runtime data, created on first run

Run from the repo root with the project venv::

    .venv\\Scripts\\python.exe scripts/bundle/build.py

The PostgreSQL binaries are cached in ``scripts/bundle/.cache`` after the
first download; delete that file to force a refresh.
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
SCRIPT_DIR = Path(__file__).resolve().parent
PY = ROOT / ".venv" / "Scripts" / "python.exe"

CACHE = SCRIPT_DIR / ".cache"
WORK = ROOT / "build" / "pyinstaller"
SPEC = SCRIPT_DIR / "work"
DIST = ROOT / "dist"
OUT = DIST / "Nomad"

# PostgreSQL installer binaries, pinned. The EDB "binaries" page
# (https://www.enterprisedb.com/download-postgresql-binaries) lists a
# Windows x86-64 archive per version; override via NOMAD_PG_ZIP_URL.
PG_VERSION = "16.15"
PG_FILEID = "1260422"
PG_URL = os.environ.get(
    "NOMAD_PG_ZIP_URL",
    f"https://sbp.enterprisedb.com/getfile.jsp?fileid={PG_FILEID}",
)
PG_ZIP = CACHE / f"postgresql-{PG_VERSION}-1-windows-x64-binaries.zip"

# Embedded Java 21 (Temurin JRE, Windows x64). The Minecraft server needs
# Java 21; bundling it with the app makes the bundle fully self-contained.
# The Adoptium "latest" binary URL follows redirects to a GitHub release zip.
JAVA_VERSION = "21"
ADOPTIUM_URL = os.environ.get(
    "NOMAD_JAVA_ZIP_URL",
    "https://api.adoptium.net/v3/binary/latest/21/ga/windows/x64/jre/hotspot/normal/eclipse",
)
JRE_ZIP = CACHE / "temurin-jre-21-win64.zip"
JAVA_DEST = OUT / "java"

# Extra bundles PyInstaller needs that its default hook set misses sometimes.
COLLECTIONS: list[str] = []
for package in (
    "psycopg",
    "psycopg_binary",
    "uvicorn",
    "fastapi",
    "argon2_cffi",
):
    COLLECTIONS += ["--collect-all", package]


def _run(cmd: list[str], cwd: Path | None = None) -> None:
    print("+ " + " ".join(str(part) for part in cmd), flush=True)
    subprocess.run(cmd, cwd=cwd, check=True)


def download_postgres() -> None:
    if PG_ZIP.exists():
        print(f"using cached {PG_ZIP}", flush=True)
        return
    CACHE.mkdir(parents=True, exist_ok=True)
    print(f"downloading PostgreSQL {PG_VERSION} binaries from {PG_URL}", flush=True)
    urllib.request.urlretrieve(PG_URL, PG_ZIP)  # noqa: S310 - pinned, https source
    print("downloaded.", flush=True)


def extract_postgres(dest: Path) -> Path:
    """Extract the zip (contains a pgsql/ tree) into ``dest``; returns pgsql root."""
    pgsql = dest / "pgsql"
    if (pgsql / "bin" / "initdb.exe").exists():
        return pgsql
    dest.mkdir(parents=True, exist_ok=True)
    print(f"extracting {PG_ZIP} …", flush=True)
    with zipfile.ZipFile(PG_ZIP) as archive:
        archive.extractall(dest)
    return pgsql


def download_java() -> None:
    if JRE_ZIP.exists():
        print(f"using cached {JRE_ZIP}", flush=True)
        return
    CACHE.mkdir(parents=True, exist_ok=True)
    print(f"downloading Java {JAVA_VERSION} (Temurin JRE) from Adoptium…", flush=True)
    request = urllib.request.Request(
        ADOPTIUM_URL,
        headers={"User-Agent": "nomad-bundle-build/1.0"},  # Adoptium blocks generic clients
    )
    with urllib.request.urlopen(request) as source, JRE_ZIP.open("wb") as sink:  # noqa: S310 - pinned https
        shutil.copyfileobj(source, sink)
    print("downloaded.", flush=True)


def extract_java(dest: Path) -> None:
    """Extract the JRE zip into ``dest`` (the bundle's java/ dir).

    The archive contains a single versioned directory (e.g. jdk-21.0.4+7-jre);
    its contents (bin/, lib/, conf/, release, …) are hoisted to the top level.
    """
    if (dest / "bin" / "java.exe").exists():
        print(f"bundle already contains Java at {dest}.", flush=True)
        return
    with zipfile.ZipFile(JRE_ZIP) as archive:
        names = archive.namelist()
        top = next(iter({name.split("/", 1)[0] for name in names if "/" in name}), None)
        if top is None:
            raise SystemExit("unexpected JRE archive layout: no top-level directory")
        dest.mkdir(parents=True, exist_ok=True)
        prefix = f"{top}/"
        members = [name for name in names if name.startswith(prefix)]
        print(f"extracting {JRE_ZIP} ({len(members)} entries, top level {top}) …", flush=True)
        for name in members:
            rel = name[len(prefix):]
            if not rel:
                continue
            target = dest / rel
            if name.endswith("/"):
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(name) as source, target.open("wb") as sink:
                    shutil.copyfileobj(source, sink)


def _pyi(
    name: str,
    script: Path,
    *,
    onefile: bool = False,
    windowed: bool = False,
    extra: list[str] | None = None,
) -> None:
    cmd = [
        PY,
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
    if onefile:
        cmd.append("--onefile")
    if windowed:
        cmd.append("--windowed")
    cmd += COLLECTIONS
    if extra:
        cmd += extra
    cmd.append(str(script))
    _run(cmd)


def build_apps() -> None:
    migrations = ROOT / "backend" / "db" / "migrations"
    for entry in ("backend", "launcher", "nomad_app", "relay", "shared"):
        print(f"build input: {entry}", flush=True)  # informational

    print("building Nomad.exe (hub, onedir)…", flush=True)
    _pyi(
        "Nomad",
        ROOT / "nomad_app" / "hub.py",
        windowed=True,
        extra=["--add-data", f"{migrations}{os.pathsep}backend/db/migrations"],
    )
    print("building nomad-controller.exe (onefile)…", flush=True)
    _pyi("nomad-controller", ROOT / "backend" / "run.py", onefile=True)
    print("building nomad-cli.exe (onefile)…", flush=True)
    _pyi("nomad-cli", ROOT / "launcher" / "cli" / "main.py", onefile=True)
    print("building nomad-relay.exe (onefile)…", flush=True)
    _pyi("nomad-relay", ROOT / "relay" / "server.py", onefile=True)


def assemble() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    existing = {entry.name.lower() for entry in OUT.iterdir()}
    for exe in ("nomad-cli.exe", "nomad-controller.exe", "nomad-relay.exe"):
        if exe.lower() in existing:
            raise SystemExit(
                f"refusing to move {exe}: a file with a case-insensitive match already "
                f"exists in {OUT}. Windows cannot hold both."
            )
        shutil.move(str(DIST / exe), str(OUT / exe))
    (OUT / "data" / "logs").mkdir(parents=True, exist_ok=True)
    print("writing data/.empty marker", flush=True)


def copy_postgres(pgsql: Path) -> None:
    target = OUT / "postgresql"
    if not (target / "bin" / "initdb.exe").exists():
        print("copying PostgreSQL into the bundle…", flush=True)
        shutil.copytree(pgsql, target, ignore=shutil.ignore_patterns(".DS_Store"))
    else:
        print("bundle already contains PostgreSQL.", flush=True)


def slim_postgres() -> None:
    """Drop the PostgreSQL payload we never use.

    The EDB 'binaries' archive ships the whole pgAdmin 4 desktop GUI (683 MB!)
    plus dev/docs items. Nomad only runs initdb, pg_ctl, postgres and friends,
    so all of that is waste that more than doubles the bundle size.
    """
    target = OUT / "postgresql"
    removed = 0
    for name in ("pgAdmin 4", "StackBuilder", "doc", "include"):
        path = target / name
        if path.exists():
            size_mb = sum(
                (child.stat().st_size for child in path.rglob("*") if child.is_file())
            ) // (1024 * 1024)
            shutil.rmtree(path)
            removed += size_mb
            print(f"postgres: removed {name}/ ({size_mb} MB)", flush=True)
    print(f"postgres: slimmed by ~{removed} MB", flush=True)


def write_readme() -> None:
    readme = """Nomad — bundled application (Windows x64)

Run Nomad.exe to start everything: an embedded PostgreSQL, the Nomad
controller on http://127.0.0.1:8000, the relay on port 9000, and the
launcher UI. No installation, no Docker, nothing to configure.

First run creates the data/ directory next to this file (PostgreSQL cluster,
worlds, blobs, logs). Everything portable lives there; delete the whole
folder to fully uninstall.

Quick start
   1. Double-click Nomad.exe.
   2. Click "Open Launcher", register an account, then "New World" to create
      your first world.
   3. "Play" boots a real Minecraft server (Java 21 is bundled in this
      folder — no separate install). Connect your Minecraft client to
      your machine:25565.

How storage works
   The bundled controller keeps a versioned snapshot of every world. When you
   "Play", Nomad downloads the latest snapshot; when you stop, the world is
   snapshotted and uploaded as the next version — so the world "sleeps" in
   storage until the next host picks it up. data/blobs holds the archives,
   data/nomad/worlds the live checkouts.

Going deeper (power users)
   - data/nomad           launcher data (worlds, snapshots, state)
   - data/blobs           uploaded snapshot archives (controller storage)
   - data/pgdata          embedded PostgreSQL cluster (port 5433)
   - data/logs            hub.log and postgres.log
   - java/                embedded Java 21 runtime (Temurin)

   The standalones nomad-cli.exe, nomad-controller.exe, nomad-relay.exe are
   equivalent to the `nomad`, `nomad-controller` and `nomad-relay` commands
   from `pip install nomad`. The CLI is named "nomad-cli" because Windows
   cannot hold both Nomad.exe and nomad.exe in the same folder.

Environment overrides (set before launching, or in your session):
   NOMAD_DATABASE_URL    use an external database instead of the bundled one
   NOMAD_PG_PORT         embedded PostgreSQL port (default 5433)
   NOMAD_CONTROLLER_PORT controller listen port (default 8000)
   NOMAD_CONTROLLER_URL  controller URL the launcher dials
   NOMAD_RELAY_ENABLED   "0"/"1" toggles the bundled relay (default 1)
   NOMAD_RELAY_PORT      relay port (default 9000)
   NOMAD_DATA_DIR        override the portable data location

Headless start (for scripts/scheduling):
   Nomad.exe --serve --quit-after 30
   Nomad.exe --serve --stop-flag <path>   # stops when the file appears

Java is included in this folder (java/) — Nomad finds it automatically. To
use a different Java, set NOMAD_JAVA_PATH to a java.exe of your own.
"""
    (OUT / "README.txt").write_text(readme, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--no-pg-download", action="store_true", help="skip downloading PostgreSQL")
    parser.add_argument("--no-java-download", action="store_true", help="skip downloading the JRE")
    args = parser.parse_args()

    if DIST.exists():
        shutil.rmtree(DIST)
    if WORK.exists():
        shutil.rmtree(WORK)
    if SPEC.exists():
        shutil.rmtree(SPEC)

    print(f"building bundle: {OUT}", flush=True)

    if args.no_pg_download:
        print("skipping PostgreSQL download (--no-pg-download)…", flush=True)
    else:
        download_postgres()
    if args.no_java_download:
        print("skipping Java download (--no-java-download)…", flush=True)
    else:
        download_java()

    build_apps()

    extract_dir = SCRIPT_DIR / "work" / "pg"
    pgsql = extract_postgres(extract_dir) if not args.no_pg_download else extract_dir / "pgsql"
    assemble()
    copy_postgres(pgsql)
    slim_postgres()
    extract_java(JAVA_DEST)
    shutil.rmtree(extract_dir, ignore_errors=True)
    write_readme()

    size_mb = sum(
        (OUT / entry).stat().st_size
        for entry in os.listdir(OUT)
        if (OUT / entry).is_file()
    ) // (1024 * 1024)
    print(f"\nbundle ready at {OUT} (top-level payload ~{size_mb} MB)", flush=True)
    print("run .venv\\Scripts\\python.exe scripts/bundle/build.py again to rebuild.", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())