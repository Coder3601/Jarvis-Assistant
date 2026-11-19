"""A.T.O.M work & revision widgets: calendar, tasks, focus timer and the Explain workspace."""
from __future__ import annotations

import datetime as dt
import math
import random

from PySide6.QtCore import QDate, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QColor, QFont, QPainter, QPen, QTextCharFormat
from PySide6.QtWidgets import (QCalendarWidget, QComboBox, QHBoxLayout, QLabel, QLineEdit,
                               QListWidget, QListWidgetItem, QMenu, QPlainTextEdit, QPushButton, QSizePolicy,
                               QTabWidget, QTextBrowser, QVBoxLayout, QWidget)

from ..config import data_dir
from ..core.study import LEVELS, friendly_date
from . import theme as T
from .widgets import amber


def _iso(d: QDate) -> str:
    return f"{d.year():04d}-{d.month():02d}-{d.day():02d}"


# ---------------------------------------------------------------------------
class CalendarPanel(QWidget):
    changed = Signal()

    def __init__(self, memory, parent=None):
        super().__init__(parent)
        self.memory = memory
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        self.cal = QCalendarWidget()
        self.cal.setGridVisible(False)
        self.cal.setVerticalHeaderFormat(QCalendarWidget.NoVerticalHeader)
        self.cal.setHorizontalHeaderFormat(QCalendarWidget.ShortDayNames)
        self.cal.setFirstDayOfWeek(Qt.Monday)
        self.cal.setMinimumHeight(220)
        self.cal.selectionChanged.connect(self.refresh_day)
        self.cal.currentPageChanged.connect(lambda *_: self.mark_month())
        lay.addWidget(self.cal)

        self.day_label = QLabel()
        self.day_label.setObjectName("Title")
        lay.addWidget(self.day_label)
        self.events = QListWidget()
        self.events.setFont(T.font(9.5))
        self.events.setContextMenuPolicy(Qt.CustomContextMenu)
        self.events.customContextMenuRequested.connect(self._menu)
        self.events.setToolTip("Right-click an event to delete it")
        lay.addWidget(self.events, 1)

        row = QHBoxLayout()
        self.e_time = QLineEdit()
        self.e_time.setPlaceholderText("HH:MM")
        self.e_time.setFixedWidth(70)
        self.e_title = QLineEdit()
        self.e_title.setPlaceholderText("Add an event, exam or deadline…")
        self.e_title.returnPressed.connect(self._add)
        b = QPushButton("ADD")
        b.clicked.connect(self._add)
        row.addWidget(self.e_time)
        row.addWidget(self.e_title, 1)
        row.addWidget(b)
        lay.addLayout(row)
        self.restyle()
        self.refresh_day()

    def restyle(self):
        fmt = QTextCharFormat()
        fmt.setForeground(QColor(T.GOLD))
        fmt.setBackground(T.qc(T.PANEL_BOT, 255))
        fmt.setFont(T.font(8, True))
        self.cal.setHeaderTextFormat(fmt)
        wk = QTextCharFormat()
        wk.setForeground(QColor(T.HOT))
        for d in (Qt.Saturday, Qt.Sunday):
            self.cal.setWeekdayTextFormat(d, wk)
        self.mark_month()

    def mark_month(self):
        y, m = self.cal.yearShown(), self.cal.monthShown()
        self.cal.setDateTextFormat(QDate(), QTextCharFormat())  # clear all
        start = QDate(y, m, 1).addDays(-7)
        end = QDate(y, m, 1).addMonths(1).addDays(7)
        fmt = QTextCharFormat()
        fmt.setForeground(QColor(T.GOLD))
        fmt.setFontWeight(QFont.Bold)
        fmt.setFontUnderline(True)
        for iso in self.memory.event_dates(_iso(start), _iso(end)):
            d = QDate.fromString(iso, "yyyy-MM-dd")
            self.cal.setDateTextFormat(d, fmt)
        today = QTextCharFormat(fmt)
        today.setBackground(amber(60))
        today.setFontUnderline(False)
        self.cal.setDateTextFormat(QDate.currentDate(), today)

    def refresh_day(self):
        d = self.cal.selectedDate()
        iso = _iso(d)
        self.day_label.setText(friendly_date(iso).upper() + "  ·  " + d.toString("d MMM yyyy").upper())
        self.events.clear()
        evs = self.memory.events_on(iso)
        for e in evs:
            it = QListWidgetItem(f"{e['time'] or 'ALL DAY':>7}   {e['title']}")
            it.setData(Qt.UserRole, e["id"])
            self.events.addItem(it)
        if not evs:
            it = QListWidgetItem("Nothing scheduled.")
            it.setFlags(Qt.NoItemFlags)
            it.setForeground(QColor(T.DIM))
            self.events.addItem(it)

    def refresh(self):
        self.mark_month()
        self.refresh_day()

    def _add(self):
        title = self.e_title.text().strip()
        if not title:
            return
        tm = self.e_time.text().strip()
        if tm and len(tm) <= 2 and tm.isdigit():
            tm = f"{int(tm):02d}:00"
        self.memory.add_event(_iso(self.cal.selectedDate()), title, tm)
        self.e_title.clear()
        self.e_time.clear()
        self.refresh()
        self.changed.emit()

    def _menu(self, pos):
        it = self.events.itemAt(pos)
        if not it or it.data(Qt.UserRole) is None:
            return
        m = QMenu(self)
        a = QAction("Delete event", m)
        a.triggered.connect(lambda: (self.memory.delete_event(it.data(Qt.UserRole)), self.refresh(),
                                     self.changed.emit()))
        m.addAction(a)
        m.exec(self.events.mapToGlobal(pos))


