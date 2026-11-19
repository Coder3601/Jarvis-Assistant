"""Custom HUD widgets: framed panels, gauges, sparklines, chat log, mic button, hologram overlay."""
from __future__ import annotations

import datetime as dt
import html
import math
import random
from collections import deque

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import (QColor, QLinearGradient, QPainter, QPainterPath,
                           QPen, QRadialGradient, QTextCharFormat, QTextCursor)
from PySide6.QtWidgets import QFrame, QSizePolicy, QTextBrowser, QVBoxLayout, QWidget

from .. import perf
from . import theme as T


def amber(a: int = 255) -> QColor:
    c = QColor(T.AMBER)
    c.setAlpha(a)
    return c


# ---------------------------------------------------------------------------
class HudFrame(QFrame):
    """A translucent panel with angled corner brackets and a title strip."""

    def __init__(self, title: str = "", parent=None, tag: str = ""):
        super().__init__(parent)
        self.title = title.upper()
        self.tag = tag
        self.setAttribute(Qt.WA_StyledBackground, False)
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(14, 34 if title else 12, 14, 12)
        self.body.setSpacing(8)
        self._phase = random.random()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        cut = 12.0
        path = QPainterPath()
        path.moveTo(r.left() + cut, r.top())
        path.lineTo(r.right(), r.top())
        path.lineTo(r.right(), r.bottom() - cut)
        path.lineTo(r.right() - cut, r.bottom())
        path.lineTo(r.left(), r.bottom())
        path.lineTo(r.left(), r.top() + cut)
        path.closeSubpath()
        g = QLinearGradient(r.topLeft(), r.bottomLeft())
        g.setColorAt(0, T.qc(T.PANEL_TOP))
        g.setColorAt(1, T.qc(T.PANEL_BOT))
        p.fillPath(path, g)
        p.setPen(QPen(amber(55), 1))
        p.drawPath(path)
        # brackets
        p.setPen(QPen(amber(220), 2))
        L = 18
        p.drawLine(QPointF(r.left() + cut, r.top()), QPointF(r.left() + cut + L, r.top()))
        p.drawLine(QPointF(r.left(), r.top() + cut), QPointF(r.left(), r.top() + cut + L))
        p.drawLine(QPointF(r.right() - cut, r.bottom()), QPointF(r.right() - cut - L, r.bottom()))
        p.drawLine(QPointF(r.right(), r.bottom() - cut), QPointF(r.right(), r.bottom() - cut - L))
        if self.title:
            p.setFont(T.font(8.5, True, spacing=2.2))
            p.setPen(QColor(T.GOLD))
            p.drawText(QRectF(r.left() + 16, r.top() + 6, r.width() - 32, 18), Qt.AlignLeft | Qt.AlignVCenter,
                       self.title)
            if self.tag:
                p.setFont(T.font(7.5, mono=True))
                p.setPen(QColor(T.DIM))
                p.drawText(QRectF(r.left() + 16, r.top() + 6, r.width() - 32, 18),
                           Qt.AlignRight | Qt.AlignVCenter, self.tag)
            y = r.top() + 26
            p.setPen(QPen(amber(70), 1))
            p.drawLine(QPointF(r.left() + 14, y), QPointF(r.right() - 14, y))
            p.setPen(QPen(amber(200), 2))
            p.drawLine(QPointF(r.left() + 14, y), QPointF(r.left() + 54, y))
        p.end()


