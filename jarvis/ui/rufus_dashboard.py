"""R.U.F.U.S dashboard page: research log + sources | research console | hologram, data stream, chat."""
from __future__ import annotations

import datetime as dt
import json
import re
import webbrowser
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QAction, QColor, QGuiApplication
from PySide6.QtWidgets import (QComboBox, QFileDialog, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QListWidget,
                               QListWidgetItem, QMenu, QProgressBar, QPushButton, QSizePolicy, QTextBrowser,
                               QVBoxLayout, QWidget)

from ..core.research import DEPTHS
from . import theme as T
from .command_bar import CommandBar
from .lazy_hologram import LazyHologram
from .widgets import ChatLog, HudFrame

STATUS_TAG = {"found": "FOUND ", "reading": "READ..", "read": "READ  ", "failed": "FAILED", "skipped": "SKIP  "}


class RufusDashboard(QWidget):
    to_notes = Signal(str, str)      # title, markdown

    def __init__(self, cfg, assistant, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.assistant = assistant
        self._buf = ""
        self._query = ""
        self._full = ""
        self._source_items: dict[int, QListWidgetItem] = {}
        self._sources: dict[int, object] = {}

        grid = QGridLayout(self)
        grid.setContentsMargins(14, 10, 14, 6)
        grid.setHorizontalSpacing(12)

        # ---- left: history + sources
        left = QWidget()
        left.setFixedWidth(330)
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        ll.setSpacing(10)
        hist = HudFrame("Research log", tag="HISTORY")
        self.history = QListWidget()
        self.history.setFont(T.font(9.5))
        self.history.itemClicked.connect(self._open_history)
        self.history.setContextMenuPolicy(Qt.CustomContextMenu)
        self.history.customContextMenuRequested.connect(self._history_menu)
        hist.body.addWidget(self.history)
        ll.addWidget(hist, 2)
        wl = HudFrame("Watchlist", tag="DAILY DIGEST")
        self.watchlist = QListWidget()
        self.watchlist.setFont(T.font(9.5))
        self.watchlist.setContextMenuPolicy(Qt.CustomContextMenu)
        self.watchlist.customContextMenuRequested.connect(self._watch_menu)
        self.watchlist.setToolTip("Right-click a topic to stop watching it")
        wl.body.addWidget(self.watchlist, 1)
        wrow = QHBoxLayout()
        self.watch_in = QLineEdit()
        self.watch_in.setPlaceholderText("Watch a topic…")
        self.watch_in.returnPressed.connect(self._watch_add)
        b_add = QPushButton("ADD")
        b_add.clicked.connect(self._watch_add)
        b_chk = QPushButton("CHECK NOW")
        b_chk.clicked.connect(lambda: self.assistant.run_digest(announce=True))
        for b in (b_add, b_chk):
            b.setStyleSheet("padding: 6px 8px; letter-spacing: 0px;")
        wrow.addWidget(self.watch_in, 1)
        wrow.addWidget(b_add)
        wrow.addWidget(b_chk)
        wl.body.addLayout(wrow)
        ll.addWidget(wl, 2)
        src = HudFrame("Sources", tag="DOUBLE-CLICK TO OPEN")
        self.sources = QListWidget()
        self.sources.setFont(T.font(8.5, mono=True))
        self.sources.setWordWrap(True)
        self.sources.itemDoubleClicked.connect(self._open_source)
        src.body.addWidget(self.sources)
        ll.addWidget(src, 2)

        # ---- centre: research console + command bar
        centre = QWidget()
        cl = QVBoxLayout(centre)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setSpacing(8)
        console = HudFrame("Research console", tag="LIVE WEB")
        q = QLabel("WHAT SHOULD I RESEARCH?")
        q.setObjectName("Title")
        console.body.addWidget(q)
        row = QHBoxLayout()
        self.query = QLineEdit()
        self.query.setObjectName("CommandLine")
        self.query.setPlaceholderText("e.g.  How close is commercial nuclear fusion?   ·   best budget laptops for "
                                      "students   ·   history of the Roman Empire")
        self.query.returnPressed.connect(self._go)
        self.depth = QComboBox()
        self.depth.addItems(list(DEPTHS))
        self.depth.setCurrentText(cfg.research_depth if cfg.research_depth in DEPTHS else "Standard")
        self.depth.setToolTip("Quick ≈ 4 pages · Standard ≈ 8 pages · Deep ≈ 14 pages")
        self.depth.currentTextChanged.connect(self._depth_changed)
        self.b_go = QPushButton("RESEARCH")
        self.b_go.clicked.connect(self._go)
        self.b_stop = QPushButton("STOP")
        self.b_stop.clicked.connect(self.assistant.stop_research)
        self.b_stop.setEnabled(False)
        row.addWidget(self.query, 1)
        row.addWidget(self.depth)
        row.addWidget(self.b_go)
        row.addWidget(self.b_stop)
        console.body.addLayout(row)
        self.status = QLabel("Ready. R.U.F.U.S searches the web, reads the best pages and writes a cited report.")
        self.status.setObjectName("Dim")
        self.status.setFont(T.font(8.5, mono=True))
        console.body.addWidget(self.status)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(4)
        console.body.addWidget(self.progress)
        self.report = QTextBrowser()
        self.report.setOpenExternalLinks(True)
        self.report.setFont(T.font(11))
        self.report.setPlaceholderText("Reports appear here, with numbered citations and a source list.")
        console.body.addWidget(self.report, 1)
        orow = QHBoxLayout()
        orow.addStretch(1)
        for text, fn in (("COPY", self._copy), ("EXPORT", self._export), ("SEND TO A.T.O.M NOTES", self._notes)):
            b = QPushButton(text)
            b.clicked.connect(fn)
            orow.addWidget(b)
        console.body.addLayout(orow)
        cl.addWidget(console, 1)
        self.bar = CommandBar("Ask R.U.F.U.S anything – it can search the web – or say “research …”",
                              ["hands", "voice", "jarvis", "atom", "protocols"])
        self.bar.mic.setToolTip("Talk to R.U.F.U.S (Ctrl+Space / F2)")
        cl.addWidget(self.bar)

        # ---- right: hologram, data stream, chat
        right = QWidget()
        right.setFixedWidth(390)
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(10)
        hf = HudFrame("R.U.F.U.S", tag="UPLINK")
        self.holo = LazyHologram(quality=cfg.quality, fps=cfg.fps, scene="rufus")
        self.holo.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.holo.setFixedHeight(290)
        hf.body.addWidget(self.holo)
        rl.addWidget(hf)
        ds = HudFrame("Data stream", tag="PACKETS")
        self.stream = QListWidget()
        self.stream.setFont(T.font(8, mono=True))
        ds.body.addWidget(self.stream)
        rl.addWidget(ds, 1)
        chat = HudFrame("Conversation", tag="R.U.F.U.S")
        self.chat = ChatLog(assistant_name="R.U.F.U.S")
        chat.body.addWidget(self.chat, 1)
        rl.addWidget(chat, 1)

        grid.addWidget(left, 0, 0)
        grid.addWidget(centre, 0, 1)
        grid.addWidget(right, 0, 2)
        grid.setColumnStretch(1, 1)

        self._render_timer = QTimer(self)
        self._render_timer.setSingleShot(True)
        self._render_timer.timeout.connect(self._render)

        a = assistant
        a.research_started.connect(self.on_started)
        a.research_step.connect(self.on_step)
        a.research_source.connect(self.on_source)
        a.research_token.connect(self.on_token)
        a.research_finished.connect(self.on_finished)
        a.watch_changed.connect(self.refresh_watchlist)
        a.watch_step.connect(lambda t: (self._log(t), self.status.setText(t)))
        a.watch_digest_ready.connect(self.on_digest)
        self.refresh_history()
        self.refresh_watchlist()

    # aliases used by the main window
    @property
    def mic(self):
        return self.bar.mic

    @property
    def input(self):
        return self.bar.input

    # ------------------------------------------------------------ actions
    def _depth_changed(self, d):
        self.cfg.research_depth = d
        self.cfg.save()

    def _go(self):
        q = self.query.text().strip()
        if q:
            self.assistant.start_research(q, self.depth.currentText())

    def _copy(self):
        if self._full:
            QGuiApplication.clipboard().setText(self._full)
            self.status.setText("Report copied to the clipboard.")

    def _export(self):
        if not self._full:
            return
        folder = Path.home() / "Documents" / "JARVIS Research"
        folder.mkdir(parents=True, exist_ok=True)
        slug = re.sub(r"[^a-z0-9]+", "-", self._query.lower()).strip("-")[:60] or "research"
        default = folder / f"{dt.date.today():%Y-%m-%d} {slug}.md"
        path, _ = QFileDialog.getSaveFileName(self, "Export report", str(default), "Markdown (*.md);;Text (*.txt)")
        if path:
            Path(path).write_text(self._full, encoding="utf-8")
            self.status.setText(f"Exported to {path}")

    def _notes(self):
        if self._full:
            self.to_notes.emit(self._query, self._full)
            self.status.setText("Sent to A.T.O.M's notes.")

    def _open_source(self, item):
        url = item.data(Qt.UserRole)
        if url and str(url).startswith(("http://", "https://")):
            webbrowser.open(url)

    # ----------------------------------------------------------- watchlist
    def refresh_watchlist(self):
        self.watchlist.clear()
        topics = self.assistant.memory.watch_topics()
        for t in topics:
            when = (dt.datetime.fromtimestamp(t["last_checked"]).strftime("checked %d %b %H:%M")
                    if t["last_checked"] else "not checked yet")
            it = QListWidgetItem(f"{t['topic']}   · {when}")
            it.setData(Qt.UserRole, t["topic"])
            self.watchlist.addItem(it)
        if not topics:
            it = QListWidgetItem("Nothing watched yet. Try “Rufus, watch SpaceX”.")
            it.setFlags(Qt.NoItemFlags)
            it.setForeground(QColor(T.DIM))
            self.watchlist.addItem(it)

    def _watch_add(self):
        t = self.watch_in.text().strip()
        if t:
            self.watch_in.clear()
            self.status.setText(self.assistant.watch.add(t))
            self.refresh_watchlist()

    def _watch_menu(self, pos):
        it = self.watchlist.itemAt(pos)
        if not it or it.data(Qt.UserRole) is None:
            return
        m = QMenu(self)
        a = QAction("Stop watching", m)
        a.triggered.connect(lambda: (self.assistant.watch.remove(it.data(Qt.UserRole)), self.refresh_watchlist()))
        m.addAction(a)
        m.exec(self.watchlist.mapToGlobal(pos))

    def on_digest(self, d: dict):
        self.refresh_watchlist()
        if not d.get("ok"):
            self.status.setText(f"Watchlist check failed: {d.get('error')}")
            self._log(f"!! watchlist: {d.get('error')}", T.RED)
            return
        self.refresh_history()
        self._log(f">> WATCHLIST DIGEST · {d['new']} new stories", T.GOLD)
        if not d.get("silent") or not self._full:
            self._query, self._full = "Watchlist digest", d["markdown"]
            T.style_links(self.report)
            self.report.setMarkdown(self._full)
            T.recolor_links(self.report)
            self.sources.clear()
            self._source_items.clear()
            for s in d["sources"]:
                it = QListWidgetItem(f"[{s['n']:>2}] NEWS   {s['title'][:70]}")
                it.setData(Qt.UserRole, s["url"])
                it.setToolTip(s["url"])
                self.sources.addItem(it)
            self.status.setText(f"Watchlist digest: {d['new']} new stories.")
            self.progress.setValue(100)

    # ------------------------------------------------------------ history
    def refresh_history(self):
        self.history.clear()
        rows = self.assistant.memory.research_list()
        for r in rows:
            when = dt.datetime.fromtimestamp(r["created"]).strftime("%d %b %H:%M")
            it = QListWidgetItem(f"{r['query']}\n{when} · {r['depth']}")
            it.setData(Qt.UserRole, r["id"])
            self.history.addItem(it)
        if not rows:
            it = QListWidgetItem("No research yet.")
            it.setFlags(Qt.NoItemFlags)
            it.setForeground(QColor(T.DIM))
            self.history.addItem(it)

    def _open_history(self, item):
        rid = item.data(Qt.UserRole)
        if rid is None:
            return
        r = self.assistant.memory.get_research(rid)
        if not r:
            return
        self._query, self._full = r["query"], r["markdown"]
        T.style_links(self.report)
        self.report.setMarkdown(self._full)
        T.recolor_links(self.report)
        self.query.setText(r["query"])
        self.sources.clear()
        self._source_items.clear()
        for s in json.loads(r["sources"] or "[]"):
            it = QListWidgetItem(f"[{s['n']:>2}] {STATUS_TAG.get(s['status'], '')} {s['title'][:70]}")
            it.setData(Qt.UserRole, s["url"])
            it.setToolTip(s["url"])
            self.sources.addItem(it)
        self.status.setText(f"Loaded research from the log: {r['query']}")
        self.progress.setValue(100)

    def _history_menu(self, pos):
        it = self.history.itemAt(pos)
        if not it or it.data(Qt.UserRole) is None:
            return
        m = QMenu(self)
        a = QAction("Delete from log", m)
        a.triggered.connect(lambda: (self.assistant.memory.delete_research(it.data(Qt.UserRole)),
                                     self.refresh_history()))
        m.addAction(a)
        m.exec(self.history.mapToGlobal(pos))

    # ------------------------------------------------------- live updates
    def _log(self, text: str, color: str | None = None):
        it = QListWidgetItem(f"{dt.datetime.now():%H:%M:%S}  {text}")
        if color:
            it.setForeground(QColor(color))
        self.stream.addItem(it)
        if self.stream.count() > 400:
            self.stream.takeItem(0)
        self.stream.scrollToBottom()

    def on_started(self, query, depth):
        self._query, self._buf, self._full = query, "", ""
        self.query.setText(query)
        self.report.clear()
        self.sources.clear()
        self._source_items.clear()
        self._sources.clear()
        self.progress.setValue(3)
        self.b_go.setEnabled(False)
        self.b_stop.setEnabled(True)
        self.status.setText(f"Researching “{query}” ({depth})…")
        self._log(f">> NEW RESEARCH · {depth.upper()} · {query}", T.GOLD)

    def on_step(self, text):
        self.status.setText(text)
        self._log(text)
        low = text.lower()
        if low.startswith("planning"):
            self.progress.setValue(6)
        elif low.startswith("searching"):
            m = re.search(r"\((\d+)/(\d+)\)", text)
            if m:
                self.progress.setValue(8 + int(22 * int(m.group(1)) / int(m.group(2))))
        elif low.startswith("checking wikipedia"):
            self.progress.setValue(32)
        elif low.startswith("found"):
            self.progress.setValue(36)
        elif low.startswith("read "):
            self.progress.setValue(72)
        elif low.startswith("writing"):
            self.progress.setValue(78)

    def on_source(self, s):
        self._sources[s.n] = s
        label = f"[{s.n:>2}] {STATUS_TAG.get(s.status, '')} {s.domain} · {s.title[:60]}"
        it = self._source_items.get(s.n)
        if it is None:
            it = QListWidgetItem(label)
            it.setData(Qt.UserRole, s.url)
            it.setToolTip(s.url)
            self.sources.addItem(it)
            self._source_items[s.n] = it
        else:
            it.setText(label)
        color = {"read": T.GOLD, "failed": T.RED, "skipped": T.FAINT, "reading": T.HOT}.get(s.status, T.DIM)
        it.setForeground(QColor(color))
        if s.status in ("reading", "read", "failed"):
            self._log(f"{STATUS_TAG[s.status]} {s.domain}", color)
        reading = [x for x in self._sources.values() if x.status in ("read", "failed")]
        total = len([x for x in self._sources.values() if x.status != "skipped"]) or 1
        if reading:
            self.progress.setValue(max(self.progress.value(), 36 + int(36 * len(reading) / total)))

    def on_token(self, t):
        self._buf += t
        if self.progress.value() < 97 and len(self._buf) % 200 < len(t):
            self.progress.setValue(self.progress.value() + 1)
        if not self._render_timer.isActive():
            self._render_timer.start(120)

    def _render(self):
        sb = self.report.verticalScrollBar()
        bottom = sb.value() >= sb.maximum() - 30
        T.style_links(self.report)
        self.report.setMarkdown(self._buf)
        T.recolor_links(self.report)
        if bottom:
            sb.setValue(sb.maximum())

    def on_finished(self, data: dict):
        self.b_go.setEnabled(True)
        self.b_stop.setEnabled(False)
        self._render_timer.stop()
        if not data.get("ok"):
            self.status.setText(f"Research stopped: {data.get('error', 'unknown error')}")
            self._log(f"!! {data.get('error', 'error')}", T.RED)
            self.progress.setValue(0)
            if self._buf:
                T.style_links(self.report)
                self.report.setMarkdown(self._buf)
                T.recolor_links(self.report)
            return
        self._full = data["markdown"]
        T.style_links(self.report)
        self.report.setMarkdown(self._full)
        T.recolor_links(self.report)
        self.report.verticalScrollBar().setValue(0)
        self.progress.setValue(100)
        self._log(">> REPORT COMPLETE", T.GOLD)
        self.refresh_history()

    def restyle(self):
        pass