# ---------------------------------------------------------------------------
class TasksPanel(QWidget):
    def __init__(self, memory, parent=None):
        super().__init__(parent)
        self.memory = memory
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        self.list = QListWidget()
        self.list.setFont(T.font(9.5))
        self.list.itemChanged.connect(self._toggled)
        self.list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._menu)
        lay.addWidget(self.list, 1)
        row = QHBoxLayout()
        self.e = QLineEdit()
        self.e.setPlaceholderText("Add a task…")
        self.e.returnPressed.connect(self._add)
        b = QPushButton("ADD")
        b.clicked.connect(self._add)
        clr = QPushButton("CLEAR DONE")
        clr.clicked.connect(lambda: (self.memory.clear_done_tasks(), self.refresh()))
        row.addWidget(self.e, 1)
        row.addWidget(b)
        row.addWidget(clr)
        lay.addLayout(row)
        self.refresh()

    def refresh(self):
        self.list.blockSignals(True)
        self.list.clear()
        for t in self.memory.tasks():
            label = t["title"] + (f"   · due {friendly_date(t['due'])}" if t["due"] else "")
            it = QListWidgetItem(label)
            it.setData(Qt.UserRole, t["id"])
            it.setFlags(Qt.ItemIsEnabled | Qt.ItemIsUserCheckable | Qt.ItemIsSelectable)
            it.setCheckState(Qt.Checked if t["done"] else Qt.Unchecked)
            if t["done"]:
                f = it.font()
                f.setStrikeOut(True)
                it.setFont(f)
                it.setForeground(QColor(T.DIM))
            elif t["due"] and t["due"] <= dt.date.today().isoformat():
                it.setForeground(QColor(T.RED))
            self.list.addItem(it)
        self.list.blockSignals(False)

    def _toggled(self, it):
        self.memory.set_task_done(it.data(Qt.UserRole), it.checkState() == Qt.Checked)
        QTimer.singleShot(0, self.refresh)

    def _add(self):
        t = self.e.text().strip()
        if t:
            self.memory.add_task(t)
            self.e.clear()
            self.refresh()

    def _menu(self, pos):
        it = self.list.itemAt(pos)
        if not it:
            return
        m = QMenu(self)
        a = QAction("Delete task", m)
        a.triggered.connect(lambda: (self.memory.delete_task(it.data(Qt.UserRole)), self.refresh()))
        m.addAction(a)
        m.exec(self.list.mapToGlobal(pos))


