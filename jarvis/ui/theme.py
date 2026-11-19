"""Colours, fonts and the global Qt stylesheet.

Two palettes: "jarvis" (amber/gold) and "atom" (blue). ``set_palette`` swaps the
module-level colour names in place; custom-painted widgets read them at paint
time, so re-applying the stylesheet and repainting re-themes the whole app.
"""
from PySide6.QtGui import QColor, QFont, QFontDatabase

PALETTES = {
    "jarvis": dict(
        AMBER="#ffa43a", GOLD="#ffc46b", HOT="#ffe2b0", DIM="#9a7048", FAINT="#5a3e24", RED="#ff5a3c",
        REPLY="#ffe9c8", BG="#070504", MENU_BG="#120c06", TIP_BG="#1b1208", HOVER_TEXT="#fff3df",
        PANEL_TOP=(40, 24, 10, 150), PANEL_BOT=(16, 10, 5, 170), HEADER_TOP=(30, 18, 8, 200),
        BACK_TOP=(12, 8, 5), BACK_BOT=(5, 4, 3)),
    "atom": dict(
        AMBER="#3fb4ff", GOLD="#7fd0ff", HOT="#d6f1ff", DIM="#4f7fa6", FAINT="#284562", RED="#ff6a7a",
        REPLY="#e2f4ff", BG="#03070d", MENU_BG="#06111d", TIP_BG="#0a1726", HOVER_TEXT="#effaff",
        PANEL_TOP=(8, 26, 48, 150), PANEL_BOT=(4, 12, 24, 175), HEADER_TOP=(6, 22, 42, 205),
        BACK_TOP=(5, 10, 18), BACK_BOT=(2, 4, 8)),
    "rufus": dict(
        AMBER="#3ee887", GOLD="#8af5b4", HOT="#d9ffe8", DIM="#4c8f68", FAINT="#24503a", RED="#ff6b6b",
        REPLY="#e3ffee", BG="#030a06", MENU_BG="#06140c", TIP_BG="#0a1d12", HOVER_TEXT="#f0fff6",
        PANEL_TOP=(8, 40, 22, 150), PANEL_BOT=(4, 18, 10, 175), HEADER_TOP=(6, 34, 18, 205),
        BACK_TOP=(5, 15, 9), BACK_BOT=(2, 6, 3)),
}

CURRENT = "jarvis"
# populated by set_palette()
AMBER = GOLD = HOT = DIM = FAINT = RED = REPLY = BG = MENU_BG = TIP_BG = HOVER_TEXT = ""
PANEL_TOP = PANEL_BOT = HEADER_TOP = BACK_TOP = BACK_BOT = (0, 0, 0)


def set_palette(name: str):
    global CURRENT
    CURRENT = name if name in PALETTES else "jarvis"
    globals().update(PALETTES[CURRENT])


set_palette("jarvis")


def accent_rgb() -> tuple[int, int, int]:
    c = QColor(AMBER)
    return c.red(), c.green(), c.blue()


def qc(value, alpha: int | None = None) -> QColor:
    """QColor from a palette value (hex string or tuple)."""
    c = QColor(*value) if isinstance(value, tuple) else QColor(value)
    if alpha is not None:
        c.setAlpha(alpha)
    return c


def style_links(widget):
    """Make hyperlinks in a text widget use the current accent colour."""
    from PySide6.QtGui import QPalette
    pal = widget.palette()
    pal.setColor(QPalette.Link, QColor(GOLD))
    pal.setColor(QPalette.LinkVisited, QColor(DIM))
    widget.setPalette(pal)


def recolor_links(widget):
    """Markdown import hard-codes link colours; repaint anchors in the accent colour."""
    from PySide6.QtGui import QTextCharFormat, QTextCursor
    doc = widget.document()
    fmt = QTextCharFormat()
    fmt.setForeground(QColor(GOLD))
    block = doc.begin()
    while block.isValid():
        it = block.begin()
        while not it.atEnd():
            frag = it.fragment()
            if frag.isValid() and frag.charFormat().isAnchor():
                c = QTextCursor(doc)
                c.setPosition(frag.position())
                c.setPosition(frag.position() + frag.length(), QTextCursor.KeepAnchor)
                c.mergeCharFormat(fmt)
            it += 1
        block = block.next()


def ui_family() -> str:
    fams = set(QFontDatabase.families())
    for f in ("Bahnschrift", "Segoe UI Variable Display", "Segoe UI", "DejaVu Sans"):
        if f in fams:
            return f
    return "Sans Serif"


def mono_family() -> str:
    fams = set(QFontDatabase.families())
    for f in ("Cascadia Mono", "Consolas", "DejaVu Sans Mono"):
        if f in fams:
            return f
    return "Monospace"


def font(size: float = 10, bold: bool = False, mono: bool = False, spacing: float = 0.0) -> QFont:
    f = QFont(mono_family() if mono else ui_family())
    f.setPointSizeF(size)
    f.setBold(bold)
    if spacing:
        f.setLetterSpacing(QFont.AbsoluteSpacing, spacing)
    return f


