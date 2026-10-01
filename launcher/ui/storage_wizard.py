"""The guided storage setup wizard.

Choosing where worlds live is the one piece of setup every user must do, so it
is a wizard rather than a form: pick a provider, enter credentials, press Test
Connection, and only save once the storage has actually been proven to work.

The two paths are deliberately the only two: Cloudflare R2, and "My own server"
(an S3-compatible server you run yourself — Garage on a VPS or home box).
"""

from __future__ import annotations

import threading

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QStackedWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from launcher.config import LauncherSettings, save_settings_file
from launcher.secrets import secret_value
from launcher.ui import theme

PROVIDER_TITLES = {"r2": "Cloudflare R2", "server": "My own server"}


class ChoiceCard(QFrame):
    """A large clickable card used to pick a provider."""

    clicked = Signal()

    def __init__(self, title: str, subtitle: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("choiceCard")
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setStyleSheet(
            f"QFrame#choiceCard {{ background: {theme.BG_CARD}; border: 1px solid {theme.BORDER};"
            f" border-radius: {theme.RADIUS}px; }}"
            f"QFrame#choiceCard:hover, QFrame#choiceCard:focus"
            f" {{ border-color: {theme.ACCENT}; }}"
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(4)
        title_label = QLabel(title)
        title_label.setStyleSheet(f"font-size: 16px; font-weight: 600; color: {theme.TEXT};")
        subtitle_label = QLabel(subtitle)
        subtitle_label.setStyleSheet(f"color: {theme.TEXT_MUTED};")
        subtitle_label.setWordWrap(True)
        layout.addWidget(title_label)
        layout.addWidget(subtitle_label)

    def mousePressEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        self.clicked.emit()
        super().mousePressEvent(event)

    def keyPressEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        if event.key() in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Space):
            self.clicked.emit()
            return
        super().keyPressEvent(event)


class StorageWizard(QDialog):
    """provider -> credentials -> test -> success."""

    test_finished = Signal(object)

    def __init__(self, settings: LauncherSettings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.settings = settings
        self.provider = settings.storage_backend if settings.storage_backend in PROVIDER_TITLES else "r2"
        self._testing = False

        self.setWindowTitle("Set up storage")
        self.setMinimumWidth(560)

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 22, 24, 20)
        root.setSpacing(14)

        self.steps = QStackedWidget()
        self.steps.addWidget(self._build_provider_page())
        self.steps.addWidget(self._build_credentials_page())
        self.steps.addWidget(self._build_success_page())
        root.addWidget(self.steps, stretch=1)

        self.buttons = QHBoxLayout()
        self.buttons.addStretch(1)
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.setObjectName("ghost")
        self.cancel_button.clicked.connect(self.reject)
        self.buttons.addWidget(self.cancel_button)
        self.back_button = QPushButton("Back")
        self.back_button.clicked.connect(self._go_back)
        self.buttons.addWidget(self.back_button)
        self.next_button = QPushButton("Continue")
        self.next_button.setObjectName("primary")
        self.next_button.clicked.connect(self._go_next)
        self.buttons.addWidget(self.next_button)
        root.addLayout(self.buttons)

        self.test_finished.connect(self._on_test_finished)
        self._show_provider()

    # --- pages -----------------------------------------------------------

    def _build_provider_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        title = QLabel("NOMAD")
        title.setStyleSheet(f"font-size: {theme.FONT_HERO}; font-weight: 700; color: {theme.TEXT};")
        layout.addWidget(title)

        tagline = QLabel("Persistent worlds.\nEphemeral servers.")
        tagline.setStyleSheet(f"color: {theme.TEXT_MUTED};")
        layout.addWidget(tagline)

        question = QLabel("Where should your worlds live?")
        question.setStyleSheet("font-weight: 600; margin-top: 6px;")
        layout.addWidget(question)

        r2 = ChoiceCard("Cloudflare R2", "Easy cloud storage. Worlds are stored online so any player can host.")
        r2.clicked.connect(lambda: self._choose("r2"))
        layout.addWidget(r2)
        server = ChoiceCard(
            "My own server",
            "Use a VPS or home server running an S3-compatible service (Garage).",
        )
        server.clicked.connect(lambda: self._choose("server"))
        layout.addWidget(server)

        layout.addStretch(1)
        return page

    def _build_credentials_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        self.cred_title = QLabel()
        self.cred_title.setStyleSheet(f"font-size: {theme.FONT_TITLE}; font-weight: 700;")
        layout.addWidget(self.cred_title)

        self.cred_intro = QLabel()
        self.cred_intro.setWordWrap(True)
        self.cred_intro.setStyleSheet(f"color: {theme.TEXT_MUTED};")
        layout.addWidget(self.cred_intro)

        form = QFormLayout()
        self.account_id = QLineEdit()
        self.endpoint = QLineEdit()
        self.access_key = QLineEdit()
        self.secret_key = QLineEdit()
        self.secret_key.setEchoMode(QLineEdit.Password)
        self.bucket = QLineEdit()
        form.addRow("Account ID", self.account_id)
        form.addRow("Endpoint", self.endpoint)
        form.addRow("Access key", self.access_key)
        form.addRow("Secret key", self.secret_key)
        form.addRow("Bucket", self.bucket)
        self.form = form
        layout.addLayout(form)

        # Progressive disclosure: the custom endpoint only matters for R2 users
        # pointing at something other than the standard host.
        self.advanced_toggle = QToolButton()
        self.advanced_toggle.setText("Advanced")
        self.advanced_toggle.setCheckable(True)
        self.advanced_toggle.setArrowType(Qt.RightArrow)
        self.advanced_toggle.toggled.connect(self._toggle_advanced)
        layout.addWidget(self.advanced_toggle, alignment=Qt.AlignLeft)
        self.advanced_hint = QLabel(
            "Leave the endpoint blank to use Cloudflare's default R2 endpoint "
            "for your account."
        )
        self.advanced_hint.setWordWrap(True)
        self.advanced_hint.setStyleSheet(f"color: {theme.TEXT_FAINT}; font-size: {theme.FONT_SMALL};")
        layout.addWidget(self.advanced_hint)

        self.help_toggle = QToolButton()
        self.help_toggle.setText("How do I set this up?")
        self.help_toggle.setCheckable(True)
        self.help_toggle.setArrowType(Qt.RightArrow)
        self.help_toggle.toggled.connect(self._toggle_help)
        layout.addWidget(self.help_toggle, alignment=Qt.AlignLeft)
        self.help_body = QLabel(
            "Your server administrator needs:\n"
            "• a Linux server (a VPS or a machine at home)\n"
            "• Garage installed and its S3 API enabled\n"
            "• a bucket for the worlds (for example nomad-worlds)\n"
            "• an access key and secret for Nomad\n\n"
            "Nomad never configures the server for you — it only talks to its "
            "S3 API. Ask your administrator for the four values above."
        )
        self.help_body.setWordWrap(True)
        self.help_body.setStyleSheet(f"color: {theme.TEXT_MUTED}; font-size: {theme.FONT_SMALL};")
        layout.addWidget(self.help_body)

        layout.addStretch(1)

        self.feedback = QLabel()
        self.feedback.setWordWrap(True)
        layout.addWidget(self.feedback)
        self.details_button = QPushButton("View technical details")
        self.details_button.setObjectName("ghost")
        self.details_button.clicked.connect(self._toggle_details)
        self.details_button.setVisible(False)
        layout.addWidget(self.details_button, alignment=Qt.AlignLeft)
        self.details = QPlainTextEdit()
        self.details.setReadOnly(True)
        self.details.setVisible(False)
        self.details.setMaximumHeight(90)
        layout.addWidget(self.details)
        return page

    def _build_success_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        heading = QLabel("✓ Storage connected")
        heading.setStyleSheet(
            f"font-size: {theme.FONT_TITLE}; font-weight: 700; color: {theme.STATUS_OK};"
        )
        layout.addWidget(heading)
        self.success_provider = QLabel()
        self.success_provider.setStyleSheet("font-weight: 600;")
        layout.addWidget(self.success_provider)
        self.success_bucket = QLabel()
        self.success_bucket.setStyleSheet(f"color: {theme.TEXT_MUTED};")
        layout.addWidget(self.success_bucket)
        ready = QLabel("Your worlds are ready to use.")
        ready.setStyleSheet(f"color: {theme.TEXT_MUTED};")
        layout.addWidget(ready)
        layout.addStretch(1)
        return page

    # --- navigation ------------------------------------------------------

    def _choose(self, provider: str) -> None:
        self.provider = provider
        self._show_credentials()

    def _show_provider(self) -> None:
        self.steps.setCurrentIndex(0)
        self.back_button.setVisible(False)
        self.next_button.setVisible(False)
        self.cancel_button.setVisible(True)

    def _show_credentials(self) -> None:
        self._load_values()
        is_r2 = self.provider == "r2"
        self.cred_title.setText(
            "Connect Cloudflare R2" if is_r2 else "Connect your server"
        )
        self.cred_intro.setText(
            "Cloudflare R2 stores your worlds online so any authorised Nomad "
            "player can take over hosting."
            if is_r2
            else "Your worlds will be stored on a VPS or home server running an "
            "S3-compatible service such as Garage."
        )
        self.account_id.setVisible(is_r2)
        self._set_row_visible(self.form, self.account_id, is_r2)
        self._set_row_visible(self.form, self.endpoint, not is_r2 or self.advanced_toggle.isChecked())
        self.advanced_toggle.setVisible(is_r2)
        self.advanced_hint.setVisible(is_r2 and self.advanced_toggle.isChecked())
        self.help_toggle.setVisible(not is_r2)
        self.help_body.setVisible(not is_r2 and self.help_toggle.isChecked())
        self.endpoint.setPlaceholderText(
            "https://<account>.r2.cloudflarestorage.com" if is_r2 else "https://storage.example.com"
        )
        self.bucket.setPlaceholderText("nomad-worlds")
        self._clear_feedback()
        self.steps.setCurrentIndex(1)
        self.back_button.setVisible(True)
        self.next_button.setVisible(True)
        self.next_button.setText("Test connection")
        self.next_button.setEnabled(True)

    def _show_success(self) -> None:
        self.success_provider.setText(PROVIDER_TITLES[self.provider])
        self.success_bucket.setText(f"Bucket: {self.bucket.text().strip()}")
        self.steps.setCurrentIndex(2)
        self.back_button.setVisible(False)
        self.cancel_button.setVisible(False)
        self.next_button.setVisible(True)
        self.next_button.setText("Continue")
        self.next_button.setEnabled(True)

    def _go_back(self) -> None:
        self._show_provider()

    def _go_next(self) -> None:
        if self.steps.currentIndex() == 1:
            self._run_test()
        elif self.steps.currentIndex() == 2:
            self._finish()

    # --- form helpers ----------------------------------------------------

    def _set_row_visible(self, form: QFormLayout, field: QWidget, visible: bool) -> None:
        """Show/hide a form row (label + field) across Qt versions."""
        label = form.labelForField(field)
        if label is not None:
            label.setVisible(visible)

    def _toggle_advanced(self, checked: bool) -> None:
        self.advanced_toggle.setArrowType(Qt.DownArrow if checked else Qt.RightArrow)
        self.advanced_hint.setVisible(checked)
        self._set_row_visible(self.form, self.endpoint, checked)
        self.adjustSize()

    def _toggle_help(self, checked: bool) -> None:
        self.help_toggle.setArrowType(Qt.DownArrow if checked else Qt.RightArrow)
        self.help_body.setVisible(checked)
        self.adjustSize()

    def _toggle_details(self) -> None:
        self.details.setVisible(not self.details.isVisible())
        self.adjustSize()

    def _clear_feedback(self) -> None:
        self.feedback.setText("")
        self.details_button.setVisible(False)
        self.details.setVisible(False)
        self.details.setPlainText("")

    def _load_values(self) -> None:
        """Prefill the form from current settings (or clear it for a new setup)."""
        s = self.settings
        self.account_id.setText(s.r2_account_id)
        self.endpoint.setText(s.r2_endpoint_url if self.provider == "r2" else s.server_endpoint_url)
        self.access_key.setText(
            secret_value(s.r2_access_key) if self.provider == "r2" else secret_value(s.server_access_key)
        )
        self.secret_key.setText(
            secret_value(s.r2_secret_key) if self.provider == "r2" else secret_value(s.server_secret_key)
        )
        self.bucket.setText(s.r2_bucket if self.provider == "r2" else s.server_bucket)

    def _apply_to(self, candidate: LauncherSettings) -> None:
        bucket = self.bucket.text().strip()
        access = self.access_key.text().strip()
        secret = self.secret_key.text().strip()
        endpoint = self.endpoint.text().strip()
        candidate.storage_backend = self.provider
        if self.provider == "r2":
            candidate.r2_account_id = self.account_id.text().strip()
            candidate.r2_endpoint_url = endpoint
            candidate.r2_access_key = access
            candidate.r2_secret_key = secret
            candidate.r2_bucket = bucket
        else:
            candidate.server_endpoint_url = endpoint
            candidate.server_access_key = access
            candidate.server_secret_key = secret
            candidate.server_bucket = bucket

    def _missing_fields(self) -> list[str]:
        """Friendly names of required fields still empty."""
        missing = []
        if self.provider == "r2":
            if not self.account_id.text().strip() and not self.endpoint.text().strip():
                missing.append("Account ID")
        elif not self.endpoint.text().strip():
            missing.append("Endpoint")
        if not self.access_key.text().strip():
            missing.append("Access key")
        if not self.secret_key.text().strip():
            missing.append("Secret key")
        if not self.bucket.text().strip():
            missing.append("Bucket")
        return missing

    # --- connection test -------------------------------------------------

    def _run_test(self) -> None:
        missing = self._missing_fields()
        if missing:
            self._show_error(f"Fill in the {', '.join(missing)} before testing the connection.")
            return

        self._testing = True
        self.next_button.setEnabled(False)
        self.next_button.setText("Testing…")
        self.feedback.setText("Checking the connection…")
        self.feedback.setStyleSheet(f"color: {theme.TEXT_MUTED};")
        self.details_button.setVisible(False)

        candidate = self.settings.model_copy(deep=True)
        self._apply_to(candidate)

        def work() -> None:
            from launcher.storage import build_store

            try:
                store = build_store(candidate)
            except Exception as exc:
                self.test_finished.emit(
                    (False, f"Nomad couldn't use these settings: {exc}", str(exc))
                )
                return
            result = store.test_connection()
            self.test_finished.emit((result.ok, result.message, result.detail))

        threading.Thread(target=work, daemon=True).start()

    def _on_test_finished(self, outcome: tuple[bool, str, str]) -> None:
        self._testing = False
        self.next_button.setEnabled(True)
        self.next_button.setText("Test connection")
        ok, message, detail = outcome
        if ok:
            self.feedback.setText(message)
            self.feedback.setStyleSheet(f"color: {theme.STATUS_OK}; font-weight: 600;")
            self._show_success()
            return
        self._show_error(message, detail)

    def _show_error(self, message: str, detail: str = "") -> None:
        self.feedback.setText(message)
        self.feedback.setStyleSheet(f"color: {theme.STATUS_ERROR};")
        self.details_button.setVisible(bool(detail))
        self.details.setPlainText(detail)
        self.details.setVisible(False)

    # --- finish ----------------------------------------------------------

    def _finish(self) -> None:
        self._apply_to(self.settings)
        save_settings_file(self.settings)
        self.accept()
