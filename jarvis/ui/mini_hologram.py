"""Floating mini hologram shown while JARVIS is minimised.

A frameless, always-on-top, per-pixel transparent window containing only the
holographic core. It is rendered offscreen with OpenGL and copied into a
transparent window, which works reliably on Windows (no black box behind it).

    hover centre  a microphone button fades in – click it to talk
    double-click  restore the full dashboard
    drag          move it anywhere
    mouse wheel   resize
    right-click   menu (Talk / Restore / Size / Quit)
"""
from __future__ import annotations

import logging

import numpy as np
import math

from PySide6.QtCore import QPoint, QPointF, QRect, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import (QAction, QColor, QGuiApplication, QImage, QOffscreenSurface, QOpenGLContext,
                           QPainter, QPen, QRadialGradient, QSurfaceFormat)
from PySide6.QtWidgets import QMenu, QWidget

from .. import perf
from . import theme as T
from .hologram.renderer import GL, HologramEngine, RenderTarget

log = logging.getLogger(__name__)

MIN_SIZE, MAX_SIZE = 140, 560


class MiniHologram(QWidget):
    talk_requested = Signal()
    restore_requested = Signal()
    quit_requested = Signal()

    def __init__(self, cfg):
        super().__init__(None, Qt.FramelessWindowHint | Qt.Tool | Qt.WindowStaysOnTopHint
                         | Qt.NoDropShadowWindowHint)
        self.cfg = cfg
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_NoSystemBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setWindowTitle("JARVIS")
        self.setMouseTracking(True)
        self._hover: QPointF | None = None
        self._mic_alpha = 0.0
        self._listening = False
        self._t = 0.0
        size = int(min(MAX_SIZE, max(MIN_SIZE, getattr(cfg, "mini_size", 260))))
        self.resize(size, size)

        self.engine: HologramEngine | None = None
        self._ctx: QOpenGLContext | None = None
        self._surface: QOffscreenSurface | None = None
        self._out: RenderTarget | None = None
        self._image: QImage | None = None
        self._failed = False
        self._pending_state = "idle"
        self._scene = getattr(cfg, "persona", "jarvis")

        self._press_global: QPoint | None = None
        self._press_offset: QPoint | None = None
        self._moved = False

        self._timer = QTimer(self)
        self._timer.setTimerType(Qt.PreciseTimer)
        self._timer.timeout.connect(self._frame)

    # ------------------------------------------------------------- state
    def set_scene(self, scene: str):
        """Show the JARVIS or A.T.O.M hologram."""
        if scene == self._scene:
            return
        self._scene = scene
        self.engine = None   # rebuilt (with the new scene) on the next frame

    def set_state(self, state: str):
        self._pending_state = state
        if self.engine:
            self.engine.set_state(state)

    def set_listening(self, on: bool):
        self._listening = on

    def set_audio_level(self, level: float):
        if self.engine:
            self.engine.set_audio_level(level)

    def set_synthetic_speech(self, on: bool):
        if self.engine:
            self.engine.set_synthetic_speech(on)

    # -------------------------------------------------------- placement
    def place(self):
        pos = getattr(self.cfg, "mini_pos", None)
        screen = QGuiApplication.primaryScreen().availableGeometry()
        if pos and any(s.availableGeometry().contains(QPoint(*pos)) for s in QGuiApplication.screens()):
            self.move(*pos)
        else:
            self.move(screen.right() - self.width() - 24, screen.bottom() - self.height() - 24)

    def _remember(self):
        self.cfg.mini_pos = [self.x(), self.y()]
        self.cfg.mini_size = self.width()
        try:
            self.cfg.save()
        except Exception:
            pass

    # --------------------------------------------------------- GL plumbing
    def _ensure_gl(self) -> bool:
        if self.engine is not None:
            return True
        if self._failed:
            return False
        if self._ctx is not None:
            # context already exists: only the scene changed
            try:
                if not self._ctx.makeCurrent(self._surface):
                    return False
                quality = "medium" if self.cfg.quality in ("high", "ultra") else self.cfg.quality
                self.engine = HologramEngine(quality, transparent=True, scene=self._scene)
                self.engine.init_gl()
                self.engine.dist = self.engine.base_dist + 0.85
                self.engine.set_state(self._pending_state)
                return self.engine.ok
            finally:
                self._ctx.doneCurrent()
        try:
            self._ctx = QOpenGLContext()
            self._ctx.setFormat(QSurfaceFormat.defaultFormat())
            if not self._ctx.create():
                raise RuntimeError("could not create OpenGL context")
            self._surface = QOffscreenSurface()
            self._surface.setFormat(self._ctx.format())
            self._surface.create()
            if not self._ctx.makeCurrent(self._surface):
                raise RuntimeError("could not activate OpenGL context")
            quality = "medium" if self.cfg.quality in ("high", "ultra") else self.cfg.quality
            self.engine = HologramEngine(quality, transparent=True, scene=self._scene)
            self.engine.init_gl()
            if not self.engine.ok:
                raise RuntimeError(self.engine.error or "renderer failed")
            self.engine.dist = self.engine.base_dist + 0.85
            self.engine.set_state(self._pending_state)
            self._ctx.doneCurrent()
            return True
        except Exception as exc:
            log.warning("Mini hologram unavailable: %s", exc)
            self._failed = True
            self.engine = None
            return False

    # ------------------------------------------------------------ mic button
    def _mic_geometry(self) -> tuple[QPointF, float]:
        c = QPointF(self.width() / 2, self.height() / 2)
        return c, max(16.0, self.width() * 0.105)

    def _over_mic(self, pos: QPointF) -> bool:
        c, r = self._mic_geometry()
        return self._mic_alpha > 0.3 and math.hypot(pos.x() - c.x(), pos.y() - c.y()) <= r * 1.15

    def _update_mic(self):
        c, _r = self._mic_geometry()
        near = self._hover is not None and math.hypot(self._hover.x() - c.x(),
                                                      self._hover.y() - c.y()) <= self.width() * 0.27
        target = 1.0 if (near or self._listening) else 0.0
        self._mic_alpha += (target - self._mic_alpha) * 0.22
        self._t += 0.05

    def _paint_mic(self, p: QPainter):
        a = self._mic_alpha
        if a < 0.02:
            return
        c, r = self._mic_geometry()
        acc = QColor(T.AMBER)
        hot = QColor(T.HOT)
        over = self._hover is not None and self._over_mic(self._hover)
        if self._listening:
            pulse = 0.5 + 0.5 * math.sin(self._t * 6)
            g = QRadialGradient(c, r * 1.7)
            ga = QColor(acc)
            ga.setAlphaF(0.45 * a)
            g.setColorAt(0, ga)
            g.setColorAt(1, QColor(0, 0, 0, 0))
            p.setPen(Qt.NoPen)
            p.setBrush(g)
            p.drawEllipse(c, r * 1.7, r * 1.7)
            rp = QColor(hot)
            rp.setAlphaF(a * (0.4 + 0.6 * pulse))
            p.setPen(QPen(rp, 2))
            p.setBrush(Qt.NoBrush)
            p.drawEllipse(c, r + 4 + 4 * pulse, r + 4 + 4 * pulse)
        fill = T.qc(T.BG)
        fill.setAlphaF(0.78 * a)
        ring = QColor(acc)
        ring.setAlphaF(a * (1.0 if over or self._listening else 0.75))
        p.setBrush(fill)
        p.setPen(QPen(ring, 2 if over else 1.5))
        p.drawEllipse(c, r, r)
        glyph = QColor(hot)
        glyph.setAlphaF(a)
        p.setPen(QPen(glyph, max(1.5, r / 9), Qt.SolidLine, Qt.RoundCap))
        p.setBrush(Qt.NoBrush)
        w, h = r * 0.36, r * 0.62
        p.drawRoundedRect(QRectF(c.x() - w / 2, c.y() - h * 0.75, w, h), w / 2, w / 2)
        p.drawArc(QRectF(c.x() - r * 0.33, c.y() - r * 0.42, r * 0.66, r * 0.62), 200 * 16, 140 * 16)
        p.drawLine(QPointF(c.x(), c.y() + r * 0.2), QPointF(c.x(), c.y() + r * 0.38))

    def _frame(self):
        self._update_mic()
        if not self.isVisible() or not self._ensure_gl():
            self.update()
            return
        dpr = self.devicePixelRatioF()
        W = H = max(16, int(self.width() * dpr))
        if not self._ctx.makeCurrent(self._surface):
            return
        try:
            if self._out is None or (self._out.w, self._out.h) != (W, H):
                if self._out is not None:
                    self._out.delete()
                self._out = RenderTarget(W, H)
            self.engine.render(self._out.fbo, W, H)
            GL.glBindFramebuffer(GL.GL_FRAMEBUFFER, self._out.fbo)
            raw = GL.glReadPixels(0, 0, W, H, GL.GL_RGBA, GL.GL_UNSIGNED_BYTE)
            GL.glBindFramebuffer(GL.GL_FRAMEBUFFER, 0)
        finally:
            self._ctx.doneCurrent()
        buf = np.frombuffer(raw if isinstance(raw, (bytes, bytearray)) else np.asarray(raw).tobytes(),
                            dtype=np.uint8).reshape(H, W, 4)[::-1].copy()
        img = QImage(buf.data, W, H, W * 4, QImage.Format_RGBA8888_Premultiplied).copy()
        img.setDevicePixelRatio(dpr)
        self._image = img
        self.update()

    def grab_image(self) -> QImage | None:
        """Latest rendered frame (used for testing / screenshots)."""
        return self._image

    # ------------------------------------------------------------ Qt events
    def showEvent(self, e):
        super().showEvent(e)
        fps = 30 if perf.on("mini_30fps") else min(60, max(20, self.cfg.fps))
        self._timer.start(max(16, int(1000 / fps)))

    def hideEvent(self, e):
        super().hideEvent(e)
        self._timer.stop()

    def paintEvent(self, e):
        if self._image is None:
            return
        p = QPainter(self)
        p.setCompositionMode(QPainter.CompositionMode_Source)
        p.drawImage(self.rect(), self._image)
        p.setCompositionMode(QPainter.CompositionMode_SourceOver)
        p.setRenderHint(QPainter.Antialiasing)
        self._paint_mic(p)
        p.end()

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._press_global = e.globalPosition().toPoint()
            self._press_offset = self._press_global - self.frameGeometry().topLeft()
            self._moved = False

    def mouseMoveEvent(self, e):
        self._hover = e.position()
        self.setCursor(Qt.PointingHandCursor if self._over_mic(self._hover) else Qt.ArrowCursor)
        if self._press_global is None:
            return
        g = e.globalPosition().toPoint()
        if not self._moved and (g - self._press_global).manhattanLength() > 5:
            self._moved = True
        if self._moved:
            self.move(g - self._press_offset)

    def mouseReleaseEvent(self, e):
        if e.button() != Qt.LeftButton or self._press_global is None:
            return
        self._press_global = None
        if self._moved:
            self._remember()
        elif self._over_mic(e.position()):
            self.talk_requested.emit()

    def mouseDoubleClickEvent(self, e):
        if e.button() == Qt.LeftButton and not self._over_mic(e.position()):
            self._press_global = None
            self.restore_requested.emit()

    def enterEvent(self, e):
        self._hover = e.position()

    def leaveEvent(self, e):
        self._hover = None
        self.setCursor(Qt.ArrowCursor)

    def wheelEvent(self, e):
        step = 20 if e.angleDelta().y() > 0 else -20
        self._resize_to(self.width() + step)

    def _resize_to(self, size: int):
        size = int(min(MAX_SIZE, max(MIN_SIZE, size)))
        c = self.geometry().center()
        self.setGeometry(QRect(c.x() - size // 2, c.y() - size // 2, size, size))
        self._remember()

    def contextMenuEvent(self, e):
        menu = QMenu(self)
        for text, fn in (("Talk", self.talk_requested.emit),
                         ("Restore JARVIS", self.restore_requested.emit)):
            a = QAction(text, menu)
            a.triggered.connect(fn)
            menu.addAction(a)
        size_menu = menu.addMenu("Size")
        for label, px in (("Small", 180), ("Medium", 260), ("Large", 380)):
            a = QAction(label, size_menu)
            a.triggered.connect(lambda _=False, px=px: self._resize_to(px))
            size_menu.addAction(a)
        menu.addSeparator()
        q = QAction("Quit JARVIS", menu)
        q.triggered.connect(self.quit_requested.emit)
        menu.addAction(q)
        menu.exec(e.globalPos())

    def shutdown(self):
        self._timer.stop()
        if self._ctx and self._surface and self._out is not None and self._ctx.makeCurrent(self._surface):
            try:
                self._out.delete()
            finally:
                self._ctx.doneCurrent()

