"""Build a self-contained Nomad app.

Produces one portable ``nomad`` executable per platform (Windows / Linux /
macOS), bundled under ``dist/nomad/``:

    dist/nomad/
        nomad(.exe)          the single entry point: every CLI command plus
                             ``nomad gui`` for the desktop window
        _internal/           bundled Python runtime + dependencies
        java/                embedded Temurin JRE 21 (downloaded once, cached)
        data/                runtime data (worlds, registry, settings)

There is deliberately just one binary on every platform: the GUI is reached
through ``nomad gui`` rather than a separate executable, so Windows no longer
needs a ``nomad-cli`` rename to dodge the ``Nomad.exe``/``nomad.exe`` clash.

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
import re
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
OUT = DIST / "nomad"

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
    "launcher.credentials",
    "launcher.registry",
    "launcher.config",
    "launcher.manifest",
    "launcher.process_runtime",
    "launcher.secrets",
    "launcher.audit",
    "launcher.server_properties",
    "launcher.minecraft.vanilla",
    "launcher.minecraft.base",
    "launcher.minecraft.process",
    "launcher.sync.worldfolder",
    "launcher.storage",
    "launcher.storage.r2",
    "launcher.storage.server",
    "launcher.tunnel",
    "launcher.tunnel.none",
    "launcher.tunnel.playit",
    "launcher.download",
    "launcher.archive",
    "launcher.java",
    "launcher.doctor",
    "launcher.logs",
    "launcher.diagnostics",
    "launcher.resources",
    "launcher.ui.app",
    "launcher.ui.about",
    "launcher.ui.onboarding",
    "launcher.ui.tray",
    "launcher.ui.errors",
    "launcher.ui.theme",
    "launcher.ui.dialogs",
    "launcher.ui.storage_wizard",
    "launcher.ui.settings_dialog",
    "launcher.ui.connection_dialog",
    "launcher.ui.widgets",
]

# Extra data PySide6 needs that PyInstaller's hooks sometimes miss. The GUI is
# loaded lazily by `nomad gui`, so it must be bundled into the single binary.
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


def _pyi(name: str, script: Path, *, collect: bool = False) -> None:
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
    assets = ROOT / "assets"
    if assets.is_dir():
        sep = ";" if _IS_WINDOWS else ":"
        for asset in ("nomad.png", "nomad.ico"):
            src = assets / asset
            if src.exists():
                cmd += ["--add-data", f"{src}{sep}assets"]
    icon = ROOT / "assets" / "nomad.ico"
    if icon.exists():
        cmd += ["--icon", str(icon)]
    cmd.append(str(script))
    _run(cmd)


def _version() -> str:
    """Read the single source of truth for the version (launcher/__init__.py)."""
    text = (ROOT / "launcher" / "__init__.py").read_text(encoding="utf-8")
    match = re.search(r'__version__\s*=\s*"([^"]+)"', text)
    return match.group(1) if match else "0.0.0"


def desktop_entry(version: str, exec_name: str = "nomad") -> str:
    """The Linux ``.desktop`` entry for the bundle (wrapped into an AppImage)."""
    return (
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=Nomad\n"
        "GenericName=Minecraft Server Hosting\n"
        "Comment=Distributed Minecraft hosting over your own object storage\n"
        f"Exec={exec_name} gui\n"
        "Icon=nomad\n"
        "Terminal=false\n"
        "Categories=Game;Network;\n"
        "Keywords=minecraft;server;hosting;\n"
        f"X-Nomad-Version={version}\n"
    )


def write_desktop(bundle_dir: Path, version: str) -> None:
    """Drop the ``.desktop`` file and icons into the bundle for desktop use."""
    (bundle_dir / "Nomad.desktop").write_text(desktop_entry(version), encoding="utf-8")
    for asset in ("nomad.png", "nomad.ico"):
        src = ROOT / "assets" / asset
        if src.exists():
            shutil.copy2(src, bundle_dir / asset)


def apprun_script() -> str:
    """The ``AppRun`` entrypoint used inside the AppImage."""
    return '#!/bin/sh\nHERE="$(dirname "$(readlink -f "$0")")"\nexec "$HERE/nomad" gui "$@"\n'


def write_appdir_extras(bundle_dir: Path) -> None:
    """Make ``bundle_dir`` AppImage-ready: an ``AppRun`` and a ``.DirIcon``."""
    apprun = bundle_dir / "AppRun"
    apprun.write_text(apprun_script(), encoding="utf-8")
    apprun.chmod(0o755)
    icon = bundle_dir / "nomad.png"
    if icon.exists():
        shutil.copy2(icon, bundle_dir / ".DirIcon")


def build_apps() -> None:
    # One console executable named `nomad` on every platform. The CLI needs a
    # console, and `nomad gui` starts the window from that same process, so a
    # single binary covers both without the old GUI/CLI name collision.
    _pyi("nomad", ROOT / "launcher" / "cli" / "__main__.py", collect=True)


def assemble() -> None:
    (OUT / "java").mkdir(parents=True, exist_ok=True)
    (OUT / "data" / "logs").mkdir(parents=True, exist_ok=True)
    write_desktop(OUT, _version())
    if not _IS_WINDOWS:
        write_appdir_extras(OUT)


def make_appimage() -> Path | None:
    """Wrap the bundle into an AppImage when ``appimagetool`` is available."""
    tool = shutil.which("appimagetool")
    if _IS_WINDOWS or tool is None:
        return None
    target = DIST / f"Nomad-{_version()}-x86_64.AppImage"
    _run([tool, str(OUT), str(target)])
    return target


_NSI_TEMPLATE = r"""
!define APP_NAME "Nomad"
!define APP_VERSION "@VERSION@"
!define SOURCE_DIR "@SOURCE@"
!define OUT_FILE "@OUT@"