# ---------------------------------------------------------------------------
class ArcGauge(QWidget):
    def __init__(self, label: str, parent=None):
        super().__init__(parent)
        self.label = label
        self.value = 0.0
        self._shown = 0.0
        self.sub = ""
        self.setMinimumSize(110, 128)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._spin = 0.0
        t = QTimer(self)
        t.timeout.connect(self._tick)
        t.start(33)

    def set_value(self, v: float, sub: str = ""):
        self.value = max(0.0, min(100.0, v))
        self.sub = sub

    def _tick(self):
        if perf.on("pause_hidden") and not self.isVisible():
            return
        if perf.on("idle_redraw"):
            # redraw only while the value is still easing towards its target
            if abs(self.value - self._shown) < 0.05:
                if self._shown != self.value:
                    self._shown = self.value
                    self.update()
                return
            self._shown += (self.value - self._shown) * 0.15
            self.update()
            return
        self._shown += (self.value - self._shown) * 0.15
        self._spin = (self._spin + 0.8) % 360
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        side = min(self.width(), self.height() - 14)
        cx, cy = self.width() / 2, side / 2 + 2
        R = side / 2 - 6
        rect = QRectF(cx - R, cy - R, 2 * R, 2 * R)
        # outer tick ring
        p.setPen(QPen(amber(60), 1))
        for i in range(60):
            a = math.radians(i * 6 + self._spin * 0.2)
            r0 = R + 2 if i % 5 else R
            p.drawLine(QPointF(cx + math.cos(a) * r0, cy + math.sin(a) * r0),
                       QPointF(cx + math.cos(a) * (R + 5), cy + math.sin(a) * (R + 5)))
        # background arc
        start, span = 225, -270
        p.setPen(QPen(amber(40), 6, Qt.SolidLine, Qt.FlatCap))
        p.drawArc(rect.adjusted(6, 6, -6, -6), start * 16, span * 16)
        # value arc, segmented
        frac = self._shown / 100.0
        col = QColor(T.RED) if self._shown > 90 else QColor(T.AMBER)
        pen = QPen(col, 6, Qt.SolidLine, Qt.FlatCap)
        p.setPen(pen)
        segs = 27
        for i in range(int(segs * frac + 0.999)):
            a0 = start - i * 10
            ln = -8 if i < int(segs * frac) else -8 * ((segs * frac) % 1)
            p.drawArc(rect.adjusted(6, 6, -6, -6), int(a0 * 16), int(ln * 16))
        # inner rotating dashes
        p.setPen(QPen(amber(90), 1, Qt.DashLine))
        p.save()
        p.translate(cx, cy)
        p.rotate(self._spin)
        p.drawEllipse(QPointF(0, 0), R - 15, R - 15)
        p.restore()
        # value text
        p.setPen(QColor(T.HOT))
        p.setFont(T.font(max(9.0, side / 7.5), True))
        p.drawText(QRectF(cx - R, cy - R * 0.55, 2 * R, R * 0.75), Qt.AlignCenter, f"{self._shown:.0f}%")
        p.setPen(QColor(T.DIM))
        p.setFont(T.font(6.5, mono=True))
        p.drawText(QRectF(cx - R * 0.8, cy + R * 0.22, 1.6 * R, 14), Qt.AlignCenter,
                   p.fontMetrics().elidedText(self.sub, Qt.ElideRight, int(1.6 * R)))
        p.setPen(QColor(T.GOLD))
        p.setFont(T.font(7.5, True, spacing=1.5))
        p.drawText(QRectF(0, self.height() - 16, self.width(), 14), Qt.AlignCenter, self.label)
        p.end()


