"""System-tray presence and desktop notifications.

Deliberately small and defensive: some Linux desktops have no system tray at
all, so every method degrades to a no-op rather than breaking the window.
"""

from __future__ import annotations

from PySide6.QtGui import QAction, QIcon
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

from launcher.resources import icon_path


class Tray:
    """A tray icon with Show/Quit plus desktop notifications."""

    def __init__(self, window) -> None:
        self.window = window
        icon = icon_path()
        self.icon = QSystemTrayIcon(QIcon(str(icon)) if icon else QIcon(), window)
        self.icon.setToolTip("Nomad")

        menu = QMenu(window)
        show_action = QAction("Show Nomad", window)
        show_action.triggered.connect(window.show_normal)
        quit_action = QAction("Quit", window)
        quit_action.triggered.connect(window.quit_app)
        menu.addAction(show_action)
        menu.addSeparator()
        menu.addAction(quit_action)
        self.icon.setContextMenu(menu)
        self.icon.activated.connect(self._on_activated)

    @property
    def available(self) -> bool:
        return QSystemTrayIcon.isSystemTrayAvailable()

    def show(self) -> None:
        if self.available:
            self.icon.show()

    def notify(self, title: str, message: str) -> None:
        if self.available and self.icon.isVisible():
            self.icon.showMessage(title, message, QSystemTrayIcon.Information, 5000)

    def _on_activated(self, reason) -> None:
        if reason == QSystemTrayIcon.Trigger:
            self.window.show_normal()
