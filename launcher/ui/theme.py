"""Design tokens and the application theme.

Every colour, radius and spacing value lives here, so the UI is consistent
instead of repeating hex literals in each widget. The launcher is a dark,
desktop-app surface: an explicit dark palette (rather than relying on whatever
Qt theme the OS happens to use) so it looks the same everywhere.
"""

from __future__ import annotations

from PySide6.QtGui import QColor, QPalette

# --- colour tokens ---------------------------------------------------------

BG_WINDOW = "#14161f"
BG_CARD = "#1e2130"
BG_ELEVATED = "#232738"
BG_INPUT = "#1a1d29"

BORDER = "#33384d"
BORDER_STRONG = "#3a4060"

TEXT = "#e8eaf0"
TEXT_MUTED = "#9aa1b5"
TEXT_FAINT = "#6b7280"

ACCENT = "#4f9cf7"
ACCENT_HOVER = "#6aacf8"

BUTTON_BG = "#2b3043"
BUTTON_HOVER = "#363c55"

# Status palette. One colour per state so users can read a card at a glance.
STATUS_OK = "#5ad1a0"
STATUS_BUSY = "#f0b35f"
STATUS_HOSTING = "#4f9cf7"
STATUS_ERROR = "#f26d6d"
STATUS_OFFLINE = "#6b7280"

# --- shape / spacing tokens ------------------------------------------------

RADIUS = 8
RADIUS_SMALL = 6
SPACE = 12
SPACE_LARGE = 20
FONT_SMALL = "12px"
FONT_BODY = "14px"
FONT_TITLE = "18px"
FONT_HERO = "22px"
MONO = "monospace"


# Card states -> colour. Keys match launcher.ui.widgets.CARD_STATES.
STATUS_COLORS: dict[str, str] = {
    "ready": STATUS_OK,
    "starting": STATUS_BUSY,
    "syncing": STATUS_BUSY,
    "stopping": STATUS_BUSY,
    "hosting": STATUS_HOSTING,
    "someone-hosting": STATUS_HOSTING,
    "offline": STATUS_OFFLINE,
    "storage-unavailable": STATUS_ERROR,
    "error": STATUS_ERROR,
}


def status_color(state: str) -> str:
    return STATUS_COLORS.get(state, STATUS_OFFLINE)


def _stylesheet() -> str:
    return f"""
    QWidget {{
        background: {BG_WINDOW};
        color: {TEXT};
        font-size: {FONT_BODY};
    }}
    QLabel {{
        background: transparent;
    }}
    QScrollArea, QScrollArea > QWidget > QWidget {{
        background: transparent;
        border: none;
    }}
    QPushButton {{
        background: {BUTTON_BG};
        border: 1px solid {BORDER_STRONG};
        border-radius: {RADIUS_SMALL}px;
        padding: 8px 14px;
    }}
    QPushButton:hover {{
        background: {BUTTON_HOVER};
    }}
    QPushButton:disabled {{
        color: {TEXT_FAINT};
        border-color: {BORDER};
    }}
    QPushButton#primary {{
        background: {ACCENT};
        border: 1px solid {ACCENT};
        color: #0d1220;
        font-weight: 600;
    }}
    QPushButton#primary:hover {{
        background: {ACCENT_HOVER};
        border-color: {ACCENT_HOVER};
    }}
    QPushButton#primary:disabled {{
        background: {BUTTON_BG};
        border-color: {BORDER};
        color: {TEXT_FAINT};
    }}
    QPushButton#ghost {{
        background: transparent;
        border: none;
        color: {TEXT_MUTED};
        padding: 4px 8px;
    }}
    QPushButton#ghost:hover {{
        color: {TEXT};
    }}
    QLineEdit, QSpinBox, QComboBox {{
        background: {BG_INPUT};
        border: 1px solid {BORDER};
        border-radius: {RADIUS_SMALL}px;
        padding: 7px 10px;
        selection-background-color: {ACCENT};
    }}
    QLineEdit:focus, QComboBox:focus {{
        border-color: {ACCENT};
    }}
    QGroupBox {{
        border: 1px solid {BORDER};
        border-radius: {RADIUS}px;
        margin-top: 14px;
        padding: 14px 12px 12px 12px;
    }}
    QGroupBox::title {{
        subcontrol-origin: margin;
        left: 10px;
        padding: 0 6px;
        color: {TEXT_MUTED};
    }}
    QPlainTextEdit {{
        background: {BG_ELEVATED};
        border: 1px solid {BORDER};
        border-radius: {RADIUS_SMALL}px;
        font-family: {MONO};
        font-size: {FONT_SMALL};
    }}
    QToolTip {{
        background: {BG_ELEVATED};
        color: {TEXT};
        border: 1px solid {BORDER_STRONG};
    }}
    """


def apply_theme(app) -> None:
    """Apply the dark palette and global stylesheet to a QApplication."""
    app.setStyle("Fusion")
    palette = QPalette()
    palette.setColor(QPalette.Window, QColor(BG_WINDOW))
    palette.setColor(QPalette.WindowText, QColor(TEXT))
    palette.setColor(QPalette.Base, QColor(BG_INPUT))
    palette.setColor(QPalette.AlternateBase, QColor(BG_CARD))
    palette.setColor(QPalette.Text, QColor(TEXT))
    palette.setColor(QPalette.Button, QColor(BUTTON_BG))
    palette.setColor(QPalette.ButtonText, QColor(TEXT))
    palette.setColor(QPalette.Highlight, QColor(ACCENT))
    palette.setColor(QPalette.HighlightedText, QColor("#0d1220"))
    app.setPalette(palette)
    app.setStyleSheet(_stylesheet())


def card_style(accent_edge: str | None = None) -> str:
    """Card frame styling, optionally with a coloured left edge for state."""
    edge = f"border-left: 3px solid {accent_edge};" if accent_edge else ""
    return (
        f"WorldCard {{ background: {BG_CARD}; border: 1px solid {BORDER};"
        f" border-radius: {RADIUS}px; {edge} }}"
    )
