"""Protocol editor: create, edit, check and run protocols."""
from __future__ import annotations

import copy

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QLineEdit, QListWidget,
                               QListWidgetItem, QMessageBox, QPlainTextEdit, QPushButton, QSplitter,
                               QVBoxLayout, QWidget)

from ..core.protocols import Protocol, parse_step
from . import theme as T

HELP = """<b>One step per line.</b> Activate by saying <i>"Activate &lt;name&gt; protocol"</i>.<br>
<span style="color:#9a7048">
<b>say</b> Good morning, {title}. &nbsp;·&nbsp; <b>wait</b> 3 seconds &nbsp;·&nbsp;
<b>ask</b> give me a motivational quote<br>
Any command: open spotify · open github.com · open my downloads folder · play/pause (toggle) · stop the music ·
next track · volume up by 20 · mute · set a timer for 25 minutes · take a screenshot ·
system status · what time is it · lock the computer · close chrome<br>
Lines starting with # are comments. Shutdown/restart steps still ask you to confirm.</span>"""


class ProtocolsDialog(QDialog):
    def __init__(self, assistant, parent=None):
        super().__init__(parent)
        self.assistant = assistant
        self.store = assistant.protocols
        self.work: list[Protocol] = copy.deepcopy(self.store.protocols)
        self._current = -1
        self.setWindowTitle("JARVIS — Protocols")
        self.resize(940, 620)

        # left: list
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        title = QLabel("PROTOCOLS")
        title.setFont(T.font(9, True, spacing=2))
        self.list = QListWidget()
        self.list.setFont(T.font(10.5))
        self.list.currentRowChanged.connect(self._select)
        b_add = QPushButton("+ NEW")
        b_add.clicked.connect(self._add)
        b_dup = QPushButton("DUPLICATE")
        b_dup.clicked.connect(self._duplicate)
        b_del = QPushButton("DELETE")
        b_del.clicked.connect(self._delete)
        row = QHBoxLayout()
        for b in (b_add, b_dup, b_del):
            row.addWidget(b)
        ll.addWidget(title)
        ll.addWidget(self.list, 1)
        ll.addLayout(row)

        # right: editor
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        self.e_name = QLineEdit()
        self.e_name.setPlaceholderText("Protocol name, e.g. House Party")
        self.e_name.textEdited.connect(self._name_edited)
        self.e_desc = QLineEdit()
        self.e_desc.setPlaceholderText("What it does (optional)")
        self.e_steps = QPlainTextEdit()
        self.e_steps.setFont(T.font(10, mono=True))
        self.e_steps.setPlaceholderText("say Initiating protocol.\nopen spotify\nwait 3 seconds\nplay\nvolume up by 20")
        self.e_steps.textChanged.connect(self._check)
        self.check = QListWidget()
        self.check.setFont(T.font(8.5, mono=True))
        self.check.setMaximumHeight(150)
        help_lbl = QLabel(HELP)
        help_lbl.setWordWrap(True)
        help_lbl.setTextFormat(Qt.RichText)
        self.trigger = QLabel()
        self.trigger.setObjectName("Dim")
        b_run = QPushButton("RUN NOW")
        b_run.clicked.connect(self._run)
        top = QHBoxLayout()
        top.addWidget(self.e_name, 2)
        top.addWidget(b_run)
        rl.addLayout(top)
        rl.addWidget(self.trigger)
        rl.addWidget(self.e_desc)
        rl.addWidget(QLabel("STEPS"))
        rl.addWidget(self.e_steps, 1)
        rl.addWidget(QLabel("STEP CHECK"))
        rl.addWidget(self.check)
        rl.addWidget(help_lbl)

        split = QSplitter()
        split.addWidget(left)
        split.addWidget(right)
        split.setSizes([260, 680])

        bb = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        bb.accepted.connect(self._save)
        bb.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addWidget(split, 1)
        lay.addWidget(bb)

        self._refresh_list()
        if self.work:
            self.list.setCurrentRow(0)
        else:
            self._set_enabled(False)

    # ------------------------------------------------------------ helpers
    def _refresh_list(self, select: int | None = None):
        self.list.blockSignals(True)
        self.list.clear()
        for p in self.work:
            it = QListWidgetItem(p.name or "(unnamed)")
            it.setToolTip(p.description)
            self.list.addItem(it)
        self.list.blockSignals(False)
        if select is not None and 0 <= select < len(self.work):
            self.list.setCurrentRow(select)

    def _set_enabled(self, on: bool):
        for w in (self.e_name, self.e_desc, self.e_steps):
            w.setEnabled(on)

    def _commit(self):
        if 0 <= self._current < len(self.work):
            p = self.work[self._current]
            p.name = self.e_name.text().strip()
            p.description = self.e_desc.text().strip()
            p.steps = [ln.rstrip() for ln in self.e_steps.toPlainText().splitlines() if ln.strip()]

    def _select(self, row: int):
        self._commit()
        self._current = row
        if not (0 <= row < len(self.work)):
            self._set_enabled(False)
            return
        self._set_enabled(True)
        p = self.work[row]
        self.e_name.setText(p.name)
        self.e_desc.setText(p.description)
        self.e_steps.setPlainText("\n".join(p.steps))
        self._update_trigger()

    def _name_edited(self, text: str):
        if 0 <= self._current < len(self.work):
            item = self.list.item(self._current)
            if item:
                item.setText(text.strip() or "(unnamed)")
        self._update_trigger()

    def _update_trigger(self):
        n = self.e_name.text().strip() or "…"
        self.trigger.setText(f'Say: “Activate {n} protocol”  ·  also: initiate / engage / execute / run')

    def _check(self):
        self.check.clear()
        ok = bad = 0
        for line in self.e_steps.toPlainText().splitlines():
            if not line.strip() or line.strip().startswith("#"):
                continue
            st = parse_step(line)
            if st.kind == "invalid":
                bad += 1
                it = QListWidgetItem(f"NO   {line.strip()}   - {st.note}")
                it.setForeground(QColor(T.RED))
            else:
                ok += 1
                detail = {"say": "speak", "wait": f"wait {st.payload}s", "ask": "ask AI"}.get(st.kind, st.note)
                it = QListWidgetItem(f"OK   {line.strip()}   - {detail}")
                it.setForeground(QColor(T.GOLD))
            self.check.addItem(it)

    # ------------------------------------------------------------ actions
    def _add(self):
        self._commit()
        base, n = "New", 1
        names = {p.name.lower() for p in self.work}
        while f"{base} {n}".lower() in names:
            n += 1
        self.work.append(Protocol(f"{base} {n}", "", ["say Initiating protocol."]))
        self._refresh_list(len(self.work) - 1)
        self.e_name.setFocus()
        self.e_name.selectAll()

    def _duplicate(self):
        self._commit()
        if 0 <= self._current < len(self.work):
            p = copy.deepcopy(self.work[self._current])
            p.name += " copy"
            self.work.append(p)
            self._refresh_list(len(self.work) - 1)

    def _delete(self):
        if not (0 <= self._current < len(self.work)):
            return
        name = self.work[self._current].name
        if QMessageBox.question(self, "Delete protocol", f"Delete the {name} protocol?") != QMessageBox.Yes:
            return
        del self.work[self._current]
        self._current = -1
        self._refresh_list(min(len(self.work) - 1, max(0, self.list.currentRow())))
        if not self.work:
            self.e_name.clear()
            self.e_desc.clear()
            self.e_steps.clear()
            self._set_enabled(False)

    def _validate(self) -> bool:
        self._commit()
        seen = set()
        for p in self.work:
            if not p.name:
                QMessageBox.warning(self, "Protocols", "Every protocol needs a name.")
                return False
            key = p.name.lower()
            if key in seen:
                QMessageBox.warning(self, "Protocols", f"There are two protocols called “{p.name}”.")
                return False
            seen.add(key)
        return True

    def _persist(self):
        self.store.protocols = copy.deepcopy(self.work)
        self.store.save()

    def _run(self):
        if not self._validate() or not (0 <= self._current < len(self.work)):
            return
        self._persist()
        self.assistant.submit(f"activate {self.work[self._current].name} protocol", "text")

    def _save(self):
        if self._validate():
            self._persist()
            self.accept()
