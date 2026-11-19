"""A.T.O.M dashboard page: calendar + tasks | explain workspace | hologram, focus timer, chat."""
from __future__ import annotations

from PySide6.QtWidgets import QGridLayout, QSizePolicy, QVBoxLayout, QWidget

from .atom_widgets import CalendarPanel, FocusTimer, TasksPanel, Workspace
from .lazy_hologram import LazyHologram
from .command_bar import CommandBar
from .widgets import ChatLog, HudFrame


class AtomDashboard(QWidget):
    def __init__(self, cfg, assistant, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.assistant = assistant
        grid = QGridLayout(self)
        grid.setContentsMargins(14, 10, 14, 6)
        grid.setHorizontalSpacing(12)

        # ---- left: calendar + tasks
        left = QWidget()
        left.setFixedWidth(340)
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        ll.setSpacing(10)
        cal = HudFrame("Calendar", tag="SCHEDULE")
        self.calendar = CalendarPanel(assistant.memory)
        cal.body.addWidget(self.calendar)
        ll.addWidget(cal, 3)
        tasks = HudFrame("To-do", tag="TASKS")
        self.tasks = TasksPanel(assistant.memory)
        tasks.body.addWidget(self.tasks)
        ll.addWidget(tasks, 2)

        # ---- centre: explain workspace + command bar
        centre = QWidget()
        cl = QVBoxLayout(centre)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setSpacing(8)
        ws = HudFrame("Study workspace", tag="EXPLAIN · FLASHCARDS · QUIZ · NOTES")
        self.workspace = Workspace(cfg)
        self.workspace.request.connect(self._work_request)
        ws.body.addWidget(self.workspace)
        cl.addWidget(ws, 1)

        self.bar = CommandBar("Ask A.T.O.M anything, or say “switch to Jarvis”…",
                              ["hands", "voice", "jarvis", "rufus", "protocols"])
        self.bar.mic.setToolTip("Talk to A.T.O.M (Ctrl+Space / F2)")
        cl.addWidget(self.bar)

        # ---- right: hologram in the corner, focus timer, chat
        right = QWidget()
        right.setFixedWidth(390)
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(10)
        holo_frame = HudFrame("A.T.O.M", tag="CORE")
        self.holo = LazyHologram(quality=cfg.quality, fps=cfg.fps, scene="atom")
        self.holo.setMinimumSize(300, 270)
        self.holo.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.holo.setFixedHeight(290)
        holo_frame.body.addWidget(self.holo)
        rl.addWidget(holo_frame)
        tf = HudFrame("Focus timer", tag="POMODORO")
        self.timer = FocusTimer(cfg)
        tf.body.addWidget(self.timer)
        rl.addWidget(tf)
        chat = HudFrame("Conversation", tag="A.T.O.M")
        self.chat = ChatLog(assistant_name="A.T.O.M")
        chat.body.addWidget(self.chat, 1)
        rl.addWidget(chat, 1)

        grid.addWidget(left, 0, 0)
        grid.addWidget(centre, 0, 1)
        grid.addWidget(right, 0, 2)
        grid.setColumnStretch(1, 1)

    # aliases used by the main window
    @property
    def mic(self):
        return self.bar.mic

    @property
    def input(self):
        return self.bar.input

    # ------------------------------------------------------------------
    def _work_request(self, kind, topic, level):
        a = self.assistant
        if kind == "explain":
            a.explain(topic, level)
        elif kind == "summary":
            a.explain(topic, level, summarise=True)
        elif kind == "flashcards":
            a.make_flashcards(topic, level)
        elif kind == "quiz":
            a.make_quiz(topic, level)

    def refresh(self):
        self.calendar.refresh()
        self.tasks.refresh()

    def restyle(self):
        self.calendar.restyle()
        self.tasks.refresh()
