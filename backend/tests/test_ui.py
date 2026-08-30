from __future__ import annotations

import os
import subprocess
import sys

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from launcher.config import LauncherSettings  # noqa: E402

# The Qt widget checks run in a subprocess: PySide6 6.11 + Python 3.14 abort
# on QApplication creation inside the pytest interpreter (loaded C extensions
# conflict with Qt's bootstrap). The subprocess isolates the Qt app lifecycle.

_UI_CHECK_SCRIPT = os.path.join(os.path.dirname(__file__), "ui_check.py")
# NixOS needs the system C++/GL libs for pip-installed Qt.
_NIX_LIB_DIRS = [
    "/nix/store/3w4ccijixck0wdchh5ab5ykyxqwkxdp5-gcc-14.3.0-lib/lib",
    "/nix/store/adas3ix7v8zry5i1nqagc2j76y0ka7rx-libglvnd-1.7.0/lib",
    "/nix/store/f7dllvig9i72z13kzxczwq7wy8a1jpgg-libxkbcommon-1.13.2/lib",
    "/nix/store/06xhsrgnf0ffca8zklbhwx1hmxajasdb-fontconfig-2.18.2-lib/lib",
    "/nix/store/1wqw2chg5chszx8bkmwdw2i6brf2311b-libxcb-1.17.0/lib",
    "/nix/store/6f2h1w0lr8kvj442cj3m756dgh323h06-libx11-1.8.13/lib",
    "/nix/store/m32vbnhl394m9q8x73y8p1zppxsb3ipi-dbus-1.16.2-lib/lib",
    "/nix/store/m3fklrivqli4arqqc600m4iv6ialc091-freetype-2.14.3/lib",
    "/nix/store/wjvib527dhnf4w04m1axnjv05yr60jn3-glib-2.88.1/lib",
    "/nix/store/b2swxfi8srrbsafvh9iyyhd26mz9giwf-zlib-1.3.2/lib",
    "/nix/store/qv6q79r6jwpzi6qm178ifdckavbpq9dq-zstd-1.5.7/lib",
]


def _run_ui_check() -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"
    existing = env.get("LD_LIBRARY_PATH", "")
    env["LD_LIBRARY_PATH"] = ":".join([d for d in _NIX_LIB_DIRS if os.path.isdir(d)]) + (":" + existing if existing else "")
    return subprocess.run(
        [sys.executable, _UI_CHECK_SCRIPT],
        capture_output=True,
        text=True,
        timeout=60,
        env=env,
    )


class TestUiWidgets:
    def test_world_card_states_and_actions(self) -> None:
        result = _run_ui_check()
        assert result.returncode == 0, f"UI check failed:\n{result.stdout}\n{result.stderr}"
        assert "UI_CHECKS_OK" in result.stdout

    def test_main_window_builds(self) -> None:
        result = _run_ui_check()
        assert result.returncode == 0, f"UI check failed:\n{result.stdout}\n{result.stderr}"
        # The single subprocess run covers both card states and the window.
        assert "UI_CHECKS_OK" in result.stdout


class TestAuth:
    def test_login_helper_against_live_server(self, client, controller_url, tmp_path) -> None:
        """Register + login via the helper against the real uvicorn test server."""
        from backend.tests.helpers import _register

        _register(client, "ui_user")

        from launcher.ui.auth import login

        settings = LauncherSettings(
            data_dir=tmp_path / "data",
            controller_url=controller_url,
        )
        token = login(settings, "ui_user", "password123", register=False)
        assert len(token) > 20
        assert settings.controller_token == token

    def test_login_helper_registers_new_user(self, controller_url, tmp_path) -> None:
        from launcher.ui.auth import login

        settings = LauncherSettings(
            data_dir=tmp_path / "data",
            controller_url=controller_url,
        )
        token = login(settings, "ui_fresh_user", "password123", register=True)
        assert len(token) > 20

    def test_login_helper_bad_password_rejected(self, controller_url, tmp_path) -> None:
        from launcher.ui.auth import login

        settings = LauncherSettings(
            data_dir=tmp_path / "data",
            controller_url=controller_url,
        )
        with pytest.raises(Exception, match="login failed"):
            login(settings, "nobody", "wrong", register=False)
