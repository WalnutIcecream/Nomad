"""The About box: version, license, and a one-click diagnostics copy."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from launcher import __version__
from launcher.config import LauncherSettings
from launcher.diagnostics import collect
from launcher.ui import theme


class AboutDialog(QDialog):
    """Product identity plus a "Copy diagnostics" support button."""

    def __init__(self, settings: LauncherSettings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.settings = settings
        self.setWindowTitle("About Nomad")
        self.setMinimumWidth(420)

        root = QVBoxLayout(self)
        root.setContentsMargins(22, 20, 22, 16)
        root.setSpacing(10)

        title = QLabel("Nomad")
        title.setStyleSheet(f"font-size: {theme.FONT_TITLE}; font-weight: 700;")
        root.addWidget(title)

        root.addWidget(self._muted(f"Version {__version__}"))
        root.addWidget(self._muted("Distributed Minecraft hosting over your own object storage."))
        root.addWidget(self._muted("Licensed under Apache-2.0. The world stays; the host changes."))

        self.feedback = QLabel("")
        self.feedback.setWordWrap(True)
        root.addWidget(self.feedback)

        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        copy_button = QPushButton("Copy diagnostics")
        buttons.addButton(copy_button, QDialogButtonBox.ActionRole)
        copy_button.clicked.connect(self._copy_diagnostics)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def _muted(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setWordWrap(True)
        label.setStyleSheet(f"color: {theme.TEXT_MUTED};")
        return label

    def _copy_diagnostics(self) -> None:
        QApplication.clipboard().setText(collect(self.settings))
        self.feedback.setText("Diagnostics copied — paste them into your bug report.")
        self.feedback.setStyleSheet(f"color: {theme.STATUS_OK};")
