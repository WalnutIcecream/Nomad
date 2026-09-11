"""Storage settings dialog for the launcher.

Selects the storage backend (r2 / git / vps) and edits its settings, plus the
player name published in the lease and an optional public address. Values are
persisted to ``data/settings.json``.
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from launcher.config import LauncherSettings, save_settings_file
from launcher.secrets import secret_value

_BACKENDS = ("r2", "git", "vps", "ssh")


def connection_user(settings: LauncherSettings) -> str:
    target = settings.ssh_target or ""
    return target.split("@", 1)[0] if "@" in target else ""


def connection_host(settings: LauncherSettings) -> str:
    target = settings.ssh_target or ""
    return target.split("@", 1)[1] if "@" in target else target


def compose_target(user: str, host: str) -> str:
    user = user.strip()
    host = host.strip()
    if user and host:
        return f"{user}@{host}"
    return host or user


class SettingsDialog(QDialog):
    """Storage backend + identity settings."""

    def __init__(self, settings: LauncherSettings, _world: dict | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.settings = settings
        self.setWindowTitle("Nomad — Storage Settings")
        self.setMinimumWidth(560)

        root = QVBoxLayout(self)
        intro = QLabel(
            "Worlds and the host lease live in a shared store. Choose one backend. "
            "R2: create a bucket at dash.cloudflare.com. Git: a repo you can push to. "
            "VPS: your own S3-compatible server (MinIO)."
        )
        intro.setWordWrap(True)
        intro.setStyleSheet("color: #9aa1b5;")
        root.addWidget(intro)

        common = QFormLayout()
        self.backend = QComboBox()
        self.backend.addItems(_BACKENDS)
        self.backend.setCurrentText(settings.storage_backend)
        self.player_name = QLineEdit(settings.player_name)
        self.public_address = QLineEdit(settings.public_address)
        self.public_address.setPlaceholderText("host:port, optional")
        common.addRow("Backend", self.backend)
        common.addRow("Your name (host)", self.player_name)
        common.addRow("Public address", self.public_address)
        root.addLayout(common)

        # --- r2 ----------------------------------------------------------
        self.r2_group = QGroupBox("Cloudflare R2")
        r2_form = QFormLayout(self.r2_group)
        self.r2_account_id = QLineEdit(settings.r2_account_id)
        self.r2_access_key = QLineEdit(secret_value(settings.r2_access_key))
        self.r2_secret_key = QLineEdit(secret_value(settings.r2_secret_key))
        self.r2_secret_key.setEchoMode(QLineEdit.Password)
        self.r2_bucket = QLineEdit(settings.r2_bucket)
        r2_form.addRow("Account ID", self.r2_account_id)
        r2_form.addRow("Access key", self.r2_access_key)
        r2_form.addRow("Secret key", self.r2_secret_key)
        r2_form.addRow("Bucket", self.r2_bucket)
        root.addWidget(self.r2_group)

        # --- git ---------------------------------------------------------
        self.git_group = QGroupBox("Git repo")
        git_form = QFormLayout(self.git_group)
        self.git_repo_dir = QLineEdit(str(settings.git_repo_dir))
        self.git_remote_url = QLineEdit(settings.git_remote_url)
        self.git_remote_url.setPlaceholderText("optional remote URL (else local only)")
        git_form.addRow("Repo path", self.git_repo_dir)
        git_form.addRow("Remote", self.git_remote_url)
        root.addWidget(self.git_group)

        # --- vps ---------------------------------------------------------
        self.vps_group = QGroupBox("Self-hosted S3 (MinIO/Garage)")
        vps_form = QFormLayout(self.vps_group)
        self.vps_endpoint_url = QLineEdit(settings.vps_endpoint_url)
        self.vps_endpoint_url.setPlaceholderText("https://s3.example.com")
        self.vps_bucket = QLineEdit(settings.vps_bucket)
        vps_form.addRow("Endpoint", self.vps_endpoint_url)
        vps_form.addRow("Bucket", self.vps_bucket)
        vps_hint = QLabel("Credentials reuse the R2 access/secret keys above.")
        vps_hint.setStyleSheet("color: #9aa1b5; font-size: 12px;")
        vps_form.addRow("", vps_hint)
        root.addWidget(self.vps_group)

        # --- ssh ---------------------------------------------------------
        self.ssh_group = QGroupBox("Home / bare-metal server (ssh)")
        ssh_form = QFormLayout(self.ssh_group)
        # User and host are entered separately, then composed into user@host.
        self.ssh_user = QLineEdit(connection_user(settings))
        self.ssh_user.setPlaceholderText("nomad")
        self.ssh_host = QLineEdit(connection_host(settings))
        self.ssh_host.setPlaceholderText("IP address or hostname")
        self.ssh_path = QLineEdit(settings.ssh_path)
        self.ssh_key = QLineEdit(settings.ssh_key)
        self.ssh_key.setPlaceholderText("blank = use the Nomad key")
        self.ssh_tunnel = QCheckBox("Publish the game port through this host (reverse tunnel)")
        self.ssh_tunnel.setChecked(settings.ssh_reverse_tunnel)
        self.ssh_remote_port = QLineEdit(str(settings.ssh_remote_port or ""))
        self.ssh_remote_port.setPlaceholderText("same as server port")
        self.ssh_remote_host = QLineEdit(settings.ssh_remote_host)
        self.ssh_remote_host.setPlaceholderText("address friends connect to (default: ssh host)")
        ssh_form.addRow("User", self.ssh_user)
        ssh_form.addRow("Host / IP", self.ssh_host)
        self.ssh_port_field = QLineEdit(str(settings.ssh_port or ""))
        self.ssh_port_field.setPlaceholderText("22")
        ssh_form.addRow("SSH port", self.ssh_port_field)
        ssh_form.addRow("Remote path", self.ssh_path)
        ssh_form.addRow("Key file", self.ssh_key)
        ssh_form.addRow("", self.ssh_tunnel)
        ssh_form.addRow("Remote port", self.ssh_remote_port)
        ssh_form.addRow("Public host", self.ssh_remote_host)

        self.ssh_setup_button = QPushButton("Set up over SSH…")
        self.ssh_setup_button.clicked.connect(self._guided_setup)
        ssh_form.addRow("", self.ssh_setup_button)

        self.ssh_fingerprint = QLabel("")
        self.ssh_fingerprint.setStyleSheet("color: #9aa1b5; font-size: 12px;")
        ssh_form.addRow("Identity", self.ssh_fingerprint)
        self.ssh_generate_button = QPushButton("Generate Nomad key")
        self.ssh_generate_button.clicked.connect(self._generate_key)
        ssh_form.addRow("", self.ssh_generate_button)

        ssh_hint = QLabel(
            "Easiest path: enter the address and username above and press "
            "\"Set up over SSH…\". Nomad installs its key for your own account, "
            "creates the folder, and verifies it. The manual fields stay for "
            "custom layouts."
        )
        ssh_hint.setWordWrap(True)
        ssh_hint.setStyleSheet("color: #9aa1b5; font-size: 12px;")
        ssh_form.addRow("", ssh_hint)
        root.addWidget(self.ssh_group)

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

        self.backend.currentTextChanged.connect(self._sync_visibility)
        self._sync_visibility(settings.storage_backend)
        self._refresh_identity()

    def _sync_visibility(self, backend: str) -> None:
        self.r2_group.setVisible(backend == "r2")
        self.git_group.setVisible(backend == "git")
        self.vps_group.setVisible(backend == "vps")
        self.ssh_group.setVisible(backend == "ssh")
        self.adjustSize()

    def _guided_setup(self) -> None:
        """Run the wizard and fold the resolved target back into the fields."""
        from launcher.ui.ssh_setup_dialog import SshSetupDialog

        dialog = SshSetupDialog(self.settings, parent=self)
        dialog.host.setText(self.ssh_host.text().strip())
        dialog.user.setText(self.ssh_user.text().strip() or dialog.user.text())
        dialog.folder.setText(self.ssh_path.text().strip() or "nomad-worlds")
        dialog.port.setText(str(self.settings.ssh_port or ""))
        if dialog.exec() != QDialog.DialogCode.Accepted or dialog.result is None:
            return

        result = dialog.result
        user = result.target.split("@", 1)[0] if "@" in result.target else ""
        self.ssh_user.setText(user)
        self.ssh_host.setText(result.target.split("@", 1)[-1])
        self.ssh_path.setText(result.base)
        self.ssh_port_field.setText(str(result.port or ""))
        self.ssh_key.setText("")
        self.backend.setCurrentText("ssh")
        self.settings.ssh_key = ""
        self.settings.ssh_target = result.target
        self.settings.ssh_path = result.base
        self.settings.ssh_port = result.port or 0
        self._refresh_identity()
        QMessageBox.information(
            self,
            "SSH setup complete",
            f"Nomad's key is installed on {result.target} and the worlds folder "
            f"exists at {result.base}.\n\nPress Save to keep these settings.",
        )

    def _generate_key(self) -> None:
        """Create the Nomad keypair and show the public key to install."""
        from launcher.ssh_identity import (
            ensure_keypair,
            fingerprint,
            nomad_key_path,
            provision_commands,
            public_key,
        )

        try:
            key = ensure_keypair(
                nomad_key_path(self.settings),
                comment=f"nomad@{self.player_name.text().strip() or 'launcher'}",
            )
            pub = public_key(key)
        except Exception as exc:
            QMessageBox.warning(self, "Could not generate key", str(exc))
            return

        self.ssh_key.setText("")  # blank means "use the Nomad key"
        self._refresh_identity()
        user = self.ssh_user.text().strip() or "nomad"
        base = f"/home/{user}/{self.ssh_path.text().strip().strip('/')}"
        steps = "\n".join(provision_commands(user, pub, base))
        QMessageBox.information(
            self,
            "Nomad SSH key",
            f"Fingerprint: {fingerprint(key) or '(unknown)'}\n\n"
            f"Public key:\n{pub}\n\n"
            f"Run these on the box to create the '{user}' user:\n\n{steps}",
        )

    def _refresh_identity(self) -> None:
        from launcher.ssh_identity import fingerprint, nomad_key_path, resolve_key

        if self.settings.ssh_key:
            self.ssh_fingerprint.setText(f"explicit: {self.settings.ssh_key}")
            return
        if resolve_key(self.settings):
            self.ssh_fingerprint.setText(
                f"Nomad key ({fingerprint(nomad_key_path(self.settings)) or 'fingerprint unavailable'})"
            )
        else:
            self.ssh_fingerprint.setText("none yet — click Generate, or set a key file")

    def _save(self) -> None:
        self.settings.storage_backend = self.backend.currentText()
        self.settings.r2_account_id = self.r2_account_id.text().strip()
        self.settings.r2_access_key = self.r2_access_key.text().strip()
        self.settings.r2_secret_key = self.r2_secret_key.text().strip()
        self.settings.r2_bucket = self.r2_bucket.text().strip()
        if self.git_repo_dir.text().strip():
            from pathlib import Path

            self.settings.git_repo_dir = Path(self.git_repo_dir.text().strip())
        self.settings.git_remote_url = self.git_remote_url.text().strip()
        self.settings.vps_endpoint_url = self.vps_endpoint_url.text().strip()
        self.settings.vps_bucket = self.vps_bucket.text().strip()
        self.settings.ssh_target = compose_target(
            self.ssh_user.text(), self.ssh_host.text()
        )
        if self.ssh_path.text().strip():
            self.settings.ssh_path = self.ssh_path.text().strip()
        try:
            self.settings.ssh_port = int(self.ssh_port_field.text().strip() or "0")
        except ValueError:
            self.settings.ssh_port = 0
        self.settings.ssh_key = self.ssh_key.text().strip()
        self.settings.ssh_reverse_tunnel = self.ssh_tunnel.isChecked()
        self.settings.ssh_remote_port = int(self.ssh_remote_port.text().strip() or "0")
        self.settings.ssh_remote_host = self.ssh_remote_host.text().strip()
        if self.player_name.text().strip():
            self.settings.player_name = self.player_name.text().strip()
        self.settings.public_address = self.public_address.text().strip()
        save_settings_file(self.settings)
        self.accept()
