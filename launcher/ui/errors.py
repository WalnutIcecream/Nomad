"""Actionable error dialogs: what happened, plus a Copy-diagnostics button."""

from __future__ import annotations

from PySide6.QtWidgets import QApplication, QMessageBox, QWidget

from launcher.diagnostics import collect


def show_error(parent: QWidget, title: str, message: str, settings) -> None:
    """Show a warning with a one-click redacted diagnostics copy."""
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Warning)
    box.setWindowTitle(title)
    box.setText(message)
    copy_button = box.addButton("Copy diagnostics", QMessageBox.ActionRole)
    box.addButton(QMessageBox.Close)
    box.exec()
    if box.clickedButton() is copy_button:
        QApplication.clipboard().setText(collect(settings))
