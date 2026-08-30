"""The Nomad desktop application window (the "hub").

One window that boots and monitors the bundled stack, exposes the data
directory, and opens the full launcher UI once the controller is ready.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from collections import deque
from logging.handlers import RotatingFileHandler

from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from nomad_app import headless
from nomad_app.server import ServerManager

logger = logging.getLogger(__name__)

STYLE = """
QMainWindow { background: #14161f; }
QWidget { background: transparent; color: #e8eaf0; }
QPushButton { background: #2b3043; border: 1px solid #3a4060; border-radius: 6px; padding: 8px 14px; }
QPushButton:hover { background: #363c55; }
QPushButton:disabled { color: #6b7388; }
QPlainTextEdit { background: #0e1017; border: 1px solid #2b3043; border-radius: 6px; padding: 6px; }
QLabel#subtitle { color: #9aa1b5; }
QLabel#path { color: #9aa1b5; font-size: 11px; }
"""


def _dot_label(running: bool) -> tuple[str, str]:
    return ("● running", "#3fb950") if running else ("○ off", "#6b7388")


class HubWindow(QMainWindow):
    def __init__(self, manager: ServerManager) -> None:
        super().__init__()
        self.manager = manager
        self.launcher: QMainWindow | None = None
        self._pending_logs: deque[str] = deque(maxlen=2000)

        self.setWindowTitle("Nomad")
        self.resize(720, 560)
        self.setStyleSheet(STYLE)
        self._build_ui()

        manager.on_log = self._push_log
        self._poll = QTimer(self)
        self._poll.timeout.connect(self._refresh)
        self._poll.start(500)

    # --- logging bridge -------------------------------------------------

    def _push_log(self, message: str) -> None:
        self._pending_logs.append(f"{time.strftime('%H:%M:%S')}  {message}")

    def _flush_logs(self) -> None:
        while self._pending_logs:
            self._log_view.appendPlainText(self._pending_logs.popleft())
        scrollbar = self._log_view.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    # --- UI -------------------------------------------------------------

    def _build_ui(self) -> None:
        root = QVBoxLayout()
        root.setContentsMargins(24, 24, 24, 24)
        root.setSpacing(10)

        title = QLabel("Nomad")
        title.setStyleSheet("font-size: 26px; font-weight: 700;")
        root.addWidget(title)

        subtitle = QLabel("Distributed Minecraft hosting — bundled server + launcher")
        subtitle.setObjectName("subtitle")
        root.addWidget(subtitle)
        root.addSpacing(8)

        services = QVBoxLayout()
        services.setSpacing(4)
        for key, label in (("postgres", "PostgreSQL"), ("controller", "Controller"), ("relay", "Relay")):
            row = QHBoxLayout()
            name = QLabel(label)
            name.setStyleSheet("font-weight: 600;")
            state = QLabel("○ off")
            state.setObjectName(f"{key}_state")
            setattr(self, f"_{key}_state", state)
            row.addWidget(name)
            row.addStretch()
            row.addWidget(state)
            services.addLayout(row)
        root.addLayout(services)
        root.addSpacing(8)

        info = QLabel()
        info.setObjectName("path")
        self._info = info
        root.addWidget(info)

        self._log_view = QPlainTextEdit()
        self._log_view.setReadOnly(True)
        root.addWidget(self._log_view, stretch=1)

        buttons = QHBoxLayout()
        self._launch_button = QPushButton("Open Launcher")
        self._launch_button.clicked.connect(self._on_open_launcher)
        buttons.addWidget(self._launch_button)

        data_button = QPushButton("Open Data Folder")
        data_button.clicked.connect(self._on_open_data)
        buttons.addWidget(data_button)

        buttons.addStretch()

        self._start_button = QPushButton("Start Server")
        self._start_button.clicked.connect(self._on_toggle)
        buttons.addWidget(self._start_button)

        quit_button = QPushButton("Quit")
        quit_button.clicked.connect(self.close)
        buttons.addWidget(quit_button)

        root.addLayout(buttons)
        widget = QWidget()
        widget.setLayout(root)
        self.setCentralWidget(widget)

    # --- refresh --------------------------------------------------------

    def _refresh(self) -> None:
        status = self.manager.status()
        for key in ("postgres", "controller", "relay"):
            label = getattr(self, f"_{key}_state")
            text, color = _dot_label(status["components"].get(key) == "on")
            label.setText(text)
            label.setStyleSheet(f"color: {color};")

        paths = (
            f"Data: {self.manager.data_dir}    "
            f"Controller: {self.manager.controller_url}    "
            f"DB: {self.manager.data_dir / 'pgdata'}"
        )
        self._info.setText(paths)

        state = status["state"]
        self._launch_button.setEnabled(state == "running")
        if state in ("starting", "stopping"):
            self._start_button.setText("Working…")
            self._start_button.setEnabled(False)
        elif state == "running":
            self._start_button.setText("Stop")
            self._start_button.setEnabled(True)
        else:
            self._start_button.setText("Start Server")
            self._start_button.setEnabled(True)

        stop_flag = self.manager.data_dir / "stop.flag"
        if stop_flag.exists():
            try:
                stop_flag.unlink()
            except OSError:
                pass
            self._push_log("stop flag seen; shutting down")
            self.close()

        self._flush_logs()

    # --- actions --------------------------------------------------------

    def _on_toggle(self) -> None:
        if self.manager.status()["state"] == "running":
            self.manager.stop()
        else:
            self.manager.start()

    def _on_open_launcher(self) -> None:
        if self.manager.status()["state"] != "running":
            self.manager.start()
            QMessageBox.information(
                self, "Nomad", "The bundled server is starting. Click Open Launcher again in a moment."
            )
            return
        if self.launcher is None:
            from launcher.config import LauncherSettings
            from launcher.ui.main_window import MainWindow

            self.launcher = MainWindow(LauncherSettings())
        self.launcher.show()
        self.launcher.raise_()

    def _on_open_data(self) -> None:
        self.manager.data_dir.mkdir(parents=True, exist_ok=True)
        os.startfile(self.manager.data_dir)  # noqa: S606 - Windows desktop app

    def closeEvent(self, event) -> None:  # type: ignore[override]
        self._poll.stop()
        self.manager.stop()
        super().closeEvent(event)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="Nomad", description="Bundled Nomad application")
    parser.add_argument("--serve", action="store_true", help="run headless (no window)")
    parser.add_argument("--stop-flag", default=None, help="headless: shut down when this file appears")
    parser.add_argument("--quit-after", type=float, default=None, help="headless: exit after N seconds")
    args, _ = parser.parse_known_args(argv)

    if args.serve:
        return headless.main(argv)

    logger.setLevel(logging.INFO)
    manager = ServerManager()
    log_dir = manager.logs_dir
    log_dir.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[RotatingFileHandler(log_dir / "hub.log", maxBytes=2_000_000, backupCount=3)],
    )

    app = QApplication(sys.argv if argv is None else argv)
    manager = ServerManager()
    window = HubWindow(manager)
    window.show()
    QTimer.singleShot(300, manager.start)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())