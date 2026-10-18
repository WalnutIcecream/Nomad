"""First-run onboarding: a short, friendly explanation before the wizard.

Three lines that say what Nomad is ("the world stays, the host changes") so a
new user understands the model before being asked for storage credentials.
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from launcher.ui import theme

STEPS: tuple[tuple[str, str], ...] = (
    (
        "One shared world",
        "Your group has a single, persistent Minecraft world. It lives in shared "
        "storage, not on anyone's computer.",
    ),
    (
        "Whoever plays hosts it",
        "The first person to press PLAY runs the world on their PC. There is no "
        "server running 24/7 and nothing to rent.",
    ),
    (
        "It saves itself",
        "When the host stops, the world is saved back to storage, and the next "
        "player continues exactly where you left off.",
    ),
)


class OnboardingDialog(QDialog):
    """The welcome tour: a title and a few plain-language steps."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Welcome to Nomad")
        self.setMinimumWidth(460)

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 18)
        root.setSpacing(14)

        title = QLabel("Welcome to Nomad")
        title.setStyleSheet(f"font-size: {theme.FONT_TITLE}; font-weight: 700;")
        root.addWidget(title)

        for heading, body in STEPS:
            root.addWidget(self._step(heading, body))

        buttons = QDialogButtonBox(QDialogButtonBox.Ok)
        buttons.button(QDialogButtonBox.Ok).setText("Get started")
        buttons.accepted.connect(self.accept)
        root.addWidget(buttons)

    def _step(self, heading: str, body: str) -> QWidget:
        box = QWidget()
        layout = QVBoxLayout(box)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)

        title = QLabel(heading)
        title.setStyleSheet("font-weight: 600;")
        detail = QLabel(body)
        detail.setWordWrap(True)
        detail.setStyleSheet(f"color: {theme.TEXT_MUTED};")

        layout.addWidget(title)
        layout.addWidget(detail)
        return box