# ---------------------------------------------------------------------------
class Sparkline(QWidget):
    def __init__(self, labels=("DOWN", "UP"), parent=None, n=90):
        super().__init__(parent)
        self.labels = labels
        self.series = [deque([0.0] * n, maxlen=n) for _ in labels]
        self.readout = ["", ""]
        self.setMinimumHeight(70)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def push(self, values, readouts):
        for s, v in zip(self.series, values):
            s.append(v)
        self.readout = readouts
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(1, 16, -1, -1)
        p.setPen(QPen(amber(25), 1))
        for i in range(1, 4):
            y = r.top() + r.height() * i / 4
            p.drawLine(QPointF(r.left(), y), QPointF(r.right(), y))
        for i in range(1, 12):
            x = r.left() + r.width() * i / 12
            p.drawLine(QPointF(x, r.top()), QPointF(x, r.bottom()))
        peak = max(1.0, *(max(s) for s in self.series))
        cols = [QColor(T.AMBER), QColor(T.HOT)]
        for idx, s in enumerate(self.series):
            path = QPainterPath()
            n = len(s)
            for i, v in enumerate(s):
                x = r.left() + r.width() * i / (n - 1)
                y = r.bottom() - r.height() * (v / peak) * 0.92
                path.moveTo(x, y) if i == 0 else path.lineTo(x, y)
            if idx == 0:
                fill = QPainterPath(path)
                fill.lineTo(r.right(), r.bottom())
                fill.lineTo(r.left(), r.bottom())
                g = QLinearGradient(r.topLeft(), r.bottomLeft())
                g.setColorAt(0, amber(90))
                g.setColorAt(1, amber(0))
                p.fillPath(fill, g)
            c = cols[idx % 2]
            p.setPen(QPen(c, 1.4))
            p.drawPath(path)
        p.setFont(T.font(7.5, mono=True))
        x = 0
        for idx, (lab, val) in enumerate(zip(self.labels, self.readout)):
            p.setPen(cols[idx % 2])
            txt = f"{lab} {val}"
            p.drawText(QRectF(x, 0, self.width() / 2, 14), Qt.AlignLeft, txt)
            x += self.width() / 2
        p.end()


# ---------------------------------------------------------------------------
class StatusChip(QWidget):
    def __init__(self, label: str, parent=None):
        super().__init__(parent)
        self.label = label
        self.value = "—"
        self.level = "off"   # ok | warn | off | busy
        self.setMinimumWidth(130)
        self.setFixedHeight(34)
        self._t = 0.0
        tm = QTimer(self)
        tm.timeout.connect(self._tick)
        tm.start(50)

    def set(self, value: str, level: str):
        self.value, self.level = value, level
        self.setToolTip(f"{self.label}: {value}")
        self.update()

    def _tick(self):
        if perf.on("pause_hidden") and not self.isVisible():
            return
        self._t += 0.05
        if self.level in ("busy", "warn"):
            self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        col = {"ok": QColor(T.GOLD), "warn": QColor(T.RED), "busy": QColor(T.HOT), "off": QColor(T.FAINT)}[self.level]
        a = 1.0 if self.level in ("ok", "off") else 0.45 + 0.55 * abs(math.sin(self._t * 3))
        c = QColor(col)
        c.setAlphaF(a)
        p.setBrush(c)
        p.setPen(Qt.NoPen)
        p.drawEllipse(QPointF(8, 17), 4, 4)
        glow = QRadialGradient(QPointF(8, 17), 10)
        g2 = QColor(col)
        g2.setAlpha(int(90 * a))
        glow.setColorAt(0, g2)
        glow.setColorAt(1, QColor(0, 0, 0, 0))
        p.setBrush(glow)
        p.drawEllipse(QPointF(8, 17), 10, 10)
        p.setFont(T.font(7, True, spacing=1.6))
        p.setPen(QColor(T.DIM))
        p.drawText(QRectF(20, 2, self.width() - 20, 14), Qt.AlignLeft | Qt.AlignVCenter, self.label)
        p.setFont(T.font(8.5, mono=True))
        p.setPen(QColor(T.HOT) if self.level != "off" else QColor(T.DIM))
        p.drawText(QRectF(20, 16, self.width() - 20, 16), Qt.AlignLeft | Qt.AlignVCenter,
                   p.fontMetrics().elidedText(self.value, Qt.ElideRight, self.width() - 22))
        p.end()


