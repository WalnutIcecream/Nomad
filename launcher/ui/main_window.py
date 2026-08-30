from __future__ import annotations

import logging
import threading
from uuid import UUID

from PySide6.QtCore import QTimer, Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from launcher.agent import HostAgent
from launcher.config import LauncherSettings
from launcher.controller import ControllerClient, ControllerError
from launcher.minecraft.vanilla import VanillaMinecraftRuntime
from launcher.state.machine import LauncherStateMachine
from launcher.state.persist import StateStore
from launcher.ui.widgets import WorldCard

logger = logging.getLogger(__name__)

POLL_INTERVAL_MS = 5000


class MainWindow(QMainWindow):
    worlds_updated = Signal(list)
    status_message = Signal(str)

    def __init__(self, settings: LauncherSettings) -> None:
        super().__init__()
        self.settings = settings
        self.client: ControllerClient | None = None
        self.cards: dict[str, WorldCard] = {}
        self.active_hosting: dict[str, threading.Thread] = {}

        self.setWindowTitle("Nomad — World Launcher")
        self.resize(560, 480)
        self.setStyleSheet(
            "QMainWindow { background: #14161f; }"
            "QWidget { background: transparent; color: #e8eaf0; }"
            "QPushButton { background: #2b3043; border: 1px solid #3a4060; "
            "border-radius: 6px; padding: 8px 14px; }"
            "QPushButton:hover { background: #363c55; }"
            "QLineEdit { color: #e8eaf0; }"
        )

        self.active_agents: dict[UUID, object] = {}
        self.worlds_updated.connect(self._render_worlds)
        self.status_message.connect(self._show_status)

        self._build_login()
        self._start_poller()

    # --- login view ------------------------------------------------------

    def _build_login(self) -> None:
        self.login_widget = QWidget()
        layout = QVBoxLayout(self.login_widget)
        layout.setContentsMargins(40, 80, 40, 40)
        layout.setSpacing(12)

        title = QLabel("Nomad")
        title.setStyleSheet("font-size: 28px; font-weight: 700; color: #e8eaf0;")
        title.setAlignment(Qt.AlignCenter)
        layout.addWidget(title)

        subtitle = QLabel("Your worlds, wherever you are.")
        subtitle.setStyleSheet("color: #9aa1b5;")
        subtitle.setAlignment(Qt.AlignCenter)
        layout.addWidget(subtitle)
        layout.addSpacing(24)

        self.username_input = QLineEdit()
        self.username_input.setPlaceholderText("Username")
        self.username_input.setStyleSheet("padding: 8px; border-radius: 6px; background: #232738;")
        layout.addWidget(self.username_input)

        self.password_input = QLineEdit()
        self.password_input.setPlaceholderText("Password")
        self.password_input.setEchoMode(QLineEdit.Password)
        self.password_input.setStyleSheet("padding: 8px; border-radius: 6px; background: #232738;")
        layout.addWidget(self.password_input)

        self.error_label = QLabel("")
        self.error_label.setStyleSheet("color: #f26d6d;")
        self.error_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.error_label)

        buttons = QHBoxLayout()
        self.register_button = QPushButton("Register")
        self.register_button.clicked.connect(lambda: self._authenticate(register=True))
        self.login_button = QPushButton("Login")
        self.login_button.clicked.connect(lambda: self._authenticate(register=False))
        buttons.addWidget(self.register_button)
        buttons.addWidget(self.login_button)
        layout.addLayout(buttons)
        layout.addStretch()

        self.setCentralWidget(self.login_widget)

    def _authenticate(self, register: bool) -> None:
        username = self.username_input.text().strip()
        password = self.password_input.text()
        if not username or not password:
            self.error_label.setText("Enter a username and password.")
            return
        if self.settings.controller_token:
            self.error_label.setText("Already logged in.")
            return

        def work() -> None:
            try:
                from launcher.ui.auth import login

                token = login(self.settings, username, password, register=register)
                self.client = ControllerClient(self.settings.controller_url, token)
                self.status_message.emit("")
                self._show_worlds_view()
            except Exception as exc:
                self.status_message.emit(str(exc))

        threading.Thread(target=work, daemon=True).start()

    # --- worlds view -----------------------------------------------------

    def _show_worlds_view(self) -> None:
        self.worlds_widget = QWidget()
        root = QVBoxLayout(self.worlds_widget)
        root.setContentsMargins(20, 20, 20, 20)
        root.setSpacing(12)

        header = QHBoxLayout()
        title = QLabel("MY WORLDS")
        title.setStyleSheet("font-size: 18px; font-weight: 700; color: #e8eaf0;")
        header.addWidget(title)
        header.addStretch()

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

        self.setCentralWidget(self.worlds_widget)
        self._refresh_worlds()

    def _refresh_worlds(self) -> None:
        if self.client is None:
            return
        try:
            worlds = self.client.list_worlds()
            self.worlds_updated.emit(worlds)
        except Exception as exc:
            self.status_label.setText(f"Refresh failed: {exc}")

    def _render_worlds(self, worlds: list[dict]) -> None:
        for card in list(self.cards.values()):
            if card.world.get("id") not in [w.get("id") for w in worlds]:
                self.cards_layout.removeWidget(card)
                card.deleteLater()
                del self.cards[card.world["id"]]

        for world in worlds:
            world_id = world["id"]
            card = self.cards.get(world_id)
            if card is None:
                card = WorldCard(
                    world,
                    self.settings,
                    on_play=self._on_play,
                    on_join=self._on_join,
                    on_stop=self._on_stop,
                )
                self.cards[world_id] = card
                self.cards_layout.insertWidget(self.cards_layout.count() - 1, card)
            else:
                card.am_host = world_id in self.active_hosting
                card.refresh(world)

        if not worlds:
            self.status_label.setText("No worlds yet. Create one with the CLI.")

    def _start_poller(self) -> None:
        timer = QTimer(self)
        timer.timeout.connect(self._refresh_worlds)
        timer.start(POLL_INTERVAL_MS)

    # --- actions ---------------------------------------------------------

    def _on_new_world(self) -> None:
        if self.client is None:
            return
        name, ok = QInputDialog.getText(self, "New World", "World name:")
        if not ok or not name.strip():
            return
        version, vok = QInputDialog.getText(
            self,
            "New World",
            "Minecraft version:",
            text=self.settings.minecraft_version,
        )
        if not vok or not version.strip():
            return

        def work() -> None:
            try:
                self.client.create_world(name.strip(), version.strip())
                self._refresh_worlds()
            except Exception as exc:
                self.status_message.emit(f"Could not create world: {exc}")

        threading.Thread(target=work, daemon=True).start()

    def _on_play(self, world: dict) -> None:
        if self.client is None:
            return
        world_id = UUID(world["id"])
        if world_id in self.active_hosting:
            return
        self.status_message.emit(f"Starting {world['name']}…")

        def work() -> None:
            machine = LauncherStateMachine(StateStore(self.settings.state_file))
            agent = HostAgent(
                self.settings,
                machine,
                self.client,
                world_id,
                VanillaMinecraftRuntime(),
            )
            self.active_agents[world_id] = agent
            try:
                code = agent.host()
                if code == 0:
                    self.status_message.emit(f"{world['name']} stopped and saved.")
                else:
                    self.status_message.emit(f"{world['name']}: could not host (see logs).")
            except Exception as exc:
                self.status_message.emit(f"Hosting failed: {exc}")
            finally:
                self.active_hosting.pop(world_id, None)
                self.active_agents.pop(world_id, None)
                self._refresh_worlds()

        thread = threading.Thread(target=work, daemon=True)
        self.active_hosting[world_id] = thread
        thread.start()

    def _on_join(self, world: dict) -> None:
        if self.client is None:
            return
        try:
            connection = world.get("connection") or {}
            if connection.get("mode") == "relay" and connection.get("relay_token"):
                target = (
                    f"Relay {connection.get('relay_host')}:{connection.get('relay_port')} "
                    f"(token {connection.get('relay_token')[:12]}…)"
                )
            else:
                target = connection.get("address") or "unknown"
            self.status_message.emit(
                f"Joining {world['name']}: connect your Minecraft client to {target}."
            )
        except Exception as exc:
            self.status_message.emit(f"Join failed: {exc}")

    def _on_stop(self, world: dict) -> None:
        world_id = UUID(world["id"])
        agent = self.active_agents.get(world_id)
        if agent is not None:
            agent.stop_requested.set()
            self.status_message.emit("Stopping server gracefully…")

    def _show_status(self, message: str) -> None:
        if hasattr(self, "status_label"):
            self.status_label.setText(message)
        else:
            self.error_label.setText(message)
