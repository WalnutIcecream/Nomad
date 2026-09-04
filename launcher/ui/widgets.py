"""A world card for the R2 launcher: name + state + play/stop/join action."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from launcher.config import LauncherSettings


class WorldCard(QFrame):
    """One world row: name, host status, and a single action button."""

    def __init__(
        self,
        world: dict,
        settings: LauncherSettings,
        on_play,
        on_join,
        on_stop,
        on_settings,
        status: str = "sleeping",
        is_host: bool = False,
        holder: str | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.world = world
        self.settings = settings
        self.on_play = on_play
        self.on_join = on_join
        self.on_stop = on_stop
        self.on_settings = on_settings

        self._status = status
        self._is_host = is_host
        self._holder = holder
        self._action_connected = False

        self.setFrameShape(QFrame.StyledPanel)
        self.setStyleSheet(
            "WorldCard { background: #1e2130; border: 1px solid #33384d; border-radius: 8px; }"
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(6)

        header = QHBoxLayout()
        title = QLabel(str(world.get("name", "Unnamed World")))
        title.setStyleSheet("font-size: 16px; font-weight: 600; color: #e8eaf0;")
        header.addWidget(title)
        header.addStretch()

        self.status_label = QLabel()
        header.addWidget(self.status_label)
        layout.addLayout(header)

        self.meta_label = QLabel()
        self.meta_label.setStyleSheet("color: #9aa1b5;")
        self.meta_label.setWordWrap(True)
        layout.addWidget(self.meta_label)

        version_label = QLabel(f"Minecraft {world.get('minecraft_version', '1.21.1')}")
        version_label.setStyleSheet("color: #6b7280; font-size: 12px;")
        layout.addWidget(version_label)

        actions = QHBoxLayout()
        actions.addStretch()
        self.action_button = QPushButton()
        self.action_button.setMinimumWidth(120)
        actions.addWidget(self.action_button)
        layout.addLayout(actions)

        self.refresh()

    # --- state -----------------------------------------------------------

    def set_hosting_state(
        self,
        status: str,
        is_host: bool = False,
        holder: str | None = None,
    ) -> None:
        self._status = status
        self._is_host = is_host
        self._holder = holder
        self.refresh()

    # --- rendering -------------------------------------------------------

    def refresh(self) -> None:
        status = self._status
        colors = {
            "sleeping": "#5ad1a0",
            "starting": "#f0b35f",
            "hosting": "#4f9cf7",
            "stopping": "#f0b35f",
            "unknown": "#f26d6d",
        }
        color = colors.get(status, "#9aa1b5")
        self.status_label.setText(f"● {status.upper()}")
        self.status_label.setStyleSheet(f"color: {color}; font-weight: 600;")

        if self._is_host:
            host_text = "YOU"
        elif self._holder:
            host_text = self._holder
        else:
            host_text = "—"
        self.meta_label.setText(f"Host: {host_text}")

        self._update_action()

    def _update_action(self) -> None:
        if self._action_connected:
            try:
                self.action_button.clicked.disconnect()
            except RuntimeError:
                pass
            self._action_connected = False

        if self._status == "sleeping":
            self.action_button.setText("▶ PLAY")
            self.action_button.clicked.connect(lambda: self.on_play(self.world))
        elif self._is_host:
            self.action_button.setText("■ STOP SERVER")
            self.action_button.clicked.connect(lambda: self.on_stop(self.world))
        else:
            self.action_button.setText("⇥ JOIN")
            self.action_button.clicked.connect(lambda: self.on_join(self.world))
        self._action_connected = True
