"""R2/object-storage settings dialog for the launcher.

Fields: account id, access key, secret key, bucket, player name (published in
the lease as the host identity) and an optional public address (filled in later
for port forwarding; stored in the lease so friends see where to connect).
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QVBoxLayout,
    QWidget,
)

from launcher.config import LauncherSettings, save_settings_file


class SettingsDialog(QDialog):
    """First-run/global R2 settings."""

    def __init__(self, settings: LauncherSettings, _world: dict | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.settings = settings
        self.setWindowTitle("R2 World Storage")
        self.setMinimumWidth(520)

        root = QVBoxLayout(self)
        intro = QLabel(
            "Worlds and the host lease live in a Cloudflare R2 bucket. "
            "Create one at dash.cloudflare.com (R2 → Create bucket), then add an "
            "API token with Object Read & Write for that bucket and enter the "
            "details below."
        )
        intro.setWordWrap(True)
        intro.setStyleSheet("color: #9aa1b5;")
        root.addWidget(intro)

        form = QFormLayout()
        self.account_id = QLineEdit(settings.r2_account_id)
        self.access_key = QLineEdit(settings.r2_access_key)
        self.secret_key = QLineEdit(settings.r2_secret_key)
        self.secret_key.setEchoMode(QLineEdit.Password)
        self.bucket = QLineEdit(settings.r2_bucket)
        self.player_name = QLineEdit(settings.player_name)
        self.public_address = QLineEdit(settings.public_address)
        self.public_address.setPlaceholderText("e.g. 203.0.113.5:25565 (optional — for later)")

        form.addRow("Account ID", self.account_id)
        form.addRow("Access Key ID", self.access_key)
        form.addRow("Secret Access Key", self.secret_key)
        form.addRow("Bucket", self.bucket)
        form.addRow("Your name (shown as host)", self.player_name)
        form.addRow("Public address", self.public_address)
        root.addLayout(form)

        hint = QLabel("All fields except Public address are required to host or join.")
        hint.setStyleSheet("color: #9aa1b5; font-size: 12px;")
        root.addWidget(hint)

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def _save(self) -> None:
        self.settings.r2_account_id = self.account_id.text().strip()
        self.settings.r2_access_key = self.access_key.text().strip()
        self.settings.r2_secret_key = self.secret_key.text().strip()
        self.settings.r2_bucket = self.bucket.text().strip()
        if self.player_name.text().strip():
            self.settings.player_name = self.player_name.text().strip()
        self.settings.public_address = self.public_address.text().strip()
        save_settings_file(self.settings)
        self.accept()
