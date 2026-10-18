from __future__ import annotations

import importlib.util
from pathlib import Path

from launcher import __version__
from launcher.resources import icon_path, resource_path

REPO = Path(__file__).resolve().parents[2]


def test_icon_path_resolves_from_a_source_checkout() -> None:
    icon = icon_path()
    assert icon is not None
    assert icon.name == "nomad.png"
    assert icon.exists()


def test_resource_path_returns_none_when_missing() -> None:
    assert resource_path("definitely-not-a-resource.xyz") is None


def _load_build_app():
    path = REPO / "scripts" / "app" / "build_app.py"
    spec = importlib.util.spec_from_file_location("nomad_build_app", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


def test_desktop_entry_has_the_required_keys() -> None:
    text = _load_build_app().desktop_entry("1.2.3")
    assert text.startswith("[Desktop Entry]\n")
    assert "Type=Application" in text
    assert "Exec=nomad gui" in text
    assert "Icon=nomad" in text
    assert "X-Nomad-Version=1.2.3" in text


def test_build_app_version_matches_the_package() -> None:
    assert _load_build_app()._version() == __version__


def test_apprun_script_launches_the_gui() -> None:
    script = _load_build_app().apprun_script()
    assert script.startswith("#!/bin/sh\n")
    assert '"$HERE/nomad" gui' in script


def test_write_appdir_extras_creates_apprun_and_diricon(tmp_path: Path) -> None:
    (tmp_path / "nomad.png").write_bytes(b"png-bytes")
    _load_build_app().write_appdir_extras(tmp_path)

    apprun = tmp_path / "AppRun"
    assert apprun.exists()
    assert apprun.stat().st_mode & 0o111  # executable
    assert (tmp_path / ".DirIcon").read_bytes() == b"png-bytes"


def test_installer_script_installs_and_uninstalls() -> None:
    script = _load_build_app().installer_script("1.2.3")
    assert '!define APP_VERSION "1.2.3"' in script
    assert "Nomad-Setup-1.2.3.exe" in script
    assert "CreateShortcut" in script
    assert '"gui"' in script  # shortcuts launch `nomad gui`
    assert "WriteUninstaller" in script