def stylesheet() -> str:
    ui = ui_family()
    mono = mono_family()
    r, g, b = accent_rgb()
    A = lambda a: f"rgba({r}, {g}, {b}, {a})"  # noqa: E731
    return f"""
    QWidget {{ background: transparent; color: {HOT}; font-family: "{ui}"; font-size: 10pt; }}
    QMainWindow, QDialog {{ background: {BG}; }}
    QToolTip {{ background: {TIP_BG}; color: {HOT}; border: 1px solid {AMBER}; padding: 4px; }}
    QLineEdit, QTextEdit, QPlainTextEdit, QSpinBox, QDoubleSpinBox, QComboBox, QDateEdit, QTimeEdit {{
        background: {A(14)}; border: 1px solid {A(70)};
        border-radius: 2px; padding: 6px 8px; color: {HOT}; selection-background-color: {AMBER};
        selection-color: #000;
    }}
    QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus, QPlainTextEdit:focus {{
        border: 1px solid {AMBER}; }}
    QLineEdit#CommandLine {{ font-size: 11pt; padding: 10px 12px; background: {A(18)}; }}
    QComboBox QAbstractItemView {{ background: {MENU_BG}; border: 1px solid {AMBER};
        selection-background-color: {AMBER}; selection-color: #000; }}
    QComboBox::drop-down {{ border: none; width: 18px; }}
    QPushButton {{
        background: {A(22)}; border: 1px solid {A(110)}; color: {GOLD};
        padding: 7px 14px; border-radius: 2px; font-weight: 600; letter-spacing: 1px;
    }}
    QPushButton:hover {{ background: {A(55)}; color: {HOVER_TEXT}; }}
    QPushButton:pressed {{ background: {A(95)}; }}
    QPushButton:checked {{ background: {A(85)}; color: #fff; border-color: {GOLD}; }}
    QPushButton:disabled {{ color: {FAINT}; border-color: {FAINT}; }}
    QPushButton#Correct {{ background: rgba(60, 200, 120, 70); border-color: #5fe0a0; color: #fff; }}
    QPushButton#Wrong {{ background: rgba(255, 90, 100, 60); border-color: {RED}; color: #fff; }}
    QCheckBox {{ spacing: 8px; }}
    QCheckBox::indicator, QListWidget::indicator {{ width: 14px; height: 14px; border: 1px solid {AMBER};
        background: transparent; }}
    QCheckBox::indicator:checked, QListWidget::indicator:checked {{ background: {AMBER}; }}
    QTabWidget::pane {{ border: 1px solid {A(60)}; top: -1px; }}
    QTabBar::tab {{ background: transparent; color: {DIM}; padding: 8px 16px; border: 1px solid transparent;
        letter-spacing: 1px; }}
    QTabBar::tab:selected {{ color: {GOLD}; border: 1px solid {A(60)}; border-bottom: none; background: {A(18)}; }}
    QScrollBar:vertical {{ background: transparent; width: 8px; margin: 0; }}
    QScrollBar::handle:vertical {{ background: {A(70)}; min-height: 30px; border-radius: 3px; }}
    QScrollBar::handle:vertical:hover {{ background: {A(140)}; }}
    QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
    QScrollBar::add-page, QScrollBar::sub-page {{ background: none; }}
    QScrollBar:horizontal {{ height: 0; }}
    QListWidget {{ background: transparent; border: none; outline: none; }}
    QListWidget::item {{ padding: 4px 2px; border-bottom: 1px solid {A(25)}; }}
    QListWidget::item:selected {{ background: {A(40)}; color: #fff; }}
    QTextBrowser {{ background: transparent; border: none; }}
    QLabel#Dim {{ color: {DIM}; }}
    QLabel#Title {{ color: {GOLD}; letter-spacing: 2px; font-weight: 600; font-size: 8.5pt; }}
    QSlider::groove:horizontal {{ height: 3px; background: {A(60)}; }}
    QSlider::handle:horizontal {{ background: {AMBER}; width: 12px; margin: -6px 0; }}
    QMenu {{ background: {MENU_BG}; border: 1px solid {AMBER}; }}
    QMenu::item {{ padding: 6px 18px; }}
    QMenu::item:selected {{ background: {AMBER}; color: #000; }}
    QGroupBox {{ border: 1px solid {A(50)}; margin-top: 14px; padding-top: 10px; }}
    QGroupBox::title {{ subcontrol-origin: margin; left: 8px; color: {GOLD}; letter-spacing: 1px; }}
    QProgressBar {{ background: {A(30)}; border: none; }}
    QProgressBar::chunk {{ background: {AMBER}; }}
    QDialogButtonBox {{ dialogbuttonbox-buttons-have-icons: 0; }}
    QStatusBar {{ color: {DIM}; font-family: "{mono}"; font-size: 8.5pt; }}

    QCalendarWidget QWidget#qt_calendar_navigationbar {{ background: {A(20)}; }}
    QCalendarWidget QToolButton {{ color: {GOLD}; background: transparent; border: none; padding: 4px 8px;
        font-weight: 600; letter-spacing: 1px; qproperty-icon: none; }}
    QCalendarWidget QToolButton:hover {{ background: {A(50)}; }}
    QCalendarWidget QToolButton#qt_calendar_prevmonth {{ qproperty-text: "<"; }}
    QCalendarWidget QToolButton#qt_calendar_nextmonth {{ qproperty-text: ">"; }}
    QCalendarWidget QMenu {{ background: {MENU_BG}; }}
    QCalendarWidget QSpinBox {{ background: {MENU_BG}; padding: 2px; }}
    QCalendarWidget QAbstractItemView {{ background: transparent; color: {HOT};
        selection-background-color: {AMBER}; selection-color: #000; outline: none; }}
    QCalendarWidget QAbstractItemView:disabled {{ color: {FAINT}; }}
    """
