from __future__ import annotations

import logging
import sys

from PySide6.QtWidgets import QApplication

from launcher.config import LauncherSettings
from launcher.ui.main_window import MainWindow


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    app = QApplication(sys.argv if argv is None else argv)
    settings = LauncherSettings()
    window = MainWindow(settings)
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
