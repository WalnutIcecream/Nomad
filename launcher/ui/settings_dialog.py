"""Global settings: where worlds live, and who this machine is.

The wizard is the normal path; this dialog is for inspecting the current
storage and, under Advanced, editing the raw connection values directly. It
also holds the two identity fields that end up in a lease: the host name shown
to other players, and the address they connect to.
"""

from __future__ import annotations

import threading

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from launcher.config import LauncherSettings, save_settings_file
from launcher.secrets import secret_value
from launcher.storage import provider_label
from launcher.ui import theme
from launcher.ui.storage_wizard import StorageWizard


class SettingsDialog(QDialog):
    """Storage summary + advanced connection values + local identity."""

    test_finished = Signal(object)

    def __init__(self, settings: LauncherSettings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.settings = settings
        self.setWindowTitle("Nomad — Settings")
        self.setMinimumWidth(560)

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 16)
        root.setSpacing(12)

        storage = QGroupBox("Storage")
        storage_layout = QVBoxLayout(storage)
        row = QHBoxLayout()
        self.provider_label = QLabel()
        self.provider_label.setStyleSheet("font-weight: 600;")
        row.addWidget(self.provider_label)
        row.addStretch()
        setup_button = QPushButton("Set up storage…")
        setup_button.setObjectName("primary")
        setup_button.clicked.connect(self._open_wizard)
        row.addWidget(setup_button)
        test_button = QPushButton("Test connection")
        test_button.clicked.connect(self._test_connection)
        row.addWidget(test_button)
        storage_layout.addLayout(row)

        self.storage_feedback = QLabel("")
        self.storage_feedback.setWordWrap(True)
        storage_layout.addWidget(self.storage_feedback)

        self.advanced = QGroupBox("Advanced")
        self.advanced.setCheckable(True)
        self.advanced.setChecked(False)
        advanced_form = QFormLayout(self.advanced)
        self.account_id = QLineEdit()
        self.account_id.setPlaceholderText("Cloudflare account id")
        self.endpoint = QLineEdit()
        self.access_key = QLineEdit()
        self.secret_key = QLineEdit()
        self.secret_key.setEchoMode(QLineEdit.Password)
        self.bucket = QLineEdit()
        advanced_form.addRow("Account ID", self.account_id)
        advanced_form.addRow("Endpoint", self.endpoint)
        advanced_form.addRow("Access key", self.access_key)
        advanced_form.addRow("Secret key", self.secret_key)
        advanced_form.addRow("Bucket", self.bucket)
        hint = QLabel(
            "Secrets are stored in your operating system's keychain when one is "
            "available, and otherwise in a private settings file."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color: {theme.TEXT_FAINT}; font-size: {theme.FONT_SMALL};")
        advanced_form.addRow("", hint)
        storage_layout.addWidget(self.advanced)
        root.addWidget(storage)

        identity = QGroupBox("Your identity")
        identity_form = QFormLayout(identity)
        self.player_name = QLineEdit()
        self.public_address = QLineEdit()
        self.public_address.setPlaceholderText("host:port — how friends reach you")
        identity_form.addRow("Your name (host)", self.player_name)
        identity_form.addRow("Public address", self.public_address)
        root.addWidget(identity)

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

        self.test_finished.connect(self._on_test_finished)
        self._load()

    # --- data ------------------------------------------------------------

    def _load(self) -> None:
        s = self.settings
        self.provider_label.setText(provider_label(s.storage_backend))
        is_r2 = s.storage_backend == "r2"
        self.account_id.setText(s.r2_account_id if is_r2 else "")
        self.account_id.setVisible(is_r2)
        self.endpoint.setText((s.r2_endpoint_url if is_r2 else s.server_endpoint_url) or "")
        self.access_key.setText(secret_value(s.r2_access_key if is_r2 else s.server_access_key))
        self.secret_key.setText(secret_value(s.r2_secret_key if is_r2 else s.server_secret_key))
        self.bucket.setText(s.r2_bucket if is_r2 else s.server_bucket)
        self.player_name.setText(s.player_name)
        self.public_address.setText(s.public_address)

    def _apply_to(self, target: LauncherSettings) -> None:
        endpoint = self.endpoint.text().strip()
        access = self.access_key.text().strip()
        secret = self.secret_key.text().strip()
        bucket = self.bucket.text().strip()
        if target.storage_backend == "r2":
            target.r2_account_id = self.account_id.text().strip()
            target.r2_endpoint_url = endpoint
            target.r2_access_key = access
            target.r2_secret_key = secret
            target.r2_bucket = bucket
        else:
            target.server_endpoint_url = endpoint
            target.server_access_key = access
            target.server_secret_key = secret
            target.server_bucket = bucket
        if self.player_name.text().strip():
            target.player_name = self.player_name.text().strip()
        target.public_address = self.public_address.text().strip()

    # --- actions ---------------------------------------------------------

    def _open_wizard(self) -> None:
        if StorageWizard(self.settings, parent=self).exec() == StorageWizard.DialogCode.Accepted:
            self._load()
            self.storage_feedback.setText("Storage saved.")
            self.storage_feedback.setStyleSheet(f"color: {theme.STATUS_OK};")

    def _test_connection(self) -> None:
        from launcher.storage import build_store

        self.storage_feedback.setText("Checking the connection…")
        self.storage_feedback.setStyleSheet(f"color: {theme.TEXT_MUTED};")
        # Test exactly what is on screen, not what was last saved.
        candidate = self.settings.model_copy(deep=True)
        self._apply_to(candidate)

        def work() -> None:
            try:
                store = build_store(candidate)
            except Exception as exc:
                self.test_finished.emit((False, f"Nomad couldn't use these settings: {exc}"))
                return
            result = store.test_connection()
            self.test_finished.emit((result.ok, result.message))

        threading.Thread(target=work, daemon=True).start()

    def _on_test_finished(self, outcome: tuple[bool, str]) -> None:
        ok, message = outcome
        self.storage_feedback.setText(message)
        self.storage_feedback.setStyleSheet(
            f"color: {theme.STATUS_OK if ok else theme.STATUS_ERROR};"
        )

    def _save(self) -> None:
        self._apply_to(self.settings)
        save_settings_file(self.settings)
        self.accept()
