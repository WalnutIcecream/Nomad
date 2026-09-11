"""The PySide6 launcher window for the R2-backed design.

No accounts, no login page: the window lists the worlds this machine knows
about (from the local registry), with PLAY/STOP driving the host agent and JOIN
showing the host's published address from the lease. R2 credentials are
configured once (first-run dialog or R2 Settings).
"""

from __future__ import annotations

import logging
import threading

from PySide6.QtCore import QTimer, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from launcher.agent import HostAgent
from launcher.cloud import UsageCounter
from launcher.config import LauncherSettings
from launcher.registry import WorldRegistry
from launcher.storage import WorldStoreProtocol, build_store as build_backend
from launcher.ui.connection_dialog import ConnectionDialog
from launcher.ui.settings_dialog import SettingsDialog
from launcher.ui.widgets import WorldCard

logger = logging.getLogger(__name__)

POLL_INTERVAL_MS = 3000


def build_store(settings: LauncherSettings) -> WorldStoreProtocol | None:
    """Construct the configured storage backend, or None if it is unconfigured.

    Returns None (rather than raising) so the window can open and prompt for
    settings instead of crashing on a missing config.
    """
    try:
        return build_backend(settings, usage=UsageCounter(settings.usage_file))
    except Exception:
        return None


class MainWindow(QWidget):
    """The launcher: worlds list + play/stop/join against the R2 world store."""

    worlds_changed = Signal()
    status_message = Signal(str)

    def __init__(self, settings: LauncherSettings, prompt_settings: bool = True) -> None:
        super().__init__()
        self.settings = settings
        self.registry = WorldRegistry(settings.registry_file)
        self.store = build_store(settings)
        self.active_agents: dict[str, HostAgent] = {}
        self.hosting_states: dict[str, str] = {}  # world_id -> "starting"|"hosting"

        self.setWindowTitle("Nomad")
        self.setStyleSheet(
            "QWidget { background: transparent; color: #e8eaf0; }"
            "QPushButton { background: #2b3043; border: 1px solid #3a4060; "
            "border-radius: 6px; padding: 8px 14px; }"
            "QPushButton:hover { background: #363c55; }"
        )

        self.worlds_changed.connect(self._reload)
        self.status_message.connect(self._show_status)
        self._build_ui()
        self._reload()

        self._poll = QTimer(self)
        self._poll.timeout.connect(self._refresh_statuses)
        self._poll.start(POLL_INTERVAL_MS)

        if self.store is None and prompt_settings:
            # First-run (or missing R2 config): show the settings dialog
            # non-modally so the app still opens; save applies on accept.
            self._settings_dialog: QDialog | None = None
            QTimer.singleShot(0, self._open_settings_nonmodal)

    # --- UI -------------------------------------------------------------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 20, 20, 20)
        root.setSpacing(12)

        header = QHBoxLayout()
        title = QLabel("MY WORLDS")
        title.setStyleSheet("font-size: 18px; font-weight: 700;")
        header.addWidget(title)
        header.addStretch()

        settings_button = QPushButton("R2 Settings…")
        settings_button.clicked.connect(self._prompt_settings)
        header.addWidget(settings_button)

        new_world_button = QPushButton("New World")
        new_world_button.clicked.connect(self._on_new_world)
        header.addWidget(new_world_button)

        self.status_label = QLabel("")
        self.status_label.setStyleSheet("color: #9aa1b5;")
        header.addWidget(self.status_label)
        root.addLayout(header)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")
        self.cards_container = QWidget()
        self.cards_layout = QVBoxLayout(self.cards_container)
        self.cards_layout.setSpacing(10)
        self.cards_layout.addStretch()
        scroll.setWidget(self.cards_container)
        root.addWidget(scroll, stretch=1)

    def _clear_cards(self) -> None:
        while self.cards_layout.count() > 1:
            item = self.cards_layout.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()

    def _reload(self) -> None:
        self._clear_cards()
        worlds = self.registry.list_worlds()
        for world in worlds:
            card = WorldCard(
                world,
                self.settings,
                on_play=self._on_play,
                on_join=self._on_join,
                on_stop=self._on_stop,
                on_settings=self._on_world_settings,
                status=self.hosting_states.get(world["id"], "sleeping"),
                is_host=world["id"] in self.active_agents,
            )
            self.cards_layout.insertWidget(self.cards_layout.count() - 1, card)
        if not worlds:
            self.status_label.setText("No worlds yet. Click New World.")

    def _refresh_statuses(self) -> None:
        """Poll the lease for every world and update the cards."""
        if self.store is None:
            return
        for i in range(self.cards_layout.count()):
            item = self.cards_layout.itemAt(i)
            card = item.widget() if item else None
            if not isinstance(card, WorldCard):
                continue
            world_id = card.world["id"]
            if world_id in self.active_agents:
                card.set_hosting_state(self.hosting_states.get(world_id, "hosting"), is_host=True)
                continue
            try:
                status = self.store.status(world_id)
                if status.get("hosted"):
                    card.set_hosting_state("hosting", is_host=False, holder=status.get("holder"))
                else:
                    card.set_hosting_state("sleeping")
            except Exception:
                card.set_hosting_state("unknown")

    # --- actions ---------------------------------------------------------

    def _on_new_world(self) -> None:
        name, ok = QInputDialog.getText(self, "New World", "World name:")
        if not ok or not name.strip():
            return
        version, vok = QInputDialog.getText(
            self, "New World", "Minecraft version:", text=self.settings.minecraft_version
        )
        if not vok or not version.strip():
            return
        world = self.registry.add(name.strip(), version.strip())
        self.status_message.emit(f"Created '{world['name']}'. Press PLAY to start it.")
        self.worlds_changed.emit()

    def _on_play(self, world: dict) -> None:
        if self.store is None:
            self._prompt_settings()
            return
        world_id = world["id"]
        if world_id in self.active_agents:
            return
        self.hosting_states[world_id] = "starting"
        self.status_message.emit(f"Starting {world['name']}…")
        self.worlds_changed.emit()

        def work() -> None:
            agent = HostAgent(self.settings, self.store, world)
            self.active_agents[world_id] = agent
            self.hosting_states[world_id] = "hosting"
            self.worlds_changed.emit()
            try:
                code = agent.host()
                if code == 0:
                    self.status_message.emit(f"{world['name']} stopped and saved to the cloud.")
                else:
                    self.status_message.emit(
                        f"{world['name']}: couldn't host — is someone else hosting it?"
                    )
            except Exception as exc:
                self.status_message.emit(f"Hosting failed: {exc}")
            finally:
                self.active_agents.pop(world_id, None)
                self.hosting_states.pop(world_id, None)
                self.worlds_changed.emit()

        threading.Thread(target=work, daemon=True).start()

    def _on_stop(self, world: dict) -> None:
        agent = self.active_agents.get(world["id"])
        if agent is not None:
            agent.stop_requested.set()
            self.status_message.emit("Stopping server gracefully…")

    def _on_join(self, world: dict) -> None:
        if self.store is None:
            self._prompt_settings()
            return
        try:
            status = self.store.status(world["id"])
        except Exception as exc:
            self.status_message.emit(f"Could not reach world storage: {exc}")
            return
        if not status.get("hosted"):
            self.status_message.emit(f"'{world['name']}' isn't hosted right now.")
            return
        if status.get("address"):
            dialog = ConnectionDialog(world, direct_address=status["address"])
            dialog.exec()
        else:
            self.status_message.emit(
                f"'{world['name']}' is hosted by {status.get('holder')}, "
                "but no address has been published yet."
            )

    def _on_world_settings(self, world: dict) -> None:
        dialog = SettingsDialog(self.settings, world)
        dialog.exec()
        self.worlds_changed.emit()

    def _open_settings_nonmodal(self) -> None:
        """Show the R2 settings dialog without blocking the main window."""
        dialog = SettingsDialog(self.settings, None, parent=self)
        dialog.accepted.connect(self._on_settings_accepted)
        dialog.rejected.connect(self._on_settings_rejected)
        self._settings_dialog = dialog
        dialog.show()

    def _on_settings_accepted(self) -> None:
        self.store = build_store(self.settings)
        self.worlds_changed.emit()
        self._refresh_statuses()

    def _on_settings_rejected(self) -> None:
        # User skipped configuration; the app remains usable read-only.
        self.store = build_store(self.settings)
        self._refresh_statuses()

    def _prompt_settings(self) -> None:
        """Blocking settings dialog (used from the R2 Settings button)."""
        dialog = SettingsDialog(self.settings, None, parent=self)
        dialog.exec()
        self.store = build_store(self.settings)
        self.worlds_changed.emit()
        self._refresh_statuses()

    def _show_status(self, message: str) -> None:
        self.status_label.setText(message)
