"""Launch the PySide6 desktop window.

This is the single entry point for the GUI: both ``nomad gui`` and
``python -m launcher.ui`` route here, so there is exactly one place that builds
the Qt application and shows the window. PySide6 is imported at module level so
a missing GUI dependency surfaces as an ``ImportError`` the CLI can turn into a
friendly install hint.
"""

from __future__ import annotations

import logging
import sys

from PySide6.QtWidgets import QApplication, QMainWindow

from launcher.config import LauncherSettings, load_settings_file
from launcher.secrets import install_log_redaction
from launcher.ui import theme
from launcher.ui.main_window import MainWindow


def run(argv: list[str] | None = None) -> int:
    """Show the window and run the Qt event loop until the user closes it."""
    install_log_redaction()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    install_log_redaction()  # basicConfig adds a handler after the first call
    app = QApplication(sys.argv if argv is None else argv)
    theme.apply_theme(app)
    settings = load_settings_file(LauncherSettings())
    window = QMainWindow()
    window.setWindowTitle("Nomad")
    window.resize(720, 620)
    window.setCentralWidget(MainWindow(settings))
    window.show()
    return app.exec()
