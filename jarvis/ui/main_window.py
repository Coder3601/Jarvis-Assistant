"""The JARVIS dashboard window."""
from __future__ import annotations

import datetime as dt
import logging
import sys
import time

import psutil
from PySide6.QtCore import QRectF, Qt, QTimer
from PySide6.QtGui import QAction, QColor, QKeySequence, QLinearGradient, QPainter, QPen, QShortcut
from PySide6.QtWidgets import (QApplication, QGridLayout, QHBoxLayout, QLabel, QListWidget, QListWidgetItem,
                               QMainWindow, QMenu, QSizePolicy, QStackedWidget, QVBoxLayout, QWidget)

from .. import perf
from ..config import Config
from ..core.assistant import Assistant
from . import theme as T
from .atom_dashboard import AtomDashboard
from .command_bar import CommandBar
from .hologram.renderer import HologramWidget
from .lazy_hologram import LazyHologram
from .mini_hologram import MiniHologram
from .protocols_dialog import ProtocolsDialog
from .rufus_dashboard import RufusDashboard
from .settings_dialog import SettingsDialog
from .widgets import ArcGauge, ChatLog, HologramOverlay, HudFrame, Sparkline, StatusChip, amber

log = logging.getLogger(__name__)


class Header(QWidget):
    def __init__(self, cfg: Config, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.title = ".".join(cfg.assistant_name.upper()) + "."
        self.subtitle = "JUST A RATHER VERY INTELLIGENT SYSTEM"
        self.setFixedHeight(64)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(220, 10, 16, 10)
        lay.setSpacing(10)
        lay.addStretch(1)
        self.chip_ai = StatusChip("AI CORE")
        self.chip_voice = StatusChip("VOICE LINK")
        self.chip_mem = StatusChip("MEMORY BANK")
        self.chip_mode = StatusChip("INPUT MODE")
        for c in (self.chip_ai, self.chip_voice, self.chip_mem, self.chip_mode):
            c.setMinimumWidth(150)
            lay.addWidget(c)
        lay.addSpacing(14)
        self.clock = QLabel()
        self.clock.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.clock.setMinimumWidth(150)
        lay.addWidget(self.clock)
        self._tick()
        t = QTimer(self)
        t.timeout.connect(self._tick)
        t.start(1000)

    def _tick(self):
        now = dt.datetime.now()
        self.clock.setText(f'<div style="font-size:18pt; font-weight:600; color:{T.HOT}; letter-spacing:2px;">'
                           f'{now:%H:%M:%S}</div><div style="font-size:8pt; color:{T.DIM}; letter-spacing:2px;">'
                           f'{now:%A %d %B %Y}'.upper() + "</div>")

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        g = QLinearGradient(0, 0, 0, h)
        g.setColorAt(0, T.qc(T.HEADER_TOP))
        g.setColorAt(1, T.qc(T.HEADER_TOP, 0))
        p.fillRect(self.rect(), g)
        p.setPen(QPen(amber(90), 1))
        p.drawLine(0, h - 1, w, h - 1)
        p.setPen(QPen(amber(230), 2))
        p.drawLine(0, h - 1, 200, h - 1)
        p.setFont(T.font(20, True, spacing=7))
        p.setPen(QColor(T.HOT))
        p.drawText(QRectF(18, 6, 400, 34), Qt.AlignLeft | Qt.AlignVCenter, self.title)
        p.setFont(T.font(7.5, spacing=2.2))
        p.setPen(QColor(T.DIM))
        p.drawText(QRectF(20, 38, 460, 16), Qt.AlignLeft | Qt.AlignVCenter, self.subtitle)
        p.end()


class Backdrop(QWidget):
    """Window background: dark gradient with a faint hex/grid texture."""

    def paintEvent(self, e):
        p = QPainter(self)
        w, h = self.width(), self.height()
        g = QLinearGradient(0, 0, 0, h)
        g.setColorAt(0, T.qc(T.BACK_TOP))
        g.setColorAt(1, T.qc(T.BACK_BOT))
        p.fillRect(self.rect(), g)
        p.setPen(QPen(amber(9), 1))
        for x in range(0, w, 40):
            p.drawLine(x, 0, x, h)
        for y in range(0, h, 40):
            p.drawLine(0, y, w, y)
        p.end()


class MainWindow(QMainWindow):
    def __init__(self, cfg: Config):
        super().__init__()
        self.cfg = cfg
        self.setWindowTitle(f"{cfg.assistant_name} — Personal AI System")
        self.resize(1600, 940)
        self.setMinimumSize(1100, 700)
        self.assistant = Assistant(cfg)

        root = Backdrop()
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        self.header = Header(cfg)
        outer.addWidget(self.header)

        self.stack = QStackedWidget()
        jarvis_page = QWidget()
        grid = QGridLayout(jarvis_page)
        grid.setContentsMargins(14, 10, 14, 6)
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(10)
        self.stack.addWidget(jarvis_page)
        self.atom = AtomDashboard(cfg, self.assistant)
        self.stack.addWidget(self.atom)
        self.rufus = RufusDashboard(cfg, self.assistant)
        self.stack.addWidget(self.rufus)
        outer.addWidget(self.stack, 1)

        grid.addWidget(self._left_column(), 0, 0)
        grid.addWidget(self._center(), 0, 1)
        grid.addWidget(self._right_column(), 0, 2)
        grid.setColumnStretch(1, 1)


        self.mini = MiniHologram(cfg)
        self.mini.talk_requested.connect(self._toggle_listen)
        self.mini.restore_requested.connect(self._restore_from_mini)
        self.mini.quit_requested.connect(self._quit)
        self._restore_state = Qt.WindowMaximized if cfg.start_maximized else Qt.WindowNoState
        self._quitting = False

        self._wire()
        self._shortcuts()
        self._load_history()
        self._refresh_memory()

        self._stats_timer = QTimer(self)
        self._stats_timer.timeout.connect(self._update_stats)
        self._stats_timer.start(1000)
        psutil.cpu_percent(None)
        self._net_last = (psutil.net_io_counters(), time.time())
        self._update_stats()
        QApplication.instance().applicationStateChanged.connect(self._app_state_changed)

        self._llm_timer = QTimer(self)
        self._llm_timer.timeout.connect(self.assistant.check_llm)
        self._llm_timer.start(15000)
        self._digest_timer = QTimer(self)
        self._digest_timer.timeout.connect(self.assistant.maybe_daily_digest)
        self._digest_timer.start(10 * 60 * 1000)
        QTimer.singleShot(3 * 60 * 1000, self.assistant.maybe_daily_digest)
        self.assistant.check_llm()
        self._first_llm_status = True
        self._update_mode_chip()
        self._apply_persona(self.assistant.persona, initial=True)

    # ---------------------------------------------------------------- layout
    def _left_column(self) -> QWidget:
        col = QWidget()
        col.setFixedWidth(330)
        lay = QVBoxLayout(col)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)

        diag = HudFrame("System diagnostics", tag="LIVE")
        g = QGridLayout()
        g.setSpacing(4)
        self.g_cpu = ArcGauge("CPU")
        self.g_ram = ArcGauge("MEMORY")
        self.g_disk = ArcGauge("STORAGE")
        self.g_bat = ArcGauge("POWER")
        g.addWidget(self.g_cpu, 0, 0)
        g.addWidget(self.g_ram, 0, 1)
        g.addWidget(self.g_disk, 1, 0)
        g.addWidget(self.g_bat, 1, 1)
        diag.body.addLayout(g)
        self.cores = CoreBars()
        diag.body.addWidget(self.cores)
        lay.addWidget(diag)

        net = HudFrame("Network traffic", tag="KB/S")
        self.spark = Sparkline(("DOWN", "UP"))
        net.body.addWidget(self.spark)
        self.sys_info = QLabel()
        self.sys_info.setFont(T.font(8, mono=True))
        self.sys_info.setObjectName("Dim")
        net.body.addWidget(self.sys_info)
        lay.addWidget(net)

        mem = HudFrame("Memory bank", tag="LONG-TERM")
        self.mem_list = QListWidget()
        self.mem_list.setFont(T.font(9))
        self.mem_list.setWordWrap(True)
        self.mem_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.mem_list.customContextMenuRequested.connect(self._memory_menu)
        self.mem_list.setToolTip("Right-click a memory to delete it")
        mem.body.addWidget(self.mem_list, 1)
        self.mem_stats = QLabel()
        self.mem_stats.setObjectName("Dim")
        self.mem_stats.setFont(T.font(8, mono=True))
        mem.body.addWidget(self.mem_stats)
        lay.addWidget(mem, 1)
        return col

    def _center(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)

        holo_box = QWidget()
        hg = QGridLayout(holo_box)
        hg.setContentsMargins(0, 0, 0, 0)
        self.holo = LazyHologram(quality=self.cfg.quality, fps=self.cfg.fps, scene="jarvis")
        self.holo.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.overlay = HologramOverlay()
        self.overlay.model = self.cfg.model
        hg.addWidget(self.holo, 0, 0)
        hg.addWidget(self.overlay, 0, 0)
        self.overlay.raise_()
        lay.addWidget(holo_box, 1)

        self.jbar = CommandBar(f"Speak or type a command for {self.cfg.assistant_name}…",
                               ["hands", "voice", "briefing", "atom", "rufus", "protocols"])
        self.mic = self.jbar.mic
        self.input = self.jbar.input
        lay.addWidget(self.jbar)
        return w

    def _right_column(self) -> QWidget:
        col = QWidget()
        col.setFixedWidth(390)
        lay = QVBoxLayout(col)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)
        chat = HudFrame("Communication log", tag="ENCRYPTED // LOCAL")
        self.chat = ChatLog(assistant_name=self.cfg.assistant_name)
        chat.body.addWidget(self.chat, 1)
        lay.addWidget(chat, 3)

        act = HudFrame("Activity feed", tag="EVENTS")
        self.activity = QListWidget()
        self.activity.setFont(T.font(8.5, mono=True))
        act.body.addWidget(self.activity, 1)
        lay.addWidget(act, 1)
        return col

    # ----------------------------------------------------------------- wiring
    def _wire(self):
        a = self.assistant
        a.state_changed.connect(self._on_state)
        a.user_message.connect(lambda t, src: self._chat().add_user(t, src))
        a.reply_started.connect(lambda: self._chat().begin_reply())
        a.reply_token.connect(lambda t: self._chat().add_token(t))
        a.reply_finished.connect(lambda _t: self._chat().end_reply())
        a.persona_changed.connect(self._apply_persona)
        a.briefing_ready.connect(lambda secs: self._chat().add_card("Morning briefing", secs))
        a.work_started.connect(self.atom.workspace.started)
        a.work_token.connect(self.atom.workspace.token)
        a.work_finished.connect(self.atom.workspace.finished)
        a.work_started.connect(lambda k, t: self.atom.workspace.show_topic(t) if k != "summary" else None)
        a.work_changed.connect(self.atom.refresh)
        a.study_timer.connect(lambda m, mode: self.atom.timer.start(m, mode))
        self.atom.timer.finished.connect(self._timer_finished)
        self.rufus.to_notes.connect(self.atom.workspace.append_note)
        for bar in self._bars():
            bar.send.connect(lambda t: self.assistant.submit(t, "text"))
            bar.talk.connect(self._toggle_listen)
            bar.settings.connect(self._open_settings)
            bar.action.connect(self._bar_action)
            a.listening_changed.connect(bar.mic.set_active)
        a.listening_changed.connect(self.mini.set_listening)
        self._sync_bars()
        a.system_note.connect(self._on_note)
        a.mic_level.connect(self._on_mic_level)
        a.speech_level.connect(self._on_speech_level)
        a.status.connect(self._on_status)
        a.memory_changed.connect(self._refresh_memory)
        a.llm_status.connect(self._on_llm_status)
        self.holo.fps_changed.connect(lambda f: setattr(self.overlay, "fps", f))

    def _shortcuts(self):
        for seq, fn in (("Ctrl+Space", self._toggle_listen), ("F2", self._toggle_listen),
                        ("Escape", self._interrupt), ("Ctrl+,", self._open_settings),
                        ("F11", self._toggle_fullscreen), ("Ctrl+N", self._new_session),
                        ("Ctrl+L", lambda: self.input.setFocus()), ("Ctrl+P", self._open_protocols),
                        ("Ctrl+M", self.showMinimized),
                        ("Ctrl+J", lambda: self.assistant.submit("switch to " + {
                            "jarvis": "atom", "atom": "rufus", "rufus": "jarvis"}[self.assistant.persona], "text"))):
            sc = QShortcut(QKeySequence(seq), self)
            sc.setContext(Qt.WindowShortcut)
            sc.activated.connect(fn)

    # ---------------------------------------------------------------- events
    def _bars(self) -> list[CommandBar]:
        return [self.jbar, self.atom.bar, self.rufus.bar]

    def _holos(self) -> dict[str, HologramWidget]:
        return {"jarvis": self.holo, "atom": self.atom.holo, "rufus": self.rufus.holo}

    def _chat(self) -> ChatLog:
        return {"atom": self.atom.chat, "rufus": self.rufus.chat}.get(self.assistant.persona, self.chat)

    def _sync_bars(self):
        for bar in self._bars():
            bar.set_checked("hands", bool(self.cfg.wake_word_enabled))
            bar.set_checked("voice", bool(self.cfg.tts_enabled))

    def _bar_action(self, key: str, checked: bool):
        if key == "hands":
            self._toggle_hands_free(checked)
        elif key == "voice":
            self._toggle_tts(checked)
        elif key == "protocols":
            self._open_protocols()
        elif key == "briefing":
            self.assistant.morning_briefing()
        elif key in ("jarvis", "atom", "rufus"):
            self.assistant.submit(f"switch to {key}", "text")
        self._sync_bars()

    def _on_state(self, s: str):
        for h in self._holos().values():
            h.set_state(s)
        if s == "speaking" and not self.assistant.tts.engine_provides_levels:
            for h in self._holos().values():
                h.set_synthetic_speech(True)
        self.mini.set_state(s)
        self.overlay.state = s
        if s == "speaking" and not self.assistant.tts.engine_provides_levels:
            self.holo.set_synthetic_speech(True)
            self.mini.set_synthetic_speech(True)
        vl = {"listening": ("Listening", "busy"), "speaking": ("Speaking", "busy"),
              "thinking": ("Standby", "ok")}.get(s)
        if vl:
            self.header.chip_voice.set(*vl)
        elif self.cfg.stt_enabled or self.cfg.tts_enabled:
            self.header.chip_voice.set("Ready" if self.assistant.stt.available else "Mic unavailable",
                                       "ok" if self.assistant.stt.available else "warn")
        else:
            self.header.chip_voice.set("Disabled", "off")
        if s == "thinking":
            self.header.chip_ai.set("Processing…", "busy")
        elif s == "offline":
            self.header.chip_ai.set(self._ai_detail or "Ollama not reachable", "warn")
        elif self._ai_detail:
            self.header.chip_ai.set(self._ai_detail, "ok" if self._ai_ok else "warn")

    _ai_detail = ""
    _ai_ok = False

    def _on_llm_status(self, online: bool, detail: str, models: list):
        ok = online and "not installed" not in detail
        self._ai_ok = ok
        self._ai_detail = self.cfg.model if ok else detail
        if self.assistant.state != "thinking":
            self.header.chip_ai.set(self._ai_detail, "ok" if ok else "warn")
        self.overlay.model = self.cfg.model.upper()
        if self._first_llm_status:
            self._first_llm_status = False
            self._startup(online, detail)

    def _startup(self, online: bool, detail: str):
        if online and "not installed" not in detail:
            self.assistant.warm_up()
        if online and self.assistant.briefing_due():
            self.assistant.morning_briefing()
        elif self.cfg.show_greeting:
            msg = self.assistant.greeting()
            if not online:
                msg += (" However, I'm unable to reach my language core. Please start Ollama; "
                        "basic commands remain available.")
            elif "not installed" in detail:
                msg += f" However, the model {self.cfg.model} isn't installed yet. Run: ollama pull {self.cfg.model}."
            self.assistant.speak_reply(msg)
        self._log(f"{self.cfg.assistant_name} online · {detail}")

    def _on_note(self, text: str, level: str):
        self._chat().add_note(text, level)
        self._log(text, level)

    def _on_status(self, text: str):
        if text:
            self._log(text, "info")

    def _on_mic_level(self, v: float):
        for bar in self._bars():
            bar.mic.set_level(v)
        if self.assistant.state == "listening":
            for h in self._holos().values():
                h.set_audio_level(v)
            self.mini.set_audio_level(v)
            self.overlay.push_level(v)

    def _on_speech_level(self, v: float):
        for h in self._holos().values():
            h.set_audio_level(v)
        self.mini.set_audio_level(v)
        self.overlay.push_level(v)

    def _log(self, text: str, level: str = "info"):
        mark = {"error": "!!", "tool": ">>", "info": "··"}.get(level, "··")
        item = QListWidgetItem(f"{dt.datetime.now():%H:%M:%S} {mark} {text}")
        item.setForeground(QColor({"error": T.RED, "tool": T.GOLD}.get(level, T.DIM)))
        self.activity.addItem(item)
        if self.activity.count() > 300:
            self.activity.takeItem(0)
        self.activity.scrollToBottom()

    # ---------------------------------------------------------------- actions
    def _send(self):
        text = self.input.text().strip()
        if text:
            self.input.clear()
            self.assistant.submit(text, "text")

    def _toggle_listen(self):
        if self.assistant.state == "listening" and not self.cfg.wake_word_enabled:
            self.assistant.cancel_listen()
        else:
            self.assistant.listen()

    def _interrupt(self):
        self.assistant.interrupt()
        self.assistant.cancel_listen()
        self._log("Interrupted")

    def _toggle_hands_free(self, on: bool):
        self.assistant.set_hands_free(on)
        self._update_mode_chip()

    def _toggle_tts(self, on: bool):
        self.cfg.tts_enabled = on
        self.cfg.save()
        if not on:
            self.assistant.tts.interrupt()

    def _update_mode_chip(self):
        if self.cfg.wake_word_enabled:
            self.header.chip_mode.set(f"Hands-free · “{self.cfg.wake_words[0].title()}”", "ok")
        else:
            self.header.chip_mode.set("Push-to-talk", "ok")

    def _new_session(self):
        self.assistant.new_conversation()
        self.chat.clear()
        self.chat.add_note("New session started. Long-term memory retained.", "info")
        self._log("New session")

    def _toggle_fullscreen(self):
        self.showNormal() if self.isFullScreen() else self.showFullScreen()

    def _open_protocols(self):
        dlg = ProtocolsDialog(self.assistant, self)
        if dlg.exec():
            names = self.assistant.protocols.names()
            self._log(f"Protocols saved ({len(names)}): {', '.join(names)}")

    def _open_settings(self):
        old = (self.cfg.quality, self.cfg.fps)
        dlg = SettingsDialog(self.cfg, self.assistant, self)
        if dlg.exec():
            self.assistant.apply_config()
            self._sync_bars()
            self._update_mode_chip()
            self.chat.assistant_name = self.cfg.assistant_name.upper()
            self.header.update()
            self._log("Settings saved")
            self._app_state_changed(QApplication.instance().applicationState())
            if old != (self.cfg.quality, self.cfg.fps):
                self._log("Restart JARVIS to apply display changes", "info")

    # ---------------------------------------------------------------- memory
    def _refresh_memory(self):
        self.mem_list.clear()
        facts = self.assistant.memory.facts()
        for f in facts:
            it = QListWidgetItem(f"{f.key.replace('_', ' ').upper()}\n{f.value}")
            it.setData(Qt.UserRole, f.id)
            it.setToolTip(f"Source: {f.source} · updated {dt.datetime.fromtimestamp(f.updated):%d %b %Y %H:%M}")
            self.mem_list.addItem(it)
        if not facts:
            it = QListWidgetItem("No long-term memories yet.\nTry: “Remember that my favourite colour is gold.”")
            it.setFlags(Qt.NoItemFlags)
            self.mem_list.addItem(it)
        st = self.assistant.memory.stats()
        self.mem_stats.setText(f"FACTS {st['facts']:>3} · MSGS {st['messages']:>5} · SESSIONS {st['conversations']}")
        self.header.chip_mem.set(f"{st['facts']} facts · {st['messages']} msgs", "ok")

    def _memory_menu(self, pos):
        item = self.mem_list.itemAt(pos)
        if not item or item.data(Qt.UserRole) is None:
            return
        menu = QMenu(self)
        act = QAction("Forget this", menu)
        act.triggered.connect(lambda: (self.assistant.memory.delete_fact(item.data(Qt.UserRole)),
                                       self._refresh_memory()))
        menu.addAction(act)
        menu.exec(self.mem_list.mapToGlobal(pos))

    def _load_history(self):
        recent = self.assistant.memory.recent_messages(8)
        if recent:
            self.chat.add_note("Previous session", "info")
            for m in recent:
                self.chat.add_history(m["role"], m["content"], m["ts"])
            self.chat.add_note("— new session —", "info")

    # ----------------------------------------------------------------- stats
    def _update_stats(self):
        cpu = psutil.cpu_percent(None)
        per = psutil.cpu_percent(None, percpu=True)
        vm = psutil.virtual_memory()
        try:
            du = psutil.disk_usage("C:\\" if sys.platform == "win32" else "/")
        except Exception:
            du = None
        self.g_cpu.set_value(cpu, f"{psutil.cpu_count()} THREADS")
        self.g_ram.set_value(vm.percent, f"{vm.used / 2**30:.1f}/{vm.total / 2**30:.0f} GB")
        if du:
            self.g_disk.set_value(du.percent, f"{du.free / 2**30:.0f} GB FREE")
        bat = psutil.sensors_battery() if hasattr(psutil, "sensors_battery") else None
        if bat:
            self.g_bat.label = "BATTERY"
            self.g_bat.set_value(bat.percent, "CHARGING" if bat.power_plugged else "DISCHARGING")
        else:
            sw = psutil.swap_memory()
            self.g_bat.label = "SWAP"
            self.g_bat.set_value(sw.percent, f"{sw.used / 2**30:.1f} GB")
        self.cores.set_values(per)
        n, t = psutil.net_io_counters(), time.time()
        pn, pt = self._net_last
        dtm = max(0.001, t - pt)
        down = (n.bytes_recv - pn.bytes_recv) / dtm / 1024
        up = (n.bytes_sent - pn.bytes_sent) / dtm / 1024
        self._net_last = (n, t)
        self.spark.push([down, up], [f"{down:,.0f}", f"{up:,.0f}"])
        upt = time.time() - psutil.boot_time()
        self._stats_n = getattr(self, "_stats_n", 0) + 1
        if not perf.on("light_stats") or self._stats_n % 5 == 1 or not hasattr(self, "_nprocs"):
            self._nprocs = len(psutil.pids())
        self.sys_info.setText(f"PROCESSES {self._nprocs:>4}   UPTIME {int(upt // 3600):02d}:{int(upt % 3600 // 60):02d}"
                              f"   PID {psutil.Process().pid}")

    # ------------------------------------------------------------------ misc
    def _app_state_changed(self, state):
        """Lower the hologram frame rate while another app is in front (Settings › Performance)."""
        from PySide6.QtCore import Qt as _Qt
        active = state == _Qt.ApplicationActive
        fps = self.cfg.fps if (active or not perf.on("background_fps")) else min(30, self.cfg.fps)
        for h in self._holos().values():
            h.set_fps(fps)

    def showEvent(self, e):
        super().showEvent(e)
        perf.record_once("startup_window", perf.since_start())
        if not getattr(self, "_preload_scheduled", False):
            self._preload_scheduled = True
            if perf.on("preload_speech"):
                QTimer.singleShot(4000, self.assistant.preload_speech)
        if sys.platform == "win32":
            _dark_title_bar(self)

    # ------------------------------------------------------------- mini mode
    def changeEvent(self, e):
        super().changeEvent(e)
        if e.type() == e.Type.WindowStateChange and self.isMinimized() and self.cfg.mini_enabled \
                and not self._quitting and self.isVisible():
            prev = self.windowState() & ~Qt.WindowMinimized
            self._restore_state = prev if prev else Qt.WindowNoState
            QTimer.singleShot(0, self._condense)

    def _condense(self):
        if not self.isMinimized():
            return
        self.hide()                       # removes the taskbar button; the hologram takes its place
        for h in self._holos().values():
            h.set_paused(True)
        self.mini.set_scene(self.assistant.persona)
        self.mini.place()
        self.mini.set_state(self.assistant.state)
        self.mini.show()
        self._log("Condensed to floating hologram (double-click it to restore)")

    def _restore_from_mini(self):
        self.mini.hide()
        self._active_holo().set_paused(False)
        st = self._restore_state
        if st & Qt.WindowFullScreen:
            self.showFullScreen()
        elif st & Qt.WindowMaximized:
            self.showMaximized()
        else:
            self.showNormal()
        self.raise_()
        self.activateWindow()

    # --------------------------------------------------------------- personas
    def _active_holo(self) -> HologramWidget:
        return self._holos().get(self.assistant.persona, self.holo)

    def _apply_persona(self, persona: str, initial: bool = False):
        T.set_palette(persona)
        QApplication.instance().setStyleSheet(T.stylesheet())
        self.stack.setCurrentIndex({"jarvis": 0, "atom": 1, "rufus": 2}.get(persona, 0))
        jname = ".".join(self.cfg.assistant_name.upper()) + "."
        self.header.title, self.header.subtitle, title = {
            "atom": ("A.T.O.M.", "ACADEMIC TUTOR & ORGANISATION MODULE", "A.T.O.M — Work & Revision"),
            "rufus": ("R.U.F.U.S.", "RESEARCH UNIT FOR UNIVERSAL SEARCH", "R.U.F.U.S — Internet Research"),
        }.get(persona, (jname, "JUST A RATHER VERY INTELLIGENT SYSTEM",
                        f"{self.cfg.assistant_name} — Personal AI System"))
        self.setWindowTitle(title)
        for name, h in self._holos().items():
            # only the visible agent's hologram renders; none while condensed to the floating hologram
            h.set_paused(self.mini.isVisible() or name != persona)
        self.mini.set_scene(persona)
        self.atom.restyle()
        self.header.chip_ai.set(self.header.chip_ai.value, self.header.chip_ai.level)
        for w in self.findChildren(QWidget):
            w.update()
        self.header._tick()
        if not initial:
            self._log(f"Switched to {self.assistant.display_name}")
            {"atom": self.atom.input, "rufus": self.rufus.input}.get(persona, self.input).setFocus()

    def _timer_finished(self, mode: str):
        if mode == "focus":
            self.assistant.speak_reply("Focus session complete. Take a five minute break – you've earned it.")
        else:
            self.assistant.speak_reply("Break's over. Ready for the next focus session when you are.")

    def _quit(self):
        self._quitting = True
        self.close()
        QApplication.instance().quit()

    def closeEvent(self, e):
        self._quitting = True
        self.mini.shutdown()
        self.mini.close()
        for h in self._holos().values():
            h.cleanup()
        self.assistant.shutdown()
        super().closeEvent(e)


class CoreBars(QWidget):
    """Per-core CPU load bars."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.values: list[float] = []
        self.setFixedHeight(34)

    def set_values(self, v):
        self.values = v
        self.update()

    def paintEvent(self, e):
        if not self.values:
            return
        p = QPainter(self)
        n = len(self.values)
        w = self.width() / n
        h = self.height() - 12
        for i, v in enumerate(self.values):
            p.fillRect(QRectF(i * w + 1, 0, w - 2, h), amber(22))
            bh = h * v / 100
            c = QColor(T.RED if v > 90 else T.AMBER)
            c.setAlpha(200)
            p.fillRect(QRectF(i * w + 1, h - bh, w - 2, bh), c)
        p.setPen(QColor(T.DIM))
        p.setFont(T.font(7, mono=True))
        p.drawText(QRectF(0, h + 1, self.width(), 11), Qt.AlignLeft, "PER-CORE LOAD")
        p.end()


def _dark_title_bar(win):
    try:
        import ctypes
        hwnd = int(win.winId())
        val = ctypes.c_int(1)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(val), ctypes.sizeof(val))
        # Windows 11: caption colour
        col = ctypes.c_int(0x00040608)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 35, ctypes.byref(col), ctypes.sizeof(col))
    except Exception:
        pass
