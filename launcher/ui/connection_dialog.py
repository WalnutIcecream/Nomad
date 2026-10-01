"""Shows how to reach a hosted world with the real Minecraft client."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from launcher.ui import theme


class ConnectionDialog(QDialog):
    def __init__(
        self,
        world: dict,
        direct_address: str,
        holder: str | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.world = world
        self.direct_address = direct_address

        self.setWindowTitle(f"Join {world.get('name', 'World')}")
        self.setMinimumWidth(440)

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 16)
        root.setSpacing(10)

        intro = QLabel(
            f"<b>{world.get('name', 'World')}</b> is hosted by "
            f"{holder or 'another player'}."
        )
        intro.setWordWrap(True)
        root.addWidget(intro)

        body = QLabel(
            "Open Minecraft, choose Multiplayer → Direct Connection, and enter "
            "the address below. Use the same Minecraft version as the world."
        )
        body.setWordWrap(True)
        body.setStyleSheet(f"color: {theme.TEXT_MUTED};")
        root.addWidget(body)

        row = QHBoxLayout()
        label = QLabel(direct_address)
        label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        label.setStyleSheet(
            f"font-family: {theme.MONO}; font-size: 14px; background: {theme.BG_ELEVATED};"
            f"padding: 8px; border-radius: {theme.RADIUS_SMALL}px;"
        )
        row.addWidget(label, stretch=1)
        copy = QPushButton("Copy")
        copy.clicked.connect(lambda: QApplication.clipboard().setText(direct_address))
        row.addWidget(copy)
        root.addLayout(row)

        close = QPushButton("Close")
        close.setObjectName("primary")
        close.clicked.connect(self.accept)
        root.addWidget(close, alignment=Qt.AlignRight)
