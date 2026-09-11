"""Guided SSH setup wizard.

Type an address and a username; the wizard generates Nomad's key, installs it on
the machine, creates the worlds folder, proves the key works on its own, and
hands the resolved target back to the caller. The user never copies a command
unless something genuinely fails, in which case the exact command is shown with
a Copy button.

All work happens on a worker thread; the UI is only touched through signals.
"""

from __future__ import annotations

import getpass
import threading

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
)

from launcher import ssh_provision


class SshSetupDialog(QDialog):
    """Collect a target, run the setup, expose the result to the caller."""

    progress = Signal(str)
    need_password = Signal(str)
    failed = Signal(str)
    succeeded = Signal(object)

    def __init__(self, settings, parent=None) -> None:
        super().__init__(parent)
        self.settings = settings
        self.result: ssh_provision.SetupResult | None = None
        self._running = False

        self.setWindowTitle("Nomad — Set up a machine over SSH")
        self.setMinimumWidth(560)

        root = QVBoxLayout(self)
        intro = QLabel(
            "Nomad will generate a key, install it on the machine for your own "
            "account, and create the folder that holds the worlds. Nothing else "
            "needs to be set up by hand."
        )
        intro.setWordWrap(True)
        intro.setStyleSheet("color: #9aa1b5;")
        root.addWidget(intro)

        form = QFormLayout()
        self.form = form
        self.host = QLineEdit()
        self.host.setPlaceholderText("192.168.1.20 or box.example.com")
        self.user = QLineEdit(_default_user())
        self.user.setPlaceholderText("your username on that machine")
        self.port = QLineEdit()
        self.port.setPlaceholderText("22")
        self.folder = QLineEdit("nomad-worlds")
        form.addRow("Address", self.host)
        form.addRow("Username", self.user)
        form.addRow("Port", self.port)
        form.addRow("Worlds folder", self.folder)
        root.addLayout(form)

        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.Password)
        self.password.setPlaceholderText("password for this machine")
        form.addRow("Password", self.password)
        _set_row_visible(form, self.password, False)

        self.status = QPlainTextEdit()
        self.status.setReadOnly(True)
        self.status.setMaximumHeight(120)
        self.status.setVisible(False)
        root.addWidget(self.status)

        self.manual = QPlainTextEdit()
        self.manual.setReadOnly(True)
        self.manual.setMaximumHeight(110)
        self.manual.setVisible(False)
        root.addWidget(self.manual)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self.copy_button = QPushButton("Copy command")
        self.copy_button.setVisible(False)
        self.copy_button.clicked.connect(self._copy_manual)
        buttons.addWidget(self.copy_button)
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.clicked.connect(self.reject)
        buttons.addWidget(self.cancel_button)
        self.run_button = QPushButton("Set up")
        self.run_button.setDefault(True)
        self.run_button.clicked.connect(self._start)
        buttons.addWidget(self.run_button)
        root.addLayout(buttons)

        self.progress.connect(self._on_progress)
        self.need_password.connect(self._on_need_password)
        self.failed.connect(self._on_failed)
        self.succeeded.connect(self._on_succeeded)

    # --- UI helpers ---------------------------------------------------

    def _log(self, message: str) -> None:
        self.status.setVisible(True)
        self.status.appendPlainText(message)

    def _set_running(self, running: bool) -> None:
        self._running = running
        self.run_button.setEnabled(not running)
        for widget in (self.host, self.user, self.port, self.folder, self.password):
            widget.setEnabled(not running)
        self.run_button.setText("Working…" if running else "Set up")

    def _on_progress(self, message: str) -> None:
        self._log(message)

    def _on_need_password(self, message: str) -> None:
        self._set_running(False)
        self._log("This machine asked for a password.")
        _set_row_visible(self.form, self.password, True)
        self.password.setFocus()

    def _on_failed(self, message: str) -> None:
        self._set_running(False)
        self._log(f"Failed: {message}")
        self._show_manual(message)

    def _on_succeeded(self, result: object) -> None:
        self.result = result  # type: ignore[assignment]
        self._set_running(False)
        self._log(f"Done. Worlds will live in {result.base}")
        self.accept()

    # --- manual fallback ----------------------------------------------

    def _show_manual(self, message: str) -> None:
        from launcher.ssh_identity import (
            ensure_keypair,
            install_key_command,
            nomad_key_path,
            public_key,
        )

        try:
            key = ensure_keypair(
                nomad_key_path(self.settings),
                comment=f"nomad@{getattr(self.settings, 'player_name', '') or 'launcher'}",
            )
            pub = public_key(key)
        except Exception:
            return
        command = install_key_command(pub)
        self.manual.setPlainText(command)
        self.manual.setVisible(True)
        self.copy_button.setVisible(True)
        self._log("Run this on the machine yourself, then press Set up again:")
        self._log(f"  {message}")

    def _copy_manual(self) -> None:
        clipboard = QApplication.clipboard()
        if clipboard is not None:
            clipboard.setText(self.manual.toPlainText())
            self._log("Command copied to the clipboard.")

    # --- worker -------------------------------------------------------

    def _start(self) -> None:
        if self._running:
            return
        host = self.host.text().strip()
        if not host:
            self._log("Enter the address of the machine first.")
            return

        port: int | None = None
        if self.port.text().strip():
            try:
                port = int(self.port.text().strip())
            except ValueError:
                self._log("Port must be a number.")
                return

        user = self.user.text().strip()
        folder = self.folder.text().strip() or "nomad-worlds"
        password_shown = _row_visible(self.form, self.password)
        if password_shown and not self.password.text():
            self._log("Enter the password for this machine, or press Cancel.")
            return
        password = self.password.text() if password_shown else None

        self._set_running(True)
        self.manual.setVisible(False)
        self.copy_button.setVisible(False)

        def work() -> None:
            try:
                result = ssh_provision.run_setup(
                    self.settings,
                    host,
                    user,
                    folder=folder,
                    port=port,
                    password=password,
                    on_step=self.progress.emit,
                )
            except ssh_provision.NeedPassword as exc:
                self.need_password.emit(exc.detail)
            except ssh_provision.ProvisionError as exc:
                self.failed.emit(exc.detail)
            except Exception as exc:  # pragma: no cover - defensive
                self.failed.emit(str(exc))
            else:
                self.succeeded.emit(result)

        threading.Thread(target=work, daemon=True).start()


def _default_user() -> str:
    try:
        return getpass.getuser()
    except Exception:
        return ""


def _set_row_visible(form: QFormLayout, field, visible: bool) -> None:
    """Show or hide a form row across Qt versions.

    ``QFormLayout.setRowVisible`` only exists from Qt 6.4; the fallback hides
    the label and the field, which is what every supported version can do.
    """
    setter = getattr(form, "setRowVisible", None)
    if setter is not None:
        try:
            setter(field, visible)
            return
        except TypeError:
            pass
    label = form.labelForField(field)
    if label is not None:
        label.setVisible(visible)
    field.setVisible(visible)


def _row_visible(form: QFormLayout, field) -> bool:
    setter = getattr(form, "isRowVisible", None)
    if setter is not None:
        try:
            return bool(setter(field))
        except TypeError:
            pass
    return field.isVisible()


__all__ = ["SshSetupDialog"]
