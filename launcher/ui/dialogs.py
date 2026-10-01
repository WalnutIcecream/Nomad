"""Small focused dialogs: create a world, and join one by its World ID.

Kept separate from the main window so the flows stay simple and testable, and
so creating a world is one clear form instead of a stack of input prompts.
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QVBoxLayout,
    QWidget,
)

from launcher.registry import new_world_id
from launcher.ui import theme


class NewWorldDialog(QDialog):
    """Collects a world name; the World ID is generated, never typed."""

    def __init__(self, default_version: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Create World")
        self.setMinimumWidth(420)

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 16)
        root.setSpacing(10)

        self.name = QLineEdit()
        self.name.setPlaceholderText("My Survival World")
        self.version = QLineEdit(default_version)

        form = QFormLayout()
        form.addRow("World name", self.name)
        form.addRow("Game", QLabel("Minecraft"))
        form.addRow("Version", self.version)
        root.addLayout(form)

        hint = QLabel(f"World ID: {new_world_id()} (generated automatically)")
        hint.setStyleSheet(
            f"color: {theme.TEXT_FAINT}; font-size: {theme.FONT_SMALL}; font-family: {theme.MONO};"
        )
        hint.setWordWrap(True)
        root.addWidget(hint)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Create")
        buttons.accepted.connect(self._accept_if_valid)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

        self.name.setFocus()

    def _accept_if_valid(self) -> None:
        if not self.name.text().strip():
            self.name.setFocus()
            return
        self.accept()

    def values(self) -> tuple[str, str]:
        return self.name.text().strip(), self.version.text().strip()


class JoinWorldDialog(QDialog):
    """Adds a world someone shared by its World ID."""

    def __init__(self, default_version: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Join a World")
        self.setMinimumWidth(440)

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 16)
        root.setSpacing(10)

        intro = QLabel(
            "Enter the World ID a friend shared with you. Make sure you have "
            "configured the same storage they use."
        )
        intro.setWordWrap(True)
        intro.setStyleSheet(f"color: {theme.TEXT_MUTED};")
        root.addWidget(intro)

        self.world_id = QLineEdit()
        self.world_id.setPlaceholderText("00000000-0000-0000-0000-000000000000")
        self.name = QLineEdit()
        self.name.setPlaceholderText("optional display name")
        self.version = QLineEdit(default_version)

        form = QFormLayout()
        form.addRow("World ID", self.world_id)
        form.addRow("Name", self.name)
        form.addRow("Version", self.version)
        root.addLayout(form)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Add")
        buttons.accepted.connect(self._accept_if_valid)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

        self.world_id.setFocus()

    def _accept_if_valid(self) -> None:
        if not self.world_id.text().strip():
            self.world_id.setFocus()
            return
        self.accept()

    def values(self) -> tuple[str, str, str]:
        return (
            self.world_id.text().strip(),
            self.name.text().strip(),
            self.version.text().strip(),
        )