# ---------------------------------------------------------------------------
class FocusTimer(QWidget):
    """Pomodoro-style focus / break timer with a circular readout."""

    finished = Signal(str)   # "focus" | "break"

    def __init__(self, cfg, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.mode = "focus"
        self.total = cfg.focus_minutes * 60
        self.left = self.total
        self.running = False
        self.sessions = 0
        self.setMinimumHeight(150)
        self._tick = QTimer(self)
        self._tick.timeout.connect(self._step)
        self._tick.start(1000)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(150, 4, 0, 4)
        col = QVBoxLayout()
        self.b_start = QPushButton("START")
        self.b_start.clicked.connect(self.toggle)
        self.b_mode = QPushButton("BREAK")
        self.b_mode.clicked.connect(self.switch_mode)
        b_reset = QPushButton("RESET")
        b_reset.clicked.connect(self.reset)
        for b in (self.b_start, self.b_mode, b_reset):
            col.addWidget(b)
        col.addStretch(1)
        lay.addLayout(col)

    def start(self, minutes: int | None = None, mode: str = "focus"):
        self.mode = mode
        self.total = int((minutes or (self.cfg.focus_minutes if mode == "focus" else self.cfg.break_minutes)) * 60)
        self.left = self.total
        self.running = True
        self._sync()

    def toggle(self):
        if self.left <= 0:
            self.reset()
        self.running = not self.running
        self._sync()

    def reset(self):
        self.running = False
        self.total = (self.cfg.focus_minutes if self.mode == "focus" else self.cfg.break_minutes) * 60
        self.left = self.total
        self._sync()

    def switch_mode(self):
        self.mode = "break" if self.mode == "focus" else "focus"
        self.reset()

    def _sync(self):
        self.b_start.setText("PAUSE" if self.running else ("START" if self.left == self.total else "RESUME"))
        self.b_mode.setText("FOCUS" if self.mode == "break" else "BREAK")
        self.update()

    def _step(self):
        if not self.running:
            return
        self.left -= 1
        if self.left <= 0:
            self.left = 0
            self.running = False
            done = self.mode
            if done == "focus":
                self.sessions += 1
            self.mode = "break" if done == "focus" else "focus"
            self.total = (self.cfg.focus_minutes if self.mode == "focus" else self.cfg.break_minutes) * 60
            self.left = self.total
            self.finished.emit(done)
        self._sync()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        R = 62
        c = QPointF(8 + R + 6, self.height() / 2)
        rect = QRectF(c.x() - R, c.y() - R, 2 * R, 2 * R)
        p.setPen(QPen(amber(40), 7, Qt.SolidLine, Qt.FlatCap))
        p.drawEllipse(rect)
        frac = self.left / max(1, self.total)
        p.setPen(QPen(QColor(T.AMBER), 7, Qt.SolidLine, Qt.FlatCap))
        p.drawArc(rect, 90 * 16, int(360 * 16 * frac))
        p.setPen(QPen(amber(80), 1))
        for i in range(60):
            a = math.radians(i * 6)
            r0 = R + 8 if i % 5 else R + 5
            p.drawLine(QPointF(c.x() + math.cos(a) * r0, c.y() + math.sin(a) * r0),
                       QPointF(c.x() + math.cos(a) * (R + 11), c.y() + math.sin(a) * (R + 11)))
        m, s = divmod(max(0, self.left), 60)
        p.setPen(QColor(T.HOT))
        p.setFont(T.font(20, True))
        p.drawText(rect.adjusted(0, -10, 0, -10), Qt.AlignCenter, f"{m:02d}:{s:02d}")
        p.setFont(T.font(7.5, True, spacing=2))
        p.setPen(QColor(T.GOLD))
        p.drawText(rect.adjusted(0, 34, 0, 0), Qt.AlignCenter,
                   ("FOCUS" if self.mode == "focus" else "BREAK") + (" · PAUSED" if not self.running and
                                                                     self.left != self.total else ""))
        p.setPen(QColor(T.DIM))
        p.setFont(T.font(7.5, mono=True))
        p.drawText(rect.adjusted(0, 58, 0, 0), Qt.AlignCenter, f"SESSIONS {self.sessions}")
        p.end()


# ---------------------------------------------------------------------------
class FlashcardView(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.cards: list[dict] = []
        self.i = 0
        self.back = False
        self.title = ""
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.card = _CardFace(self)
        self.card.clicked.connect(self.flip)
        lay.addWidget(self.card, 1)
        row = QHBoxLayout()
        self.counter = QLabel()
        self.counter.setObjectName("Dim")
        row.addWidget(self.counter, 1)
        for text, fn in (("PREV", self.prev), ("FLIP", self.flip), ("NEXT", self.next), ("SHUFFLE", self.shuffle)):
            b = QPushButton(text)
            b.clicked.connect(fn)
            row.addWidget(b)
        lay.addLayout(row)
        self._show()

    def set_cards(self, cards, title=""):
        self.cards, self.i, self.back, self.title = list(cards), 0, False, title
        self._show()

    def flip(self):
        if self.cards:
            self.back = not self.back
            self._show()

    def next(self):
        if self.cards:
            self.i = (self.i + 1) % len(self.cards)
            self.back = False
            self._show()

    def prev(self):
        if self.cards:
            self.i = (self.i - 1) % len(self.cards)
            self.back = False
            self._show()

    def shuffle(self):
        random.shuffle(self.cards)
        self.i, self.back = 0, False
        self._show()

    def _show(self):
        if not self.cards:
            self.card.set("Ask A.T.O.M for flashcards on any topic.", False, "")
            self.counter.setText("")
            return
        c = self.cards[self.i]
        self.card.set(c["back"] if self.back else c["front"], self.back, self.title)
        self.counter.setText(f"CARD {self.i + 1} / {len(self.cards)}   ·   click the card to flip")


class _CardFace(QWidget):
    clicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.text, self.is_back, self.title = "", False, ""
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumHeight(200)

    def set(self, text, is_back, title):
        self.text, self.is_back, self.title = text, is_back, title
        self.update()

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.clicked.emit()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(20, 12, -20, -12)
        p.setBrush(amber(38 if self.is_back else 18))
        p.setPen(QPen(amber(160 if self.is_back else 90), 1.5))
        p.drawRoundedRect(r, 10, 10)
        p.setPen(QColor(T.DIM))
        p.setFont(T.font(8, True, spacing=2))
        p.drawText(r.adjusted(16, 10, -16, 0), Qt.AlignLeft | Qt.AlignTop, "ANSWER" if self.is_back else "QUESTION")
        if self.title:
            p.drawText(r.adjusted(16, 10, -16, 0), Qt.AlignRight | Qt.AlignTop, self.title.upper()[:40])
        p.setPen(QColor(T.HOT))
        p.setFont(T.font(15 if len(self.text) < 120 else 12, not self.is_back))
        p.drawText(r.adjusted(30, 30, -30, -20), Qt.AlignCenter | Qt.TextWordWrap, self.text)
        p.end()


# ---------------------------------------------------------------------------
class QuizView(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.qs: list[dict] = []
        self.i = 0
        self.score = 0
        self.answered = False
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 8, 10, 8)
        self.progress = QLabel()
        self.progress.setObjectName("Title")
        self.q = QLabel()
        self.q.setWordWrap(True)
        self.q.setFont(T.font(13, True))
        lay.addWidget(self.progress)
        lay.addWidget(self.q)
        self.opts = []
        for k in range(6):
            b = QPushButton()
            b.setStyleSheet("text-align: left; padding: 10px 14px; font-weight: 500; letter-spacing: 0px;")
            b.clicked.connect(lambda _=False, k=k: self.choose(k))
            b.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            lay.addWidget(b)
            self.opts.append(b)
        self.feedback = QLabel()
        self.feedback.setWordWrap(True)
        lay.addWidget(self.feedback)
        lay.addStretch(1)
        row = QHBoxLayout()
        row.addStretch(1)
        self.b_next = QPushButton("NEXT")
        self.b_next.clicked.connect(self.next)
        self.b_restart = QPushButton("RESTART")
        self.b_restart.clicked.connect(self.restart)
        row.addWidget(self.b_restart)
        row.addWidget(self.b_next)
        lay.addLayout(row)
        self._show()

    def set_quiz(self, qs, title=""):
        self.qs, self.title = list(qs), title
        self.restart()

    def restart(self):
        self.i, self.score, self.answered = 0, 0, False
        self._show()

    def choose(self, k):
        if self.answered or not self.qs:
            return
        self.answered = True
        q = self.qs[self.i]
        right = k == q["answer"]
        if right:
            self.score += 1
        for idx, b in enumerate(self.opts):
            b.setObjectName("Correct" if idx == q["answer"] else ("Wrong" if idx == k else ""))
            b.style().unpolish(b)
            b.style().polish(b)
        self.feedback.setText(("<b>Correct.</b> " if right else "<b>Not quite.</b> ") + q.get("explanation", ""))
        self.b_next.setEnabled(True)
        self._progress()

    def next(self):
        if not self.qs:
            return
        if self.i < len(self.qs) - 1:
            self.i += 1
            self.answered = False
            self._show()
        else:
            self._finish()

    def _progress(self):
        self.progress.setText(f"QUESTION {self.i + 1} / {len(self.qs)}   ·   SCORE {self.score}")

    def _show(self):
        for b in self.opts:
            b.setObjectName("")
            b.style().unpolish(b)
            b.style().polish(b)
            b.hide()
        self.feedback.setText("")
        if not self.qs:
            self.progress.setText("QUIZ")
            self.q.setText("Ask A.T.O.M to quiz you on any topic.")
            self.b_next.setEnabled(False)
            return
        q = self.qs[self.i]
        self._progress()
        self.q.setText(q["question"])
        for k, opt in enumerate(q["options"]):
            self.opts[k].setText(f"{'ABCDEF'[k]}.   {opt}")
            self.opts[k].show()
        self.b_next.setEnabled(False)
        self.b_next.setText("FINISH" if self.i == len(self.qs) - 1 else "NEXT")

    def _finish(self):
        for b in self.opts:
            b.hide()
        pct = round(100 * self.score / max(1, len(self.qs)))
        verdict = ("Outstanding." if pct >= 90 else "Solid work." if pct >= 70 else
                   "Getting there – review the explanations and try again." if pct >= 40 else
                   "This one needs another look. Try the Explain tab first.")
        self.progress.setText("RESULT")
        self.q.setText(f"You scored {self.score} out of {len(self.qs)} ({pct}%).")
        self.feedback.setText(verdict)
        self.b_next.setEnabled(False)


# ---------------------------------------------------------------------------
class Workspace(QWidget):
    """The Explain box plus flashcards, quiz and notes tabs."""

    request = Signal(str, str, str)    # kind, topic, level

    def __init__(self, cfg, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self._buf = ""
        self._topic = ""
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)

        ask = QLabel("WHAT DO YOU WANT EXPLAINED?")
        ask.setObjectName("Title")
        lay.addWidget(ask)
        self.input = QPlainTextEdit()
        self.input.setPlaceholderText("e.g.  How does photosynthesis work?   ·   Integration by parts   ·   "
                                      "the causes of World War One   ·   or paste notes to summarise…")
        self.input.setFixedHeight(74)
        self.input.setFont(T.font(11))
        lay.addWidget(self.input)

        row = QHBoxLayout()
        lv = QLabel("LEVEL")
        lv.setObjectName("Dim")
        self.level = QComboBox()
        self.level.addItems(LEVELS)
        self.level.setCurrentText(cfg.study_level if cfg.study_level in LEVELS else "GCSE")
        self.level.currentTextChanged.connect(self._level_changed)
        row.addWidget(lv)
        row.addWidget(self.level)
        row.addStretch(1)
        for text, kind in (("EXPLAIN", "explain"), ("SUMMARISE", "summary"), ("FLASHCARDS", "flashcards"),
                           ("QUIZ ME", "quiz")):
            b = QPushButton(text)
            b.clicked.connect(lambda _=False, k=kind: self._go(k))
            row.addWidget(b)
        lay.addLayout(row)

        self.status = QLabel()
        self.status.setObjectName("Dim")
        self.status.setFont(T.font(8.5, mono=True))
        lay.addWidget(self.status)

        self.tabs = QTabWidget()
        # explain
        ex = QWidget()
        el = QVBoxLayout(ex)
        el.setContentsMargins(10, 8, 10, 8)
        self.output = QTextBrowser()
        self.output.setOpenExternalLinks(True)
        self.output.setFont(T.font(11))
        self.output.setPlaceholderText("Explanations appear here.")
        el.addWidget(self.output, 1)
        orow = QHBoxLayout()
        orow.addStretch(1)
        b_notes = QPushButton("SAVE TO NOTES")
        b_notes.clicked.connect(self._to_notes)
        b_cards = QPushButton("MAKE FLASHCARDS FROM THIS")
        b_cards.clicked.connect(lambda: self._topic and self.request.emit("flashcards", self._topic,
                                                                          self.level.currentText()))
        orow.addWidget(b_cards)
        orow.addWidget(b_notes)
        el.addLayout(orow)
        self.tabs.addTab(ex, "EXPLAIN")
        self.cards = FlashcardView()
        self.tabs.addTab(self.cards, "FLASHCARDS")
        self.quiz = QuizView()
        self.tabs.addTab(self.quiz, "QUIZ")
        self.notes = QPlainTextEdit()
        self.notes.setFont(T.font(10.5, mono=True))
        self.notes.setPlaceholderText("Your revision notes. Saved automatically.")
        self._notes_path = data_dir() / "notes.md"
        if self._notes_path.exists():
            self.notes.setPlainText(self._notes_path.read_text(encoding="utf-8"))
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.timeout.connect(self._save_notes)
        self.notes.textChanged.connect(lambda: self._save_timer.start(800))
        self.tabs.addTab(self.notes, "NOTES")
        lay.addWidget(self.tabs, 1)

        self._render_timer = QTimer(self)
        self._render_timer.setSingleShot(True)
        self._render_timer.timeout.connect(self._render)

    # -------------------------------------------------------------- input
    def _level_changed(self, lv):
        self.cfg.study_level = lv
        self.cfg.save()

    def _go(self, kind):
        topic = self.input.toPlainText().strip()
        if not topic:
            self.status.setText("Type a topic or question first.")
            self.input.setFocus()
            return
        self.request.emit(kind, topic, self.level.currentText())

    # ------------------------------------------------------------ results
    def started(self, kind, topic):
        self._topic = topic if kind != "summary" else self.input.toPlainText().strip()[:80]
        label = {"explain": "Explaining", "summary": "Summarising", "flashcards": "Writing flashcards on",
                 "quiz": "Building a quiz on"}.get(kind, "Working on")
        self.status.setText(f"{label} {topic if kind != 'summary' else 'your text'}…")
        if kind in ("explain", "summary"):
            self._buf = ""
            self.output.clear()
            self.tabs.setCurrentIndex(0)

    def token(self, t):
        self._buf += t
        if not self._render_timer.isActive():
            self._render_timer.start(90)

    def _render(self):
        sb = self.output.verticalScrollBar()
        at_bottom = sb.value() >= sb.maximum() - 30
        T.style_links(self.output)
        self.output.setMarkdown(self._buf)
        T.recolor_links(self.output)
        if at_bottom:
            sb.setValue(sb.maximum())

    def finished(self, kind, data):
        if kind == "error":
            self.status.setText(f"Couldn't complete that: {data}")
            return
        if kind in ("explain", "summary"):
            self._buf = data or self._buf
            self._render()
            self.status.setText("Done.  Save it to your notes, or turn it into flashcards.")
        elif kind == "flashcards":
            self.cards.set_cards(data, self._topic)
            self.tabs.setCurrentWidget(self.cards)
            self.status.setText(f"{len(data)} flashcards ready.")
        elif kind == "quiz":
            self.quiz.set_quiz(data, self._topic)
            self.tabs.setCurrentWidget(self.quiz)
            self.status.setText(f"{len(data)}-question quiz ready. Good luck.")

    def append_note(self, title: str, markdown: str):
        cur = self.notes.toPlainText().rstrip()
        stamp = dt.datetime.now().strftime("%d %b %Y %H:%M")
        self.notes.setPlainText((cur + "\n\n" if cur else "") + f"## {title}  ({stamp})\n\n{markdown.strip()}\n")

    def show_topic(self, topic):
        self.input.setPlainText(topic)

    # -------------------------------------------------------------- notes
    def _to_notes(self):
        if not self._buf.strip():
            return
        stamp = dt.datetime.now().strftime("%d %b %Y %H:%M")
        cur = self.notes.toPlainText().rstrip()
        self.notes.setPlainText((cur + "\n\n" if cur else "") + f"## {self._topic}  ({stamp})\n\n{self._buf.strip()}\n")
        self.tabs.setCurrentWidget(self.notes)
        self.status.setText("Saved to notes.")

    def _save_notes(self):
        try:
            self._notes_path.write_text(self.notes.toPlainText(), encoding="utf-8")
        except OSError:
            pass
