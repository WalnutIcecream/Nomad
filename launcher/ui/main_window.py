"""The PySide6 launcher window.

No accounts and no login page: the window lists the worlds this machine knows
about, and each card offers the single action that applies right now. Nomad
decides host-versus-join by reading the lease, so the user never has to.

On first run the storage wizard opens, because a world must live somewhere
before anything else makes sense.
"""

from __future__ import annotations

import logging
import threading

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from launcher.agent import HostAgent
from launcher.cloud import UsageCounter
from launcher.config import LauncherSettings
from launcher.registry import WorldRegistry
from launcher.storage import WorldStoreProtocol, build_store as build_backend, provider_label
from launcher.ui import theme
from launcher.ui.connection_dialog import ConnectionDialog
from launcher.ui.dialogs import JoinWorldDialog, NewWorldDialog
from launcher.ui.settings_dialog import SettingsDialog
from launcher.ui.storage_wizard import StorageWizard
from launcher.ui.widgets import WorldCard

logger = logging.getLogger(__name__)

POLL_INTERVAL_MS = 3000

# Host-lifecycle phases (from HostAgent) mapped onto card states.
PHASE_STATES = {
    "starting": "starting",
    "pulling": "starting",
    "booting": "starting",
    "hosting": "hosting",
    "stopping": "stopping",
    "syncing": "syncing",
    "releasing": "syncing",
    "done": "ready",
}


def build_store(settings: LauncherSettings) -> WorldStoreProtocol | None:
    """Construct the configured backend, or None when it is not usable.

    Returns None (rather than raising) so the window can open and offer setup
    instead of crashing on a missing or wrong configuration.
    """
    try:
        return build_backend(settings, usage=UsageCounter(settings.usage_file))
    except Exception:
        return None