# ---------------------------------------------------------------------------
class ChatLog(QTextBrowser):
    """Conversation transcript with streaming support."""

    def __init__(self, parent=None, assistant_name="JARVIS"):
        super().__init__(parent)
        self.assistant_name = assistant_name.upper()
        self.setOpenExternalLinks(True)
        self.setReadOnly(True)
        self.document().setDocumentMargin(4)
        self.setFont(T.font(10.5))
        self._streaming = False
        self._fmt = QTextCharFormat()
        self._fmt.setForeground(QColor(T.REPLY))
        self._fmt.setFont(T.font(10.5))

    def _at_bottom(self) -> bool:
        sb = self.verticalScrollBar()
        return sb.value() >= sb.maximum() - 30

    def _scroll(self, force=False):
        if force or self._at_bottom():
            self.verticalScrollBar().setValue(self.verticalScrollBar().maximum())

    def _header(self, who: str, color: str, extra: str = "") -> str:
        ts = dt.datetime.now().strftime("%H:%M:%S")
        return (f'<p style="margin-top:12px; margin-bottom:2px;"><span style="color:{color}; font-weight:600; '
                f'letter-spacing:2px; font-size:8pt;">{who}</span> <span style="color:{T.FAINT}; font-size:8pt;'
                f' font-family:{T.mono_family()};">{ts}{extra}</span></p>')

    def add_user(self, text: str, source: str = "text"):
        self.end_reply()
        icon = " · VOICE" if source == "voice" else ""
        self.append(self._header("YOU", T.DIM, icon))
        self.append(f'<p style="margin:0; color:{T.GOLD};">{html.escape(text)}</p>')
        self._scroll(True)

    def begin_reply(self):
        self.end_reply()
        self._fmt.setForeground(QColor(T.REPLY))
        self.append(self._header(self.assistant_name, T.AMBER))
        self.append("")
        self._streaming = True
        self._scroll(True)

    def add_token(self, token: str):
        if not self._streaming:
            self.begin_reply()
        bottom = self._at_bottom()
        c = self.textCursor()
        c.movePosition(QTextCursor.End)
        c.insertText(token, self._fmt)
        if bottom:
            self._scroll(True)

    def end_reply(self):
        self._streaming = False

    def add_note(self, text: str, level: str = "info"):
        if self._streaming:
            return  # keep the reply contiguous; notes also go to the activity feed
        col = {"error": T.RED, "tool": T.DIM, "info": T.FAINT}.get(level, T.FAINT)
        mark = {"error": "!", "tool": ">", "info": "-"}.get(level, "-")
        self.append(f'<p style="margin-top:6px; color:{col}; font-size:8.5pt; font-family:{T.mono_family()};">'
                    f'{mark} {html.escape(text)}</p>')
        self._scroll()

    def add_card(self, title: str, sections):
        """A formatted block (e.g. the morning briefing) with headings and clickable links."""
        self.end_reply()
        body = "".join(
            f'<p style="margin-top:8px; margin-bottom:2px; color:{T.GOLD}; font-size:8pt; letter-spacing:2px;">'
            f'{html.escape(h.upper())}</p><div style="color:{T.HOT}; font-size:9.5pt;">{content}</div>'
            for h, content in sections)
        body = body.replace("<a ", f'<a style="color:{T.GOLD};" ')
        self.append(f'<p style="margin-top:10px; color:{T.AMBER}; font-weight:600; letter-spacing:2px; '
                    f'font-size:8pt;">{html.escape(title.upper())}</p>{body}')
        self._scroll(True)

    def add_history(self, role: str, text: str, ts: float):
        who = "YOU" if role == "user" else self.assistant_name
        stamp = dt.datetime.fromtimestamp(ts).strftime("%d %b %H:%M")
        col = T.FAINT
        self.append(f'<p style="margin-top:8px; margin-bottom:0; color:{col}; font-size:8pt; letter-spacing:1px;">'
                    f'{who} · {stamp}</p>')
        self.append(f'<p style="margin:0; color:{T.DIM};">{html.escape(text)}</p>')