Name "${APP_NAME}"
OutFile "${OUT_FILE}"
InstallDir "$PROGRAMFILES64\${APP_NAME}"
RequestExecutionLevel admin
SetCompressor /SOLID lzma

Page directory
Page instfiles
UninstPage uninstConfirm
UninstPage instfiles

Section "Install"
  SetOutPath "$INSTDIR"
  File /r "${SOURCE_DIR}\*.*"
  CreateDirectory "$SMPROGRAMS\${APP_NAME}"
  CreateShortcut "$SMPROGRAMS\${APP_NAME}\${APP_NAME}.lnk" "$INSTDIR\nomad.exe" "gui"
  CreateShortcut "$DESKTOP\${APP_NAME}.lnk" "$INSTDIR\nomad.exe" "gui"
  WriteUninstaller "$INSTDIR\Uninstall.exe"
  WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP_NAME}" "DisplayName" "${APP_NAME}"
  WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP_NAME}" "DisplayVersion" "${APP_VERSION}"
  WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP_NAME}" "UninstallString" "$INSTDIR\Uninstall.exe"
  WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP_NAME}" "DisplayIcon" "$INSTDIR\nomad.exe"
SectionEnd

Section "Uninstall"
  Delete "$DESKTOP\${APP_NAME}.lnk"
  Delete "$SMPROGRAMS\${APP_NAME}\${APP_NAME}.lnk"
  RMDir "$SMPROGRAMS\${APP_NAME}"
  RMDir /r "$INSTDIR"
  DeleteRegKey HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP_NAME}"
SectionEnd
"""


def installer_script(
    version: str, source_dir: Path | None = None, out_file: Path | None = None
) -> str:
    """The NSIS script for the Windows installer (Start-menu + uninstaller)."""
    source = source_dir or OUT
    out = out_file or (source.parent / f"Nomad-Setup-{version}.exe")
    return (
        _NSI_TEMPLATE.replace("@VERSION@", version)
        .replace("@SOURCE@", str(source))
        .replace("@OUT@", str(out))
        .strip()
        + "\n"
    )


def make_installer() -> Path | None:
    """Build the Windows installer with ``makensis`` when it is available."""
    tool = shutil.which("makensis")
    if not _IS_WINDOWS or tool is None:
        return None
    WORK.mkdir(parents=True, exist_ok=True)
    script = WORK / "nomad.nsi"
    script.write_text(installer_script(_version()), encoding="utf-8")
    _run([tool, str(script)])
    target = DIST / f"Nomad-Setup-{_version()}.exe"
    return target if target.exists() else None


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

    appimage = make_appimage()
    if appimage is not None:
        print(f"appimage ready at {appimage}", flush=True)
    elif not _IS_WINDOWS:
        print(f"appimagetool not found; AppImage-ready bundle is at {OUT}", flush=True)

    installer = make_installer()
    if installer is not None:
        print(f"installer ready at {installer}", flush=True)
    elif _IS_WINDOWS:
        print("makensis not found; skipping the Windows installer", flush=True)

    size_mb = sum(p.stat().st_size for p in OUT.rglob("*") if p.is_file()) // (1024 * 1024)
    print(f"\napp ready at {OUT} (~{size_mb} MB)", flush=True)
    print(
        "configure storage (env vars or data/settings.json), then run `nomad` or `nomad gui`.",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
