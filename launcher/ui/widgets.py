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
    """A single world row: name, status, version, host, and an action button."""

    def __init__(
        self,
        world: dict,
        settings: LauncherSettings,
        on_play,
        on_join,
        on_stop,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.world = world
        self.settings = settings
        self.on_play = on_play
        self.on_join = on_join
        self.on_stop = on_stop
        # Set by the main window when the local launcher is the active host.
        self.am_host = False

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
        layout.addWidget(self.meta_label)

        actions = QHBoxLayout()
        actions.addStretch()
        self.action_button = QPushButton()
        self.action_button.setMinimumWidth(120)
        actions.addWidget(self.action_button)
        layout.addLayout(actions)

        self.refresh()

    # --- rendering -------------------------------------------------------

    def refresh(self, world: dict | None = None) -> None:
        if world is not None:
            self.world = world
        status = str(self.world.get("status", "sleeping"))
        latest = self.world.get("latest_version")
        host_name = self.world.get("current_host_name") or self.world.get("current_host") or "-"
        member_count = self.world.get("member_count", 0)

        colors = {
            "sleeping": "#5ad1a0",
            "starting": "#f0b35f",
            "hosting": "#4f9cf7",
            "syncing": "#a78bfa",
            "stopping": "#f0b35f",
            "error": "#f26d6d",
        }
        color = colors.get(status, "#9aa1b5")
        self.status_label.setText(f"● {status.upper()}")
        self.status_label.setStyleSheet(f"color: {color}; font-weight: 600;")

        version_text = f"v{latest}" if latest is not None else "no saves yet"
        if self.am_host and status in ("hosting", "starting", "syncing", "stopping"):
            host_text = "YOU"
        else:
            host_text = host_name
        self.meta_label.setText(
            f"{member_count} member{'s' if member_count != 1 else ''}  ·  World {version_text}"
            f"  ·  Host: {host_text}"
        )

        self._update_action()

    def _update_action(self) -> None:
        status = str(self.world.get("status", "sleeping"))
        try:
            self.action_button.clicked.disconnect()
        except RuntimeError:
            pass

        if status == "sleeping":
            self.action_button.setText("▶ PLAY")
            self.action_button.clicked.connect(lambda: self.on_play(self.world))
        elif self.am_host and status in ("hosting", "starting", "syncing", "stopping"):
            self.action_button.setText("■ STOP SERVER")
            self.action_button.clicked.connect(lambda: self.on_stop(self.world))
        else:
            self.action_button.setText("⇥ JOIN")
            self.action_button.clicked.connect(lambda: self.on_join(self.world))