# ---------------------------------------------------------------------------
class MicButton(QWidget):
    clicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(58, 58)
        self.setCursor(Qt.PointingHandCursor)
        self.active = False
        self.level = 0.0
        self._t = 0.0
        self._hover = False
        tm = QTimer(self)
        tm.timeout.connect(self._tick)
        tm.start(33)
        self.setToolTip("Talk to JARVIS (Ctrl+Space / F2)")

    def set_active(self, on: bool):
        self.active = on

    def set_level(self, v: float):
        self.level = max(self.level * 0.6, v)

    def _tick(self):
        if perf.on("pause_hidden") and not self.isVisible():
            return
        self._t += 0.033
        self.level *= 0.9
        if perf.on("idle_redraw"):
            # an idle mic button only needs its slow rotating arc: ~6 fps instead of 30
            busy = self.active or self.level > 0.01
            self._idle_n = (getattr(self, "_idle_n", 0) + 1) % 5
            if not busy and self._idle_n:
                return
        self.update()

    def enterEvent(self, e):
        self._hover = True
        self.update()

    def leaveEvent(self, e):
        self._hover = False
        self.update()

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton and self.rect().contains(e.position().toPoint()):
            self.clicked.emit()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        c = QPointF(self.width() / 2, self.height() / 2)
        R = self.width() / 2 - 3
        base = 120 if self._hover else 70
        if self.active:
            pulse = 0.5 + 0.5 * math.sin(self._t * 6)
            g = QRadialGradient(c, R)
            g.setColorAt(0, amber(int(80 + 120 * self.level)))
            g.setColorAt(1, amber(0))
            p.setBrush(g)
            p.setPen(Qt.NoPen)
            p.drawEllipse(c, R, R)
            p.setPen(QPen(QColor(T.HOT), 2))
            p.setBrush(Qt.NoBrush)
            rr = R - 4 + 3 * pulse
            p.drawEllipse(c, rr, rr)
        p.setPen(QPen(amber(base + 80), 1.5))
        p.setBrush(amber(25 if not self.active else 60))
        p.drawEllipse(c, R - 7, R - 7)
        # rotating arc
        p.setPen(QPen(amber(220), 2, Qt.SolidLine, Qt.FlatCap))
        a = int((self._t * (240 if self.active else 40)) % 360)
        p.drawArc(QRectF(c.x() - R, c.y() - R, 2 * R, 2 * R), a * 16, 70 * 16)
        p.drawArc(QRectF(c.x() - R, c.y() - R, 2 * R, 2 * R), (a + 180) * 16, 70 * 16)
        # microphone glyph
        p.setPen(QPen(QColor(T.HOT), 2, Qt.SolidLine, Qt.RoundCap))
        p.setBrush(Qt.NoBrush)
        mic = QRectF(c.x() - 5, c.y() - 12, 10, 16)
        p.drawRoundedRect(mic, 5, 5)
        p.drawArc(QRectF(c.x() - 9, c.y() - 6, 18, 14), 200 * 16, 140 * 16)
        p.drawLine(QPointF(c.x(), c.y() + 8), QPointF(c.x(), c.y() + 12))
        p.end()


