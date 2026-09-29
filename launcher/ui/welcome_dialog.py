"""First-run welcome dialog.

Shown instead of a bare settings form the first time Nomad opens with no shared
storage configured. It explains what Nomad is in plain language first, then
offers the single next step: choose where the group's world will live.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


class WelcomeDialog(QDialog):
    """Explains the idea, then hands the user to the storage chooser."""

    choose_storage = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Welcome to Nomad")
        self.setMinimumWidth(480)

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 24, 24, 24)
        root.setSpacing(12)

        title = QLabel("Welcome to Nomad")
        title.setStyleSheet("font-size: 20px; font-weight: 700; color: #e8eaf0;")
        root.addWidget(title)

        intro = QLabel(
            "Nomad lets your group share one Minecraft world without keeping "
            "one computer online all the time."
        )
        intro.setWordWrap(True)
        intro.setStyleSheet("font-size: 14px; color: #e8eaf0;")
        root.addWidget(intro)

        body = QLabel(
            "The world stays. The host changes.\n\n"
            "The group's world lives in shared storage. Whoever is online presses "
            "Play to run it for a while, and when they stop, the world is saved "
            "back to storage so someone else can continue it later. No permanent "
            "server is needed."
        )
        body.setWordWrap(True)
        body.setStyleSheet("color: #9aa1b5;")
        root.addWidget(body)

        steps = QLabel(
            "To get your group started:\n"
            "1. Choose where the shared world will live (one person does this).\n"
            "2. Create a world and share its World ID.\n"
            "3. Everyone connects to the same storage and enters the World ID."
        )
        steps.setWordWrap(True)
        steps.setStyleSheet("color: #9aa1b5;")
        root.addWidget(steps)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        skip = QPushButton("Not now")
        skip.clicked.connect(self.reject)
        buttons.addWidget(skip)
        go = QPushButton("Choose where your world lives…")
        go.setDefault(True)
        go.clicked.connect(self._go)
        buttons.addWidget(go)
        root.addLayout(buttons)

    def _go(self) -> None:
        self.accept()
        self.choose_storage.emit()


__all__ = ["WelcomeDialog"]