"""Standalone Qt smoke checks, run as a subprocess so the Qt app lifecycle is
isolated from pytest's interpreter (PySide6 6.11 + Python 3.14 abort on
QApplication creation after pytest imports C extensions)."""

from __future__ import annotations

import sys
import tempfile

SCRATCH = tempfile.mkdtemp(prefix="nomad_ui_check_")


def main() -> int:
    from PySide6.QtWidgets import QApplication, QWidget

    from launcher.config import LauncherSettings
    from launcher.ui.main_window import MainWindow
    from launcher.ui.widgets import WorldCard

    app = QApplication([])
    holder = QWidget()
    holder.show()

    settings = LauncherSettings(data_dir=SCRATCH)

    def make(world: dict):
        return WorldCard(
            world,
            settings,
            on_play=lambda w: None,
            on_join=lambda w: None,
            on_stop=lambda w: None,
            parent=holder,
        )

    sleeping = make({"id": "w1", "name": "Walnut SMP", "status": "sleeping", "member_count": 3})
    assert sleeping.action_button.text() == "▶ PLAY"

    join = make({"id": "w1", "name": "Walnut SMP", "status": "hosting", "member_count": 3})
    join.am_host = False
    join.refresh()
    assert join.action_button.text() == "⇥ JOIN"

    stop = make({"id": "w1", "name": "Walnut SMP", "status": "hosting", "member_count": 3})
    stop.am_host = True
    stop.refresh()
    assert stop.action_button.text() == "■ STOP SERVER"

    # Click handler fires.
    fired: list[dict] = []
    clickable = WorldCard(
        {"id": "w1", "name": "Walnut SMP", "status": "sleeping"},
        settings,
        on_play=lambda w: fired.append(w),
        on_join=lambda w: None,
        on_stop=lambda w: None,
        parent=holder,
    )
    clickable.action_button.click()
    assert len(fired) == 1 and fired[0]["name"] == "Walnut SMP"

    # Refresh updates status text.
    sleeping.refresh({"id": "w1", "name": "Walnut SMP", "status": "hosting", "current_host_name": "Alice"})
    assert "HOSTING" in sleeping.status_label.text()
    assert "Alice" in sleeping.meta_label.text()

    # Main window builds and shows the login view.
    window = MainWindow(LauncherSettings(data_dir=SCRATCH))
    assert window.windowTitle() == "Nomad — World Launcher"
    assert window.centralWidget() is window.login_widget

    print("UI_CHECKS_OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