# ---------------------------------------------------------------------------
class HologramOverlay(QWidget):
    """Transparent HUD drawn over the 3D core: reticle, readouts, state and waveform."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WA_NoSystemBackground)
        self.state = "idle"
        self.level_hist = deque([0.0] * 64, maxlen=64)
        self.fps = 0.0
        self.model = ""
        self._t = 0.0
        self._hex = [self._rand_hex() for _ in range(9)]
        tm = QTimer(self)
        tm.timeout.connect(self._tick)
        tm.start(40)

    @staticmethod
    def _rand_hex():
        return " ".join(f"{random.randint(0, 255):02X}" for _ in range(4))

    def push_level(self, v: float):
        self._pending = max(getattr(self, "_pending", 0.0), v)

    def _tick(self):
        if perf.on("pause_hidden") and not self.isVisible():
            return
        self._t += 0.04
        self.level_hist.append(getattr(self, "_pending", 0.0))
        self._pending = 0.0
        if perf.on("idle_redraw") and self.state in ("idle", "offline") and max(self.level_hist) < 0.01:
            # standing by: refresh the HUD a few times a second instead of 25
            self._idle_n = (getattr(self, "_idle_n", 0) + 1) % 6
            if self._idle_n:
                return
        if random.random() < 0.25:
            self._hex[random.randrange(len(self._hex))] = self._rand_hex()
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        cx, cy = w / 2, h / 2
        state_col = {"idle": QColor(T.GOLD), "listening": QColor(T.HOT), "thinking": QColor(T.AMBER),
                     "speaking": QColor(T.HOT), "error": QColor(T.RED), "offline": QColor(T.DIM)}.get(
            self.state, QColor(T.GOLD))

        # corner reticles
        p.setPen(QPen(amber(150), 1.2))
        m, L = 16, 26
        for (x, y, dx, dy) in ((m, m, 1, 1), (w - m, m, -1, 1), (m, h - m, 1, -1), (w - m, h - m, -1, -1)):
            p.drawLine(QPointF(x, y), QPointF(x + L * dx, y))
            p.drawLine(QPointF(x, y), QPointF(x, y + L * dy))

        # side scales
        p.setPen(QPen(amber(70), 1))
        for i in range(41):
            y = cy - 160 + i * 8
            ln = 10 if i % 5 == 0 else 5
            p.drawLine(QPointF(m + 4, y), QPointF(m + 4 + ln, y))
            p.drawLine(QPointF(w - m - 4, y), QPointF(w - m - 4 - ln, y))
        mark = cy + math.sin(self._t * 0.7) * 120
        p.setPen(QPen(QColor(T.HOT), 1.5))
        p.drawLine(QPointF(m + 18, mark), QPointF(m + 30, mark))
        p.drawLine(QPointF(w - m - 18, cy - math.sin(self._t * 0.5) * 120), QPointF(w - m - 30, cy - math.sin(self._t * 0.5) * 120))

        # top-left identification block
        p.setFont(T.font(8, True, spacing=2))
        p.setPen(QColor(T.GOLD))
        p.drawText(QPointF(m + 34, m + 14), "HOLOGRAPHIC CORE")
        p.setFont(T.font(7.5, mono=True))
        p.setPen(QColor(T.DIM))
        p.drawText(QPointF(m + 34, m + 30), f"MK-VII // {self.model or 'NO MODEL'}")
        p.drawText(QPointF(m + 34, m + 44), f"RENDER {self.fps:4.0f} FPS")

        # top-right telemetry hex
        p.setFont(T.font(7.5, mono=True))
        for i, hx in enumerate(self._hex):
            p.setPen(amber(200 if i == int(self._t * 3) % len(self._hex) else 90))
            p.drawText(QRectF(w - m - 180, m + 4 + i * 13, 150, 13), Qt.AlignRight, f"{0x3F0 + i * 16:04X}  {hx}")

        # bottom state readout + waveform
        bw = min(420.0, w * 0.6)
        base_y = h - 46
        bars = len(self.level_hist)
        step = bw / bars
        x0 = cx - bw / 2
        for i, v in enumerate(self.level_hist):
            # mirrored, centre-weighted bars
            k = 1.0 - abs(i - bars / 2) / (bars / 2) * 0.5
            amp = 2 + v * 26 * k + (1.5 if self.state in ("thinking",) and (i + int(self._t * 20)) % 12 == 0 else 0)
            c = QColor(state_col)
            c.setAlpha(int(90 + 160 * min(1.0, v * 2)))
            p.setPen(Qt.NoPen)
            p.setBrush(c)
            p.drawRect(QRectF(x0 + i * step + 1, base_y - amp, max(1.0, step - 2), amp * 2))
        p.setFont(T.font(10, True, spacing=5))
        p.setPen(state_col)
        label = {"idle": "STANDING BY", "listening": "LISTENING", "thinking": "PROCESSING",
                 "speaking": "SPEAKING", "error": "FAULT DETECTED", "offline": "AI CORE OFFLINE"}.get(self.state,
                                                                                                    self.state.upper())
        if self.state == "thinking":
            label += "." * (int(self._t * 3) % 4)
        p.drawText(QRectF(0, h - 30, w, 20), Qt.AlignCenter, label)
        p.setPen(QPen(amber(110), 1))
        p.drawLine(QPointF(cx - bw / 2, h - 22), QPointF(cx - 90, h - 22))
        p.drawLine(QPointF(cx + 90, h - 22), QPointF(cx + bw / 2, h - 22))
        p.end()
