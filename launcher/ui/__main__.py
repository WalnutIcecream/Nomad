from __future__ import annotations

import logging
import sys

from PySide6.QtWidgets import QApplication, QMainWindow

from launcher.config import LauncherSettings, load_settings_file
from launcher.secrets import install_log_redaction
from launcher.ui.main_window import MainWindow


def main(argv: list[str] | None = None) -> int:
    install_log_redaction()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    install_log_redaction()  # basicConfig adds a handler after the first call
    app = QApplication(sys.argv if argv is None else argv)
    settings = load_settings_file(LauncherSettings())
    window = QMainWindow()
    window.setWindowTitle("Nomad")
    window.resize(620, 560)
    window.setCentralWidget(MainWindow(settings))
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
