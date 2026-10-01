"""The world card: one world's name, state, and its single next action.

The card is the whole main screen. It shows what the world is doing right now,
and offers exactly one primary action — PLAY when nobody is hosting, STOP while
you are, JOIN while someone else is — because Nomad decides host-versus-join on
its own. Technical details (leases, addresses) stay out of the card.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from launcher.ui import theme

# Public card states. Kept as a tuple so callers and tests can enumerate them.
CARD_STATES = (
    "ready",
    "starting",
    "hosting",
    "someone-hosting",
    "syncing",
    "stopping",
    "offline",
    "storage-unavailable",
    "error",
)

_STATUS_TEXT = {
    "ready": "Ready",
    "starting": "Starting…",
    "hosting": "You are hosting",
    "someone-hosting": "Someone is hosting",
    "syncing": "Syncing…",
    "stopping": "Stopping…",
    "offline": "Offline",
    "storage-unavailable": "Storage unavailable",
    "error": "Error",
}

_STATUS_DETAIL = {
    "ready": "No one is hosting right now. Press PLAY to start the world.",
    "starting": "Loading the world and starting the server…",
    "hosting": "You are running this world on your PC.",
    "syncing": "Saving the world back to shared storage…",
    "stopping": "Shutting down the server…",
    "offline": "This world's storage is offline.",
    "storage-unavailable": (
        "Nomad couldn't reach your storage. Check the server is online and "
        "your connection is working, then try again."
    ),
    "error": "Something went wrong. See the message at the top of the window.",
}


class WorldCard(QFrame):
    """One world: name, state, world id, and a single primary action."""

    def __init__(
        self,
        world: dict,
        on_play,
        on_join,
        on_stop,
        on_rename,
        on_remove,
        state: str = "ready",
        holder: str | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.world = world
        self.on_play = on_play
        self.on_join = on_join
        self.on_stop = on_stop
        self.on_rename = on_rename
        self.on_remove = on_remove

        self._state = state
        self._holder = holder
        self._action_connected = False

        self.setObjectName("WorldCard")
        self.setStyleSheet(theme.card_style(theme.status_color(state)))

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(6)

        header = QHBoxLayout()
        self.title_label = QLabel(str(world.get("name", "Unnamed World")))
        self.title_label.setStyleSheet(f"font-size: 16px; font-weight: 600; color: {theme.TEXT};")
        header.addWidget(self.title_label)
        header.addStretch()

        self.menu_button = QPushButton("⋯")
        self.menu_button.setObjectName("ghost")
        self.menu_button.setFixedWidth(32)
        self.menu_button.setToolTip("World options")
        self.menu_button.clicked.connect(self._open_menu)
        header.addWidget(self.menu_button)
        layout.addLayout(header)

        self.status_label = QLabel()
        self.status_label.setStyleSheet("font-weight: 600;")
        layout.addWidget(self.status_label)

        self.detail_label = QLabel()
        self.detail_label.setStyleSheet(f"color: {theme.TEXT_MUTED};")
        self.detail_label.setWordWrap(True)
        layout.addWidget(self.detail_label)

        self.id_label = QLabel(f"World ID: {world.get('id', '')}")
        self.id_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.id_label.setStyleSheet(
            f"color: {theme.TEXT_FAINT}; font-size: {theme.FONT_SMALL}; font-family: {theme.MONO};"
        )
        layout.addWidget(self.id_label)

        actions = QHBoxLayout()
        actions.addStretch()
        self.action_button = QPushButton()
        self.action_button.setObjectName("primary")
        self.action_button.setMinimumWidth(130)
        actions.addWidget(self.action_button)
        layout.addLayout(actions)

        self.refresh()

    # --- state -----------------------------------------------------------

    def set_state(self, state: str, holder: str | None = None) -> None:
        self._state = state
        self._holder = holder
        self.refresh()

    # --- rendering -------------------------------------------------------

    def refresh(self) -> None:
        state = self._state
        self.setStyleSheet(theme.card_style(theme.status_color(state)))
        self.status_label.setText(f"● {self._status_text()}")
        self.status_label.setStyleSheet(
            f"color: {theme.status_color(state)}; font-weight: 600;"
        )
        self.detail_label.setText(self._detail_text())
        self._update_action()

    def _status_text(self) -> str:
        if self._state == "someone-hosting":
            return f"{self._holder} is hosting" if self._holder else "Someone is hosting"
        return _STATUS_TEXT.get(self._state, self._state.capitalize())

    def _detail_text(self) -> str:
        if self._state == "someone-hosting":
            return f"{self._holder or 'Someone'} is running this world on their PC."
        return _STATUS_DETAIL.get(self._state, "")

    def _update_action(self) -> None:
        if self._action_connected:
            try:
                self.action_button.clicked.disconnect()
            except RuntimeError:
                pass
            self._action_connected = False

        state = self._state
        if state in ("starting", "syncing", "stopping"):
            self.action_button.setText("…")
            self.action_button.setEnabled(False)
        elif state == "hosting":
            self.action_button.setText("■ STOP SERVER")
            self.action_button.setEnabled(True)
            self.action_button.clicked.connect(lambda: self.on_stop(self.world))
            self._action_connected = True
        elif state == "someone-hosting":
            self.action_button.setText("⇥ JOIN")
            self.action_button.setEnabled(True)
            self.action_button.clicked.connect(lambda: self.on_join(self.world))
            self._action_connected = True
        else:
            # ready / offline / storage-unavailable / error: PLAY doubles as the
            # retry, so a failed state is one click from being tried again.
            self.action_button.setText("▶ PLAY")
            self.action_button.setEnabled(True)
            self.action_button.clicked.connect(lambda: self.on_play(self.world))
            self._action_connected = True

    # --- menu ------------------------------------------------------------

    def _open_menu(self) -> None:
        menu = QMenu(self)
        menu.addAction("Copy World ID", self._copy_id)
        menu.addAction("Rename…", lambda: self.on_rename(self.world))
        menu.addSeparator()
        menu.addAction("Delete…", lambda: self.on_remove(self.world))
        menu.exec(self.menu_button.mapToGlobal(self.menu_button.rect().bottomLeft()))

    def _copy_id(self) -> None:
        QApplication.clipboard().setText(str(self.world.get("id", "")))
