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
        self.setMinimumWidth(420)

        root = QVBoxLayout(self)
        intro = QLabel(
            f"<b>{world.get('name', 'World')}</b> is hosted by "
            f"{holder or 'another player'}."
        )
        intro.setWordWrap(True)
        root.addWidget(intro)

        body = QLabel(
            "Open your Minecraft client and connect to the address below "
            "(multiplayer → direct connect). Use the same Minecraft version "
            "as the world."
        )
        body.setWordWrap(True)
        root.addWidget(body)

        row = QHBoxLayout()
        label = QLabel(direct_address)
        label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        label.setStyleSheet(
            "font-family: monospace; font-size: 14px; background: #232738;"
            "padding: 8px; border-radius: 6px;"
        )
        row.addWidget(label, stretch=1)
        copy = QPushButton("Copy")
        copy.clicked.connect(lambda: QApplication.clipboard().setText(direct_address))
        row.addWidget(copy)
        root.addLayout(row)

        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        root.addWidget(close, alignment=Qt.AlignRight)