class MainWindow(QWidget):
    """Worlds list plus the storage indicator; the whole launcher surface."""

    worlds_changed = Signal()
    status_message = Signal(str)
    progress = Signal(str, str)  # world_id, phase

    def __init__(self, settings: LauncherSettings, prompt_settings: bool = True) -> None:
        super().__init__()
        self.settings = settings
        self.registry = WorldRegistry(settings.registry_file)
        self.store = build_store(settings)
        self.active_agents: dict[str, HostAgent] = {}
        self.states: dict[str, str] = {}

        self.setWindowTitle("Nomad")

        self.worlds_changed.connect(self._reload)
        self.status_message.connect(self._show_status)
        self.progress.connect(self._on_progress)
        self._build_ui()
        self._reload()

        self._poll = QTimer(self)
        self._poll.timeout.connect(self._refresh_statuses)
        self._poll.start(POLL_INTERVAL_MS)

        if self.store is None and prompt_settings:
            QTimer.singleShot(0, self._open_setup)

    # --- UI -------------------------------------------------------------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(theme.SPACE_LARGE, theme.SPACE_LARGE, theme.SPACE_LARGE, theme.SPACE_LARGE)
        root.setSpacing(theme.SPACE)

        header = QHBoxLayout()
        title = QLabel("Your worlds")
        title.setStyleSheet(f"font-size: {theme.FONT_TITLE}; font-weight: 700;")
        header.addWidget(title)
        header.addStretch()

        self.storage_button = QPushButton()
        self.storage_button.setObjectName("ghost")
        self.storage_button.clicked.connect(self._prompt_settings)
        header.addWidget(self.storage_button)

        join_button = QPushButton("Join a world")
        join_button.clicked.connect(self._on_join_world)
        header.addWidget(join_button)

        create_button = QPushButton("+ Create World")
        create_button.setObjectName("primary")
        create_button.clicked.connect(self._on_new_world)
        header.addWidget(create_button)
        root.addLayout(header)

        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        self.status_label.setStyleSheet(f"color: {theme.TEXT_MUTED};")
        root.addWidget(self.status_label)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        self.cards_container = QWidget()
        self.cards_layout = QVBoxLayout(self.cards_container)
        self.cards_layout.setSpacing(10)
        self.cards_layout.addStretch()
        scroll.setWidget(self.cards_container)
        root.addWidget(scroll, stretch=1)

        self.empty_label = QLabel(
            "No worlds yet.\n\nCreate one and share its World ID with your group, "
            "or add a world someone shared with you."
        )
        self.empty_label.setAlignment(Qt.AlignCenter)
        self.empty_label.setStyleSheet(f"color: {theme.TEXT_FAINT};")
        self.cards_layout.insertWidget(0, self.empty_label)

        self._update_storage_indicator()

    def _update_storage_indicator(self) -> None:
        if self.store is None:
            self.storage_button.setText(f"● {provider_label(self.settings.storage_backend)} — not set up")
            self.storage_button.setStyleSheet(f"color: {theme.STATUS_ERROR};")
            self.storage_button.setToolTip("Open storage settings to finish setup")
        else:
            self.storage_button.setText(f"● {provider_label(self.settings.storage_backend)}")
            self.storage_button.setStyleSheet(f"color: {theme.STATUS_OK};")
            self.storage_button.setToolTip("Worlds are stored here — click to change")

    def _clear_cards(self) -> None:
        while self.cards_layout.count() > 1:
            item = self.cards_layout.takeAt(0)
            widget = item.widget()
            if widget is not None and widget is not self.empty_label:
                widget.deleteLater()

    def _reload(self) -> None:
        self._clear_cards()
        worlds = self.registry.list_worlds()
        self.empty_label.setVisible(not worlds)
        if not worlds:
            self.cards_layout.insertWidget(0, self.empty_label)
        for world in worlds:
            state, holder = self._state_for(world["id"])
            card = WorldCard(
                world,
                on_play=self._on_play,
                on_join=self._on_join,
                on_stop=self._on_stop,
                on_rename=self._on_rename,
                on_remove=self._on_remove,
                state=state,
                holder=holder,
            )
            self.cards_layout.insertWidget(self.cards_layout.count() - 1, card)
        self._update_storage_indicator()

    def _state_for(self, world_id: str) -> tuple[str, str | None]:
        """Resolve the card state for a world: local activity wins over polling."""
        if world_id in self.active_agents:
            return self.states.get(world_id, "starting"), None
        if self.store is None:
            return "storage-unavailable", None
        try:
            status = self.store.status(world_id)
        except Exception:
            return "storage-unavailable", None
        if status.get("hosted"):
            return "someone-hosting", status.get("holder")
        return "ready", None

    def _refresh_statuses(self) -> None:
        """Poll the lease for every world and update the cards in place."""
        for i in range(self.cards_layout.count()):
            item = self.cards_layout.itemAt(i)
            card = item.widget() if item else None
            if not isinstance(card, WorldCard):
                continue
            state, holder = self._state_for(card.world["id"])
            card.set_state(state, holder)

    # --- actions ---------------------------------------------------------

    def _on_new_world(self) -> None:
        dialog = NewWorldDialog(self.settings.minecraft_version, parent=self)
        if dialog.exec() != NewWorldDialog.DialogCode.Accepted:
            return
        name, version = dialog.values()
        world = self.registry.add(name, version or self.settings.minecraft_version)
        self.status_message.emit(
            f"Created '{world['name']}'. Share this World ID with your group: {world['id']}"
        )
        self.worlds_changed.emit()

    def _on_join_world(self) -> None:
        dialog = JoinWorldDialog(self.settings.minecraft_version, parent=self)
        if dialog.exec() != JoinWorldDialog.DialogCode.Accepted:
            return
        world_id, name, version = dialog.values()
        if self.registry.get(world_id) is not None:
            self.status_message.emit("That world is already in your list.")
            return
        world = self.registry.add_with_id(
            world_id, name or world_id[:8], version or self.settings.minecraft_version
        )
        self.status_message.emit(f"Added '{world['name']}'. Press PLAY when you want to host it.")
        self.worlds_changed.emit()

    def _on_play(self, world: dict) -> None:
        if self.store is None:
            self.status_message.emit("Set up storage first — worlds need somewhere to live.")
            self._open_setup()
            return
        world_id = world["id"]
        if world_id in self.active_agents:
            return
        self.states[world_id] = "starting"
        self.status_message.emit(f"Starting {world['name']}…")
        self.worlds_changed.emit()

        def report(phase: str) -> None:
            self.progress.emit(world_id, phase)

        def work() -> None:
            agent = HostAgent(self.settings, self.store, world, progress=report)
            self.active_agents[world_id] = agent
            try:
                code = agent.host()
                if agent.upload_error:
                    self.status_message.emit(
                        "Your world couldn't be synced. Your current copy is still "
                        "available on this computer."
                    )
                elif code == 0:
                    self.status_message.emit(
                        f"{world['name']} stopped and saved. Another player can host it now."
                    )
                else:
                    self.status_message.emit(
                        f"Couldn't start {world['name']} — someone else may be hosting it."
                    )
            except Exception as exc:
                self.status_message.emit(f"Hosting failed: {exc}")
            finally:
                self.active_agents.pop(world_id, None)
                self.states.pop(world_id, None)
                self.worlds_changed.emit()

        threading.Thread(target=work, daemon=True).start()

    def _on_progress(self, world_id: str, phase: str) -> None:
        state = PHASE_STATES.get(phase)
        if state is None:
            return
        if state == "ready":
            self.states.pop(world_id, None)
        else:
            self.states[world_id] = state
        self._refresh_statuses()
        name = next(
            (w["name"] for w in self.registry.list_worlds() if w["id"] == world_id), "world"
        )
        messages = {
            "starting": f"Starting {name}…",
            "pulling": "Loading the world…",
            "booting": "Starting the server…",
            "hosting": "You're hosting. Friends can join now.",
            "stopping": "Stopping the server…",
            "syncing": "Saving and syncing the world…",
            "releasing": "World is ready for another player.",
        }
        if phase in messages:
            self.status_message.emit(messages[phase])

    def _on_stop(self, world: dict) -> None:
        agent = self.active_agents.get(world["id"])
        if agent is not None:
            self.states[world["id"]] = "stopping"
            agent.stop_requested.set()
            self.status_message.emit("Stopping the server…")
            self._refresh_statuses()

    def _on_join(self, world: dict) -> None:
        if self.store is None:
            self._open_setup()
            return
        try:
            status = self.store.status(world["id"])
        except Exception as exc:
            self.status_message.emit(
                f"Nomad couldn't reach your storage. Check the server is online and "
                f"try again. ({exc})"
            )
            return
        if not status.get("hosted"):
            self.status_message.emit(f"'{world['name']}' isn't being hosted right now.")
            return
        if status.get("address"):
            ConnectionDialog(
                world, direct_address=status["address"], holder=status.get("holder"), parent=self
            ).exec()
        else:
            self.status_message.emit(
                f"{status.get('holder')} is hosting ' {world['name']}' but hasn't "
                "published an address yet. Ask them to set a public address."
            )

    def _on_rename(self, world: dict) -> None:
        name, ok = QInputDialog.getText(
            self, "Rename World", "New name:", text=str(world.get("name", ""))
        )
        if ok and name.strip():
            self.registry.rename(world["id"], name.strip())
            self.worlds_changed.emit()

    def _on_remove(self, world: dict) -> None:
        confirm = QMessageBox.question(
            self,
            "Delete World",
            f"Remove '{world.get('name')}' from this computer?\n\n"
            "The world in shared storage is not deleted, and other players are "
            "unaffected. You can add it again with its World ID.",
        )
        if confirm == QMessageBox.Yes:
            self.registry.remove(world["id"])
            self.status_message.emit(f"Removed '{world.get('name')}' from this computer.")
            self.worlds_changed.emit()

    # --- storage ---------------------------------------------------------

    def _open_setup(self) -> None:
        """Run the storage wizard, then pick up whatever it saved."""
        dialog = StorageWizard(self.settings, parent=self)
        if dialog.exec() == StorageWizard.DialogCode.Accepted:
            self.store = build_store(self.settings)
            if self.store is None:
                self.status_message.emit("Storage still isn't usable — check the settings.")
            else:
                self.status_message.emit("Storage is ready. Create a world to get started.")
        self.worlds_changed.emit()
        self._refresh_statuses()

    def _prompt_settings(self) -> None:
        SettingsDialog(self.settings, parent=self).exec()
        self.store = build_store(self.settings)
        self.worlds_changed.emit()
        self._refresh_statuses()

    def _show_status(self, message: str) -> None:
        self.status_label.setText(message)
