"""A hologram placeholder that creates the real OpenGL widget the first time it's shown.

With Settings › Performance › "Build holograms only when first shown" off, the real widget is
created immediately. The public API matches HologramWidget, so callers don't need to care.
"""
from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QSizePolicy, QVBoxLayout, QWidget

from .. import perf
from .hologram.renderer import HologramWidget


class LazyHologram(QWidget):
    fps_changed = Signal(float)

    def __init__(self, parent=None, quality: str = "high", fps: int = 60, scene: str = "jarvis"):
        super().__init__(parent)
        self._args = dict(quality=quality, fps=fps, scene=scene)
        self.real: HologramWidget | None = None
        self._state = "idle"
        self._paused = False
        self._fps = fps
        self._lay = QVBoxLayout(self)
        self._lay.setContentsMargins(0, 0, 0, 0)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        if not perf.on("lazy_holograms"):
            self._create()

    def _create(self):
        if self.real is not None:
            return
        self.real = HologramWidget(**self._args)
        self.real.fps_changed.connect(self.fps_changed.emit)
        self._lay.addWidget(self.real)
        self.real.set_state(self._state)
        self.real.set_fps(self._fps)
        self.real.set_paused(self._paused)

    def showEvent(self, e):
        super().showEvent(e)
        self._create()

    # ---- HologramWidget API
    def set_state(self, state: str):
        self._state = state
        if self.real:
            self.real.set_state(state)

    def set_audio_level(self, level: float):
        if self.real:
            self.real.set_audio_level(level)

    def set_synthetic_speech(self, on: bool):
        if self.real:
            self.real.set_synthetic_speech(on)

    def set_paused(self, paused: bool):
        self._paused = paused
        if self.real:
            self.real.set_paused(paused)

    def set_fps(self, fps: int):
        self._fps = fps
        if self.real:
            self.real.set_fps(fps)

    def cleanup(self):
        if self.real:
            self.real.cleanup()
