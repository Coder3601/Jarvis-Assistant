"""The command bar shared by all agents: mic, input, SEND, a collapsible MENU and SETTINGS."""
from __future__ import annotations

from PySide6.QtCore import QEasingCurve, QPropertyAnimation, Signal
from PySide6.QtWidgets import QHBoxLayout, QLineEdit, QPushButton, QSizePolicy, QWidget

from .widgets import HudFrame, MicButton

# key -> (label, checkable, tooltip)
ACTIONS = {
    "hands": ("HANDS-FREE", True, "Always listen for the wake word"),
    "voice": ("VOICE", True, "Spoken replies on/off"),
    "jarvis": ("JARVIS", False, "Switch to JARVIS (general assistant)"),
    "atom": ("A.T.O.M", False, "Switch to A.T.O.M (work and revision)"),
    "rufus": ("R.U.F.U.S", False, "Switch to R.U.F.U.S (internet research)"),
    "protocols": ("PROTOCOLS", False, "Create and edit protocols (Ctrl+P)"),
    "briefing": ("BRIEFING", False, "Morning briefing: your day, tasks, watchlist and tech news"),
}

COMPACT = "padding: 7px 10px; letter-spacing: 0px;"


class CommandBar(HudFrame):
    send = Signal(str)
    talk = Signal()
    settings = Signal()
    action = Signal(str, bool)     # key, checked

    def __init__(self, placeholder: str, keys: list[str], parent=None):
        super().__init__(parent=parent)
        self.body.setContentsMargins(14, 8, 14, 8)
        row = QHBoxLayout()
        row.setSpacing(10)

        self.mic = MicButton()
        self.mic.clicked.connect(self.talk.emit)
        self.input = QLineEdit()
        self.input.setObjectName("CommandLine")
        self.input.setPlaceholderText(placeholder)
        self.input.returnPressed.connect(self._send)
        b_send = QPushButton("SEND")
        b_send.setStyleSheet(COMPACT)
        b_send.clicked.connect(self._send)

        self.b_menu = QPushButton("MENU")
        self.b_menu.setCheckable(True)
        self.b_menu.setStyleSheet(COMPACT)
        self.b_menu.setToolTip("Show / hide more controls")
        self.b_menu.toggled.connect(self._toggle_menu)

        # collapsible drawer
        self.drawer = QWidget()
        dl = QHBoxLayout(self.drawer)
        dl.setContentsMargins(0, 0, 0, 6)
        dl.setSpacing(8)
        dl.addStretch(1)
        self.buttons: dict[str, QPushButton] = {}
        for key in keys:
            label, checkable, tip = ACTIONS[key]
            b = QPushButton(label)
            b.setCheckable(checkable)
            b.setToolTip(tip)
            b.setStyleSheet(COMPACT)
            if checkable:
                b.toggled.connect(lambda on, k=key: self.action.emit(k, on))
            else:
                b.clicked.connect(lambda _=False, k=key: (self.action.emit(k, False), self.b_menu.setChecked(False)))
            dl.addWidget(b)
            self.buttons[key] = b
        # the drawer opens as a row above the bar, so it never squeezes the text box
        self.drawer.setMaximumHeight(0)
        self.drawer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._anim = QPropertyAnimation(self.drawer, b"maximumHeight", self)
        self._anim.setDuration(220)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)

        b_settings = QPushButton("SETTINGS")
        b_settings.setStyleSheet(COMPACT)
        b_settings.setToolTip("Settings (Ctrl+,)")
        b_settings.clicked.connect(self.settings.emit)

        row.addWidget(self.mic)
        row.addWidget(self.input, 1)
        row.addWidget(b_send)
        row.addWidget(self.b_menu)
        row.addWidget(b_settings)
        self.body.addWidget(self.drawer)
        self.body.addLayout(row)

    def _send(self):
        t = self.input.text().strip()
        if t:
            self.input.clear()
            self.send.emit(t)

    def _toggle_menu(self, open_: bool):
        self.b_menu.setText("CLOSE" if open_ else "MENU")
        self._anim.stop()
        self._anim.setStartValue(self.drawer.maximumHeight())
        self._anim.setEndValue(self.drawer.sizeHint().height() if open_ else 0)
        self._anim.start()

    def set_checked(self, key: str, on: bool):
        b = self.buttons.get(key)
        if b and b.isCheckable() and b.isChecked() != on:
            b.blockSignals(True)
            b.setChecked(on)
            b.blockSignals(False)
