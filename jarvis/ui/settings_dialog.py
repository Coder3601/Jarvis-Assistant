"""Settings dialog."""
from __future__ import annotations

import os
import subprocess
import sys

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFileDialog,
                               QFormLayout, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton, QSpinBox,
                               QGroupBox, QScrollArea, QTabWidget, QVBoxLayout, QWidget)

from ..config import data_dir

WHISPER_MODELS = ["tiny.en", "base.en", "small.en", "medium.en", "tiny", "base", "small", "medium",
                  "large-v3", "distil-large-v3", "large-v3-turbo"]


def _row(*widgets):
    w = QWidget()
    lay = QHBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 0)
    for x in widgets:
        lay.addWidget(x)
    return w


def _pitch_box(value: float) -> QSpinBox:
    sp = QSpinBox()
    sp.setRange(60, 120)
    sp.setSuffix(" %")
    sp.setValue(int(round(float(value) * 100)))
    sp.setToolTip("100% = the voice's natural pitch. Lower = deeper (try 80–90%).")
    return sp


class SettingsDialog(QDialog):
    def __init__(self, cfg, assistant, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.assistant = assistant
        self.setWindowTitle("JARVIS — Settings")
        self.setMinimumWidth(920)
        self.setMinimumHeight(640)
        tabs = QTabWidget()
        tabs.addTab(self._general(), "GENERAL")
        tabs.addTab(self._ai(), "AI CORE")
        tabs.addTab(self._voice(), "VOICE")
        tabs.addTab(self._atom(), "A.T.O.M")
        tabs.addTab(self._rufus(), "R.U.F.U.S")
        tabs.addTab(self._display(), "DISPLAY")
        tabs.addTab(self._performance(), "PERFORMANCE")
        tabs.addTab(self._memory(), "MEMORY")
        bb = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        bb.accepted.connect(self._save)
        bb.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addWidget(tabs)
        lay.addWidget(bb)

    # -------------------------------------------------------------- tabs
    def _general(self):
        w = QWidget()
        f = QFormLayout(w)
        self.e_name = QLineEdit(self.cfg.assistant_name)
        self.e_user = QLineEdit(self.cfg.user_name)
        self.e_user.setPlaceholderText("Your first name")
        self.e_title = QLineEdit(self.cfg.user_title)
        self.e_title.setPlaceholderText("sir / ma'am / boss / your name")
        self.c_greet = QCheckBox("Greet me when JARVIS starts")
        self.c_greet.setChecked(self.cfg.show_greeting)
        f.addRow("Assistant name", self.e_name)
        f.addRow("Your name", self.e_user)
        f.addRow("Address me as", self.e_title)
        f.addRow("", self.c_greet)
        self.c_brief = QCheckBox("Morning briefing on the first start of the day (before noon)")
        self.c_brief.setChecked(self.cfg.briefing_auto)
        self.c_brief_news = QCheckBox("Include news headlines in the briefing (uses the internet)")
        self.c_brief_news.setChecked(self.cfg.briefing_news)
        self.e_brief_q = QLineEdit(self.cfg.briefing_news_query)
        self.e_brief_q.setPlaceholderText("technology")
        f.addRow("", self.c_brief)
        f.addRow("", self.c_brief_news)
        f.addRow("Briefing news topic", self.e_brief_q)
        keys = QLabel("<b>KEYBOARD SHORTCUTS</b><br>"
                      "Ctrl+Space / F2 — talk<br>Esc — interrupt<br>Ctrl+J — next agent (JARVIS → A.T.O.M → R.U.F.U.S)"
                      "<br>Ctrl+P — protocols<br>Ctrl+M — floating mini hologram<br>Ctrl+N — new session<br>"
                      "Ctrl+L — focus the command box<br>Ctrl+, — settings<br>F11 — full screen")
        keys.setObjectName("Dim")
        f.addRow(keys)
        return w

    def _ai(self):
        w = QWidget()
        f = QFormLayout(w)
        self.e_url = QLineEdit(self.cfg.ollama_url)
        self.cb_model = QComboBox()
        self.cb_model.setEditable(True)
        self.cb_model.addItem(self.cfg.model)
        self.cb_model.setCurrentText(self.cfg.model)
        refresh = QPushButton("REFRESH")
        refresh.clicked.connect(self._refresh_models)
        self._refresh_models()
        self.sp_temp = QDoubleSpinBox()
        self.sp_temp.setRange(0.0, 1.5)
        self.sp_temp.setSingleStep(0.1)
        self.sp_temp.setValue(self.cfg.temperature)
        self.sp_ctx = QSpinBox()
        self.sp_ctx.setRange(2, 60)
        self.sp_ctx.setValue(self.cfg.context_messages)
        self.sp_numctx = QSpinBox()
        self.sp_numctx.setRange(2048, 131072)
        self.sp_numctx.setSingleStep(2048)
        self.sp_numctx.setValue(self.cfg.num_ctx)
        self.c_tools = QCheckBox("Allow the model to control the computer (tool calling)")
        self.c_tools.setChecked(self.cfg.use_tools)
        self.c_automem = QCheckBox("Automatically learn facts about me from conversations")
        self.c_automem.setChecked(self.cfg.auto_memory)
        f.addRow("Ollama URL", self.e_url)
        f.addRow("Model", _row(self.cb_model, refresh))
        hint = QLabel("Install models with:  ollama pull llama3.1:8b   (or qwen2.5:7b, llama3.2:3b for low-end PCs)")
        hint.setObjectName("Dim")
        hint.setWordWrap(True)
        f.addRow("", hint)
        f.addRow("Creativity (temperature)", self.sp_temp)
        f.addRow("Recent messages in context", self.sp_ctx)
        f.addRow("Context window (tokens)", self.sp_numctx)
        f.addRow("", self.c_tools)
        f.addRow("", self.c_automem)
        return w

    def _voice(self):
        w = QWidget()
        f = QFormLayout(w)
        self.c_stt = QCheckBox("Enable voice input")
        self.c_stt.setChecked(self.cfg.stt_enabled)
        self.cb_whisper = QComboBox()
        self.cb_whisper.setEditable(True)
        self.cb_whisper.addItems(WHISPER_MODELS)
        self.cb_whisper.setCurrentText(self.cfg.whisper_model)
        self.cb_dev = QComboBox()
        self.cb_dev.addItems(["auto", "cpu", "cuda"])
        self.cb_dev.setCurrentText(self.cfg.whisper_device)
        self.e_lang = QLineEdit(self.cfg.language)
        self.e_lang.setPlaceholderText("en  (blank = auto-detect)")
        self.cb_mic = QComboBox()
        self.cb_mic.addItem("System default", None)
        try:
            import sounddevice as sd
            for i, d in enumerate(sd.query_devices()):
                if d.get("max_input_channels", 0) > 0:
                    self.cb_mic.addItem(f"{i}: {d['name']}", i)
        except Exception:
            pass
        idx = self.cb_mic.findData(self.cfg.input_device)
        self.cb_mic.setCurrentIndex(max(0, idx))
        self.c_wake = QCheckBox("Hands-free mode (always listening for the wake word)")
        self.c_wake.setChecked(self.cfg.wake_word_enabled)
        self.e_wake = QLineEdit(", ".join(self.cfg.wake_words))
        self.sp_sens = QDoubleSpinBox()
        self.sp_sens.setRange(0.3, 3.0)
        self.sp_sens.setSingleStep(0.1)
        self.sp_sens.setValue(self.cfg.vad_sensitivity)
        self.sp_sil = QDoubleSpinBox()
        self.sp_sil.setRange(0.4, 3.0)
        self.sp_sil.setSingleStep(0.1)
        self.sp_sil.setValue(self.cfg.silence_timeout)

        self.c_tts = QCheckBox("Speak responses aloud")
        self.c_tts.setChecked(self.cfg.tts_enabled)
        self.cb_engine = QComboBox()
        self.cb_engine.addItem("Windows voices (built in)", "windows")
        self.cb_engine.addItem("Piper (neural, optional)", "piper")
        eng = {"pyttsx3": "windows"}.get(self.cfg.tts_engine, self.cfg.tts_engine)
        self.cb_engine.setCurrentIndex(max(0, self.cb_engine.findData(eng)))
        self.cb_voice = QComboBox()
        self.cb_voice.addItem("Automatic (prefer English male voice)", "")
        for vid, name in self.assistant.tts.voices():
            self.cb_voice.addItem(name, vid)
        vi = self.cb_voice.findData(self.cfg.tts_voice)
        self.cb_voice.setCurrentIndex(max(0, vi))
        self.sp_rate = QSpinBox()
        self.sp_rate.setRange(90, 320)
        self.sp_rate.setValue(self.cfg.tts_rate)
        self.sp_vol = QDoubleSpinBox()
        self.sp_vol.setRange(0.0, 1.0)
        self.sp_vol.setSingleStep(0.1)
        self.sp_vol.setValue(self.cfg.tts_volume)
        self.e_piper = QLineEdit(self.cfg.piper_exe)
        self.e_piper_model = QLineEdit(self.cfg.piper_model)
        b1 = QPushButton("…")
        b1.clicked.connect(lambda: self._browse(self.e_piper, "Piper executable (piper.exe)", "Executables (*.exe);;All (*)"))
        b2 = QPushButton("…")
        b2.clicked.connect(lambda: self._browse(self.e_piper_model, "Piper voice (.onnx)", "Voices (*.onnx)"))
        test = QPushButton("TEST VOICE")
        test.clicked.connect(self._test_voice)

        f.addRow(QLabel("<b>SPEECH RECOGNITION</b>"))
        f.addRow("", self.c_stt)
        f.addRow("Whisper model", self.cb_whisper)
        f.addRow("Compute device", self.cb_dev)
        f.addRow("Language", self.e_lang)
        f.addRow("Microphone", self.cb_mic)
        f.addRow("", self.c_wake)
        f.addRow("Wake words", self.e_wake)
        f.addRow("Mic sensitivity", self.sp_sens)
        f.addRow("End-of-speech silence (s)", self.sp_sil)
        f.addRow(QLabel("<b>SPEECH OUTPUT</b>"))
        f.addRow("", self.c_tts)
        f.addRow("Engine", self.cb_engine)
        f.addRow("Voice (Windows)", self.cb_voice)
        self.sp_pitch = _pitch_box(self.cfg.tts_pitch)
        f.addRow("Voice depth", self.sp_pitch)
        f.addRow("Speaking rate", self.sp_rate)
        f.addRow("Volume", self.sp_vol)
        dl = QPushButton("DOWNLOAD 3 MALE VOICES")
        dl.setToolTip("Downloads one natural Piper voice for each agent from the official Piper voice library")
        dl.clicked.connect(self._download_voices)
        self.dl_status = QLabel("Alan (JARVIS) · Northern English male (A.T.O.M) · Ryan (R.U.F.U.S)")
        self.dl_status.setObjectName("Dim")
        self.dl_status.setWordWrap(True)
        f.addRow("Natural voices", dl)
        f.addRow("", self.dl_status)
        f.addRow("JARVIS Piper voice", _row(self.e_piper_model, b2))
        f.addRow("Piper program (legacy, optional)", _row(self.e_piper, b1))
        f.addRow("", test)
        return w

    def _atom(self):
        w = QWidget()
        f = QFormLayout(w)
        self.cb_atom_voice = QComboBox()
        self.cb_atom_voice.addItem("Automatic (a different voice from JARVIS)", "")
        for vid, name in self.assistant.tts.voices():
            self.cb_atom_voice.addItem(name, vid)
        vi = self.cb_atom_voice.findData(self.cfg.atom_tts_voice)
        self.cb_atom_voice.setCurrentIndex(max(0, vi))
        self.sp_atom_rate = QSpinBox()
        self.sp_atom_rate.setRange(90, 320)
        self.sp_atom_rate.setValue(self.cfg.atom_tts_rate)
        self.e_atom_piper = QLineEdit(self.cfg.atom_piper_model)
        self.e_atom_piper.setPlaceholderText("Optional – blank uses JARVIS's Piper voice")
        b = QPushButton("…")
        b.clicked.connect(lambda: self._browse(self.e_atom_piper, "Piper voice (.onnx)", "Voices (*.onnx)"))
        self.sp_focus = QSpinBox()
        self.sp_focus.setRange(5, 120)
        self.sp_focus.setValue(self.cfg.focus_minutes)
        self.sp_break = QSpinBox()
        self.sp_break.setRange(1, 60)
        self.sp_break.setValue(self.cfg.break_minutes)
        info = QLabel("Switch with “Jarvis, switch to Atom” / “Atom, switch to Jarvis”, the A.T.O.M button, or Ctrl+J.")
        info.setObjectName("Dim")
        info.setWordWrap(True)
        f.addRow(info)
        f.addRow("Voice (Windows)", self.cb_atom_voice)
        self.sp_atom_pitch = _pitch_box(self.cfg.atom_tts_pitch)
        f.addRow("Voice depth", self.sp_atom_pitch)
        f.addRow("Speaking rate", self.sp_atom_rate)
        f.addRow("Piper voice model", _row(self.e_atom_piper, b))
        f.addRow("Focus session (minutes)", self.sp_focus)
        f.addRow("Break (minutes)", self.sp_break)
        return w

    def _rufus(self):
        from ..core.research import DEPTHS
        w = QWidget()
        f = QFormLayout(w)
        info = QLabel("R.U.F.U.S is the only agent that uses the internet. It searches the web for free "
                      "(DuckDuckGo and other engines through the ddgs package, plus Wikipedia), reads the pages, "
                      "and your local AI model writes the report.")
        info.setObjectName("Dim")
        info.setWordWrap(True)
        f.addRow(info)
        self.cb_backend = QComboBox()
        self.cb_backend.addItem("Web (DuckDuckGo & co. – free, no account)", "ddgs")
        self.cb_backend.addItem("My own SearXNG server", "searxng")
        self.cb_backend.setCurrentIndex(max(0, self.cb_backend.findData(self.cfg.search_backend)))
        self.e_searx = QLineEdit(self.cfg.searxng_url)
        self.e_searx.setPlaceholderText("http://localhost:8888")
        self.e_region = QLineEdit(self.cfg.search_region)
        self.e_region.setPlaceholderText("uk-en, us-en, wt-wt (worldwide)…")
        self.cb_depth = QComboBox()
        self.cb_depth.addItems(list(DEPTHS))
        self.cb_depth.setCurrentText(self.cfg.research_depth)
        self.cb_rufus_voice = QComboBox()
        self.cb_rufus_voice.addItem("Automatic (a different voice from the others)", "")
        for vid, name in self.assistant.tts.voices():
            self.cb_rufus_voice.addItem(name, vid)
        self.cb_rufus_voice.setCurrentIndex(max(0, self.cb_rufus_voice.findData(self.cfg.rufus_tts_voice)))
        self.sp_rufus_rate = QSpinBox()
        self.sp_rufus_rate.setRange(90, 320)
        self.sp_rufus_rate.setValue(self.cfg.rufus_tts_rate)
        self.e_rufus_piper = QLineEdit(self.cfg.rufus_piper_model)
        self.e_rufus_piper.setPlaceholderText("Optional – blank uses JARVIS's Piper voice")
        b = QPushButton("…")
        b.clicked.connect(lambda: self._browse(self.e_rufus_piper, "Piper voice (.onnx)", "Voices (*.onnx)"))
        f.addRow("Search with", self.cb_backend)
        f.addRow("SearXNG URL", self.e_searx)
        f.addRow("Search region", self.e_region)
        f.addRow("Default research depth", self.cb_depth)
        self.c_watch_auto = QCheckBox("Check watched topics automatically once a day")
        self.c_watch_auto.setChecked(self.cfg.watch_auto)
        self.sp_watch_hour = QSpinBox()
        self.sp_watch_hour.setRange(0, 23)
        self.sp_watch_hour.setSuffix(":00")
        self.sp_watch_hour.setValue(self.cfg.watch_hour)
        f.addRow("", self.c_watch_auto)
        f.addRow("Daily digest not before", self.sp_watch_hour)
        f.addRow("Voice (Windows)", self.cb_rufus_voice)
        self.sp_rufus_pitch = _pitch_box(self.cfg.rufus_tts_pitch)
        f.addRow("Voice depth", self.sp_rufus_pitch)
        f.addRow("Speaking rate", self.sp_rufus_rate)
        f.addRow("Piper voice model", _row(self.e_rufus_piper, b))
        return w

    def _display(self):
        w = QWidget()
        f = QFormLayout(w)
        self.cb_quality = QComboBox()
        self.cb_quality.addItems(["low", "medium", "high", "ultra"])
        self.cb_quality.setCurrentText(self.cfg.quality)
        self.sp_fps = QSpinBox()
        self.sp_fps.setRange(20, 144)
        self.sp_fps.setValue(self.cfg.fps)
        self.c_max = QCheckBox("Start maximised")
        self.c_max.setChecked(self.cfg.start_maximized)
        self.c_mini = QCheckBox("Condense to a floating hologram when minimised")
        self.c_mini.setChecked(self.cfg.mini_enabled)
        note = QLabel("Quality and FPS changes apply after restarting JARVIS.")
        note.setObjectName("Dim")
        f.addRow("Hologram quality", self.cb_quality)
        f.addRow("Target frame rate", self.sp_fps)
        f.addRow("", self.c_max)
        f.addRow("", self.c_mini)
        f.addRow("", note)
        return w

    def _performance(self):
        from .. import perf
        outer = QWidget()
        ol = QVBoxLayout(outer)
        ol.setContentsMargins(0, 0, 0, 0)
        top = QHBoxLayout()
        intro = QLabel("Every speed-up can be switched on or off. Items marked RESTART take effect the next "
                       "time JARVIS starts. The single biggest speed-up for replies is a smaller AI model "
                       "(e.g. llama3.2:3b) in AI Core.")
        intro.setWordWrap(True)
        intro.setObjectName("Dim")
        top.addWidget(intro, 1)
        b_on = QPushButton("ALL ON")
        b_off = QPushButton("ALL OFF")
        top.addWidget(b_on)
        top.addWidget(b_off)
        ol.addLayout(top)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        inner = QWidget()
        il = QVBoxLayout(inner)
        self.perf_boxes: dict[str, QCheckBox] = {}
        groups: dict[str, QVBoxLayout] = {}
        for key, group, label, desc, _default, restart in perf.OPTIONS:
            if group not in groups:
                gb = QGroupBox(group.upper())
                groups[group] = QVBoxLayout(gb)
                il.addWidget(gb)
            cb = QCheckBox(label + ("   ·  RESTART" if restart else ""))
            cb.setChecked(perf.on(key))
            groups[group].addWidget(cb)
            if desc:
                d = QLabel(desc)
                d.setWordWrap(True)
                d.setObjectName("Dim")
                d.setContentsMargins(24, 0, 0, 6)
                groups[group].addWidget(d)
            self.perf_boxes[key] = cb
        mg = QGroupBox("LATEST MEASUREMENTS")
        ml = QVBoxLayout(mg)
        self.perf_metrics = QLabel()
        self.perf_metrics.setTextInteractionFlags(Qt.TextSelectableByMouse)
        ml.addWidget(self.perf_metrics)
        rb = QPushButton("REFRESH")
        rb.clicked.connect(self._refresh_metrics)
        ml.addWidget(rb)
        il.addWidget(mg)
        il.addStretch(1)
        scroll.setWidget(inner)
        ol.addWidget(scroll, 1)
        b_on.clicked.connect(lambda: [cb.setChecked(True) for cb in self.perf_boxes.values()])
        b_off.clicked.connect(lambda: [cb.setChecked(False) for cb in self.perf_boxes.values()])
        self._refresh_metrics()
        return outer

    def _refresh_metrics(self):
        from .. import perf
        rows = perf.summary()
        if not rows:
            self.perf_metrics.setText("Nothing measured yet. Use JARVIS for a moment (with “Measure and log "
                                      "timings” on) and press REFRESH.")
        else:
            self.perf_metrics.setText("\n".join(f"{label:<34} {value}" for label, value in rows))
            self.perf_metrics.setFont(self.perf_metrics.font())

    def _memory(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        st = self.assistant.memory.stats()
        info = QLabel(f"Database: {data_dir() / 'memory.db'}\n\n"
                      f"Conversations: {st['conversations']}    Messages: {st['messages']}    "
                      f"Facts: {st['facts']}    Size: {st['size_kb']} KB")
        info.setWordWrap(True)
        info.setTextInteractionFlags(Qt.TextSelectableByMouse)
        b_open = QPushButton("OPEN DATA FOLDER")
        b_open.clicked.connect(self._open_data)
        b_facts = QPushButton("FORGET ALL FACTS")
        b_facts.clicked.connect(lambda: self._confirm("Delete every remembered fact?", self.assistant.memory.clear_facts))
        b_hist = QPushButton("DELETE CONVERSATION HISTORY")
        b_hist.clicked.connect(lambda: self._confirm("Delete all conversation history?",
                                                     self.assistant.memory.clear_history))
        lay.addWidget(info)
        lay.addSpacing(10)
        for b in (b_open, b_facts, b_hist):
            lay.addWidget(b)
        lay.addStretch(1)
        return w

    # ---------------------------------------------------------- actions
    def _refresh_models(self):
        models = self.assistant.llm.list_models()
        cur = self.cb_model.currentText()
        self.cb_model.clear()
        self.cb_model.addItems(models or [cur])
        self.cb_model.setCurrentText(cur)

    def _browse(self, edit, title, filt):
        path, _ = QFileDialog.getOpenFileName(self, title, os.path.dirname(edit.text()) or "", filt)
        if path:
            edit.setText(path)

    def _download_voices(self):
        import threading

        from ..core import voices as V
        if not V.piper_installed():
            self.dl_status.setText("Piper isn't installed yet. In your venv run:  pip install piper-tts  "
                                   "then reopen Settings.")
            return
        self._dl_msgs: list[str] = []
        self._dl_done = None

        def run():
            try:
                paths = V.download_recommended(self.cfg, progress=self._dl_msgs.append)
                self._dl_done = ("ok", paths)
            except Exception as exc:
                self._dl_done = ("error", str(exc))
        threading.Thread(target=run, daemon=True).start()
        self.dl_status.setText("Starting download (about 60 MB in total)…")
        self._dl_timer = QTimer(self)
        self._dl_timer.timeout.connect(self._poll_download)
        self._dl_timer.start(300)

    def _poll_download(self):
        if self._dl_msgs:
            self.dl_status.setText(self._dl_msgs[-1])
        if self._dl_done is None:
            return
        self._dl_timer.stop()
        kind, data = self._dl_done
        if kind == "error":
            self.dl_status.setText(f"Download failed: {data}")
            return
        self.e_piper_model.setText(data["jarvis"])
        self.e_atom_piper.setText(data["atom"])
        self.e_rufus_piper.setText(data["rufus"])
        self.cb_engine.setCurrentIndex(max(0, self.cb_engine.findData("piper")))
        self.dl_status.setText("Done – engine set to Piper with a different voice for each agent. Press TEST VOICE.")

    def _test_voice(self):
        self._apply()
        self.assistant.tts.reload()
        self.assistant.tts.say(f"Voice systems calibrated. Good to hear you, {self.cfg.user_title}.")

    def _confirm(self, msg, fn):
        if QMessageBox.question(self, "Confirm", msg) == QMessageBox.Yes:
            fn()
            self.assistant.memory_changed.emit()

    def _open_data(self):
        path = str(data_dir())
        if sys.platform == "win32":
            os.startfile(path)
        else:
            subprocess.Popen(["xdg-open", path])

    def _apply(self):
        c = self.cfg
        c.assistant_name = self.e_name.text().strip() or "JARVIS"
        c.user_name = self.e_user.text().strip()
        c.user_title = self.e_title.text().strip()
        c.show_greeting = self.c_greet.isChecked()
        c.ollama_url = self.e_url.text().strip() or "http://127.0.0.1:11434"
        c.model = self.cb_model.currentText().strip() or c.model
        c.temperature = self.sp_temp.value()
        c.context_messages = self.sp_ctx.value()
        c.num_ctx = self.sp_numctx.value()
        c.use_tools = self.c_tools.isChecked()
        c.auto_memory = self.c_automem.isChecked()
        c.stt_enabled = self.c_stt.isChecked()
        c.whisper_model = self.cb_whisper.currentText().strip()
        c.whisper_device = self.cb_dev.currentText()
        c.language = self.e_lang.text().strip()
        c.input_device = self.cb_mic.currentData()
        c.wake_word_enabled = self.c_wake.isChecked()
        c.wake_words = [w.strip().lower() for w in self.e_wake.text().split(",") if w.strip()] or ["jarvis"]
        c.vad_sensitivity = self.sp_sens.value()
        c.silence_timeout = self.sp_sil.value()
        c.tts_enabled = self.c_tts.isChecked()
        c.tts_engine = self.cb_engine.currentData() or "windows"
        c.tts_pitch = self.sp_pitch.value() / 100.0
        c.atom_tts_pitch = self.sp_atom_pitch.value() / 100.0
        c.rufus_tts_pitch = self.sp_rufus_pitch.value() / 100.0
        c.tts_voice = self.cb_voice.currentData() or ""
        c.tts_rate = self.sp_rate.value()
        c.tts_volume = self.sp_vol.value()
        c.piper_exe = self.e_piper.text().strip()
        c.piper_model = self.e_piper_model.text().strip()
        c.quality = self.cb_quality.currentText()
        c.fps = self.sp_fps.value()
        c.start_maximized = self.c_max.isChecked()
        c.mini_enabled = self.c_mini.isChecked()
        c.perf = {k: cb.isChecked() for k, cb in self.perf_boxes.items()}
        c.atom_tts_voice = self.cb_atom_voice.currentData() or ""
        c.atom_tts_rate = self.sp_atom_rate.value()
        c.atom_piper_model = self.e_atom_piper.text().strip()
        c.focus_minutes = self.sp_focus.value()
        c.search_backend = self.cb_backend.currentData() or "ddgs"
        c.watch_auto = self.c_watch_auto.isChecked()
        c.watch_hour = self.sp_watch_hour.value()
        c.briefing_auto = self.c_brief.isChecked()
        c.briefing_news = self.c_brief_news.isChecked()
        c.briefing_news_query = self.e_brief_q.text().strip() or "technology"
        c.searxng_url = self.e_searx.text().strip()
        c.search_region = self.e_region.text().strip() or "uk-en"
        c.research_depth = self.cb_depth.currentText()
        c.rufus_tts_voice = self.cb_rufus_voice.currentData() or ""
        c.rufus_tts_rate = self.sp_rufus_rate.value()
        c.rufus_piper_model = self.e_rufus_piper.text().strip()
        c.break_minutes = self.sp_break.value()

    def _save(self):
        self._apply()
        self.cfg.save()
        self.accept()
