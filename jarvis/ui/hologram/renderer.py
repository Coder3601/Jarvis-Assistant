"""Hardware-accelerated holographic core (QOpenGLWidget + GLSL 3.30).

Pipeline per frame
    1. Scene pass  -> HDR multisampled FBO, additive blending, no depth writes
    2. Resolve     -> HDR texture
    3. Bloom       -> bright pass (1/2) -> blur, downsample (1/4) -> blur, (1/8) -> blur
    4. Composite   -> exposure curve, warm grading, background haze, scanlines,
                      grain, vignette -> default framebuffer
"""
from __future__ import annotations

import ctypes
from pathlib import Path
import logging
import math
import time
from dataclasses import dataclass, field
from typing import Callable

import numpy as np
from PySide6.QtCore import QElapsedTimer, QPointF, Qt, QTimer, Signal
from PySide6.QtGui import QSurfaceFormat
from PySide6.QtOpenGLWidgets import QOpenGLWidget

import os

import OpenGL

# Per-call error checking costs a lot of CPU in Python; shader/FBO status is checked explicitly instead.
OpenGL.ERROR_CHECKING = bool(os.environ.get("JARVIS_GL_DEBUG"))
from OpenGL import GL  # noqa: E402

from . import geometry as G
from . import geometry as GC  # noqa: F401  (GeoCache class lives here)
from . import shaders as S
from ... import perf
from ...config import data_dir
from .glmath import chain, look_at, perspective, rx, ry, rz, scale, translate

log = logging.getLogger(__name__)

TAU = 2 * math.pi

# HDR colour palette (linear, values > 1 feed the bloom)
AMBER = (1.00, 0.50, 0.12)
GOLD = (1.00, 0.70, 0.28)
HOT = (1.00, 0.84, 0.52)
DEEP = (1.00, 0.36, 0.05)
PALE = (1.00, 0.78, 0.45)

# visual targets per assistant state
STATE_PROFILES = {
    #             energy speed  tint                 core
    "idle":      (0.35, 1.00, (1.00, 1.00, 1.00), 1.00),
    "listening": (0.70, 1.25, (1.00, 1.03, 1.08), 1.25),
    "thinking":  (0.85, 2.40, (1.05, 1.00, 0.92), 1.15),
    "speaking":  (1.00, 1.50, (1.06, 1.02, 0.95), 1.45),
    "error":     (0.60, 0.60, (1.25, 0.55, 0.45), 0.90),
    "offline":   (0.15, 0.40, (0.75, 0.70, 0.70), 0.55),
}


# per-scene look: background, highlight grading and the "white-hot" colour
SCENE_THEMES = {
    "jarvis": dict(white=(1.0, 0.95, 0.86), bg_base=(0.010, 0.007, 0.006), bg_haze=(0.075, 0.035, 0.010),
                   bg_glow=(0.020, 0.010, 0.004), grid=(1.0, 0.55, 0.15), grade=(0.92, 0.97, 1.05)),
    "rufus": dict(white=(0.86, 1.0, 0.9), bg_base=(0.004, 0.012, 0.007), bg_haze=(0.010, 0.070, 0.030),
                  bg_glow=(0.004, 0.024, 0.010), grid=(0.30, 1.0, 0.55), grade=(1.05, 0.93, 1.06)),
    "atom": dict(white=(0.86, 0.96, 1.0), bg_base=(0.004, 0.008, 0.016), bg_haze=(0.010, 0.040, 0.090),
                 bg_glow=(0.004, 0.014, 0.032), grid=(0.25, 0.65, 1.0), grade=(1.06, 0.98, 0.90)),
}

# R.U.F.U.S palette (greens)
GREEN = (0.20, 1.00, 0.45)
LIME = (0.50, 1.00, 0.55)
MINT = (0.80, 1.00, 0.86)
FOREST = (0.05, 0.60, 0.25)

# A.T.O.M palette (cool blues)
CYAN = (0.22, 0.70, 1.00)
ICE = (0.45, 0.85, 1.00)
FROST = (0.78, 0.94, 1.00)
NAVY = (0.10, 0.35, 1.00)


def _geometry_cache_path(scene: str, quality: str):
    """Cache file name includes a hash of the geometry/scene code so edits invalidate it."""
    import hashlib
    h = hashlib.md5()
    for f in (Path(__file__), Path(G.__file__)):
        try:
            h.update(f.read_bytes())
        except OSError:
            pass
    return data_dir() / "cache" / f"holo_{scene}_{quality}_{h.hexdigest()[:10]}.npz"


def default_surface_format(samples: int = 0) -> QSurfaceFormat:
    fmt = QSurfaceFormat()
    fmt.setVersion(3, 3)
    fmt.setProfile(QSurfaceFormat.CoreProfile)
    fmt.setDepthBufferSize(24)
    fmt.setSwapInterval(1)
    if samples:
        fmt.setSamples(samples)
    return fmt


# ---------------------------------------------------------------------------
# GL helpers
# ---------------------------------------------------------------------------
class Program:
    def __init__(self, vs: str, fs: str, name: str):
        self.name = name
        self.id = GL.glCreateProgram()
        shaders = []
        for src, kind in ((vs, GL.GL_VERTEX_SHADER), (fs, GL.GL_FRAGMENT_SHADER)):
            sh = GL.glCreateShader(kind)
            GL.glShaderSource(sh, src)
            GL.glCompileShader(sh)
            if not GL.glGetShaderiv(sh, GL.GL_COMPILE_STATUS):
                raise RuntimeError(f"{name} shader compile error:\n{GL.glGetShaderInfoLog(sh).decode()}")
            GL.glAttachShader(self.id, sh)
            shaders.append(sh)
        GL.glLinkProgram(self.id)
        if not GL.glGetProgramiv(self.id, GL.GL_LINK_STATUS):
            raise RuntimeError(f"{name} link error:\n{GL.glGetProgramInfoLog(self.id).decode()}")
        for sh in shaders:
            GL.glDeleteShader(sh)
        self._loc: dict[str, int] = {}
        # remember the last value sent for each uniform and skip identical updates
        self._sent: dict[int, object] | None = {} if perf.on("gl_cache") else None

    def loc(self, name: str) -> int:
        if name not in self._loc:
            self._loc[name] = GL.glGetUniformLocation(self.id, name)
        return self._loc[name]

    def use(self):
        GL.glUseProgram(self.id)

    def f(self, name, *vals):
        l = self.loc(name)
        if l < 0:
            return
        if self._sent is not None:
            if self._sent.get(l) == vals:
                return
            self._sent[l] = vals
        if len(vals) == 1:
            GL.glUniform1f(l, float(vals[0]))
        elif len(vals) == 2:
            GL.glUniform2f(l, float(vals[0]), float(vals[1]))
        elif len(vals) == 3:
            GL.glUniform3f(l, float(vals[0]), float(vals[1]), float(vals[2]))

    def i(self, name, val: int):
        l = self.loc(name)
        if l >= 0:
            if self._sent is not None:
                if self._sent.get(l) == ("i", val):
                    return
                self._sent[l] = ("i", val)
            GL.glUniform1i(l, int(val))

    def m4(self, name, m: np.ndarray):
        l = self.loc(name)
        if l >= 0:
            data = np.ascontiguousarray(m, dtype=np.float32)
            if self._sent is not None:
                key = data.tobytes()
                if self._sent.get(l) == key:
                    return
                self._sent[l] = key
            GL.glUniformMatrix4fv(l, 1, GL.GL_TRUE, data)


class Mesh:
    def __init__(self, data: np.ndarray, layout: list[int], primitive):
        self.primitive = primitive
        self.count = len(data)
        self.vao = GL.glGenVertexArrays(1)
        self.vbo = GL.glGenBuffers(1)
        GL.glBindVertexArray(self.vao)
        GL.glBindBuffer(GL.GL_ARRAY_BUFFER, self.vbo)
        data = np.ascontiguousarray(data, dtype=np.float32)
        GL.glBufferData(GL.GL_ARRAY_BUFFER, data.nbytes, data, GL.GL_STATIC_DRAW)
        stride = sum(layout) * 4
        off = 0
        for idx, n in enumerate(layout):
            GL.glEnableVertexAttribArray(idx)
            GL.glVertexAttribPointer(idx, n, GL.GL_FLOAT, GL.GL_FALSE, stride, ctypes.c_void_p(off))
            off += n * 4
        GL.glBindVertexArray(0)

    def draw(self):
        if self.count:
            GL.glBindVertexArray(self.vao)
            GL.glDrawArrays(self.primitive, 0, self.count)


class RenderTarget:
    """Colour texture FBO (optionally with a multisampled sibling)."""

    def __init__(self, w: int, h: int, samples: int = 0):
        self.w, self.h = max(1, w), max(1, h)
        self.tex = GL.glGenTextures(1)
        GL.glBindTexture(GL.GL_TEXTURE_2D, self.tex)
        GL.glTexImage2D(GL.GL_TEXTURE_2D, 0, GL.GL_RGBA16F, self.w, self.h, 0, GL.GL_RGBA, GL.GL_FLOAT, None)
        for p, v in ((GL.GL_TEXTURE_MIN_FILTER, GL.GL_LINEAR), (GL.GL_TEXTURE_MAG_FILTER, GL.GL_LINEAR),
                     (GL.GL_TEXTURE_WRAP_S, GL.GL_CLAMP_TO_EDGE), (GL.GL_TEXTURE_WRAP_T, GL.GL_CLAMP_TO_EDGE)):
            GL.glTexParameteri(GL.GL_TEXTURE_2D, p, v)
        self.fbo = GL.glGenFramebuffers(1)
        GL.glBindFramebuffer(GL.GL_FRAMEBUFFER, self.fbo)
        GL.glFramebufferTexture2D(GL.GL_FRAMEBUFFER, GL.GL_COLOR_ATTACHMENT0, GL.GL_TEXTURE_2D, self.tex, 0)
        if GL.glCheckFramebufferStatus(GL.GL_FRAMEBUFFER) != GL.GL_FRAMEBUFFER_COMPLETE:
            raise RuntimeError("HDR framebuffer incomplete")
        self.ms_fbo = None
        self.ms_rbo = None
        if samples > 1:
            try:
                self.ms_rbo = GL.glGenRenderbuffers(1)
                GL.glBindRenderbuffer(GL.GL_RENDERBUFFER, self.ms_rbo)
                GL.glRenderbufferStorageMultisample(GL.GL_RENDERBUFFER, samples, GL.GL_RGBA16F, self.w, self.h)
                self.ms_fbo = GL.glGenFramebuffers(1)
                GL.glBindFramebuffer(GL.GL_FRAMEBUFFER, self.ms_fbo)
                GL.glFramebufferRenderbuffer(GL.GL_FRAMEBUFFER, GL.GL_COLOR_ATTACHMENT0, GL.GL_RENDERBUFFER, self.ms_rbo)
                if GL.glCheckFramebufferStatus(GL.GL_FRAMEBUFFER) != GL.GL_FRAMEBUFFER_COMPLETE:
                    raise RuntimeError("MSAA incomplete")
            except Exception as exc:  # pragma: no cover - driver dependent
                log.warning("MSAA unavailable (%s); continuing without it", exc)
                self._delete_ms()

    def _delete_ms(self):
        if self.ms_fbo:
            GL.glDeleteFramebuffers(1, [self.ms_fbo])
        if self.ms_rbo:
            GL.glDeleteRenderbuffers(1, [self.ms_rbo])
        self.ms_fbo = self.ms_rbo = None

    def bind_draw(self):
        GL.glBindFramebuffer(GL.GL_FRAMEBUFFER, self.ms_fbo or self.fbo)
        GL.glViewport(0, 0, self.w, self.h)

    def resolve(self):
        if self.ms_fbo:
            GL.glBindFramebuffer(GL.GL_READ_FRAMEBUFFER, self.ms_fbo)
            GL.glBindFramebuffer(GL.GL_DRAW_FRAMEBUFFER, self.fbo)
            GL.glBlitFramebuffer(0, 0, self.w, self.h, 0, 0, self.w, self.h, GL.GL_COLOR_BUFFER_BIT, GL.GL_NEAREST)

    def delete(self):
        self._delete_ms()
        GL.glDeleteFramebuffers(1, [self.fbo])
        GL.glDeleteTextures(1, [self.tex])


# ---------------------------------------------------------------------------
# Scene description
# ---------------------------------------------------------------------------
@dataclass
class Ctx:
    t: float          # animation time (speed-scaled, integrated)
    rt: float         # real time
    energy: float
    level: float      # audio level 0..1
    core: float


@dataclass
class Item:
    mesh: Mesh
    prog: str                     # "geo" | "pts" | "glow"
    color: tuple
    alpha: float
    model: Callable[[Ctx], np.ndarray]
    u: dict = field(default_factory=dict)
    alpha_fn: Callable[[Ctx], float] | None = None


GEO_DEFAULTS = dict(uRepeat=1.0, uSpeed=0.0, uSeedPhase=0.0, uWidth=0.06, uBase=1.0, uGain=0.0,
                    uFlicker=0.0, uPlates=0.0, uPulse=0.0, uJitter=0.0)


class HologramEngine:
    """Renders the holographic core into any framebuffer (a widget, or offscreen for the mini hologram).

    All GL calls must happen with the owning context current.
    """

    def __init__(self, quality: str = "high", transparent: bool = False, scene: str = "jarvis"):
        self.quality = quality
        self.transparent = transparent
        self.scene = scene if scene in SCENE_THEMES else "jarvis"
        self.theme = SCENE_THEMES[self.scene]
        # the floating mini hologram is small and sits over other apps: tone it down
        self.exposure = 0.75 if transparent else 1.25
        self.bloom_mul = 0.45 if transparent else 1.0
        self.samples = {"low": 0, "medium": 2, "high": 4, "ultra": 8}.get(quality, 4)
        self.particle_mul = {"low": 0.4, "medium": 0.7, "high": 1.0, "ultra": 1.6}.get(quality, 1.0)

        self.items: list[Item] = []
        self.programs: dict[str, Program] = {}
        self.ok = False
        self.error: str | None = None
        self._targets = None

        # animation state
        self._clock = QElapsedTimer()
        self._last = 0.0
        self.anim_t = 0.0
        self.state = "idle"
        self.energy = 0.35
        self.speed = 1.0
        self.tint = np.array((1.0, 1.0, 1.0))
        self.core_gain = 1.0
        self.audio_level = 0.0
        self._target_level = 0.0
        self._synthetic_speech = False

        # camera
        self.yaw = 0.0
        self.pitch = 0.22
        self.base_dist = 4.35 if self.scene == "rufus" else 3.75
        self.dist = self.base_dist
        self.dragging = False
        self._vel = np.zeros(2)
        self._user_cam = False

    # ------------------------------------------------------------------ API
    def set_state(self, state: str):
        self.state = state if state in STATE_PROFILES else "idle"
        self._synthetic_speech = self.state == "speaking"

    def set_audio_level(self, level: float):
        self._target_level = max(0.0, min(1.0, float(level)))
        self._synthetic_speech = False

    def set_synthetic_speech(self, on: bool):
        self._synthetic_speech = on

    # ------------------------------------------------------------ GL setup
    def init_gl(self):
        try:
            self._init_gl()
            self.ok = True
        except Exception as exc:  # pragma: no cover - driver dependent
            log.exception("Hologram initialisation failed")
            self.error = str(exc)
            self.ok = False
        self._clock.start()

    def _init_gl(self):
        ver = GL.glGetString(GL.GL_VERSION)
        log.info("OpenGL %s | %s", ver.decode() if ver else "?", (GL.glGetString(GL.GL_RENDERER) or b"?").decode())
        self.programs = {
            "geo": Program(S.GEO_VS, S.GEO_FS, "geo"),
            "pts": Program(S.PTS_VS, S.PTS_FS, "pts"),
            "glow": Program(S.GLOW_VS, S.GLOW_FS, "glow"),
            "orb": Program(S.ORB_VS, S.ORB_FS, "orb"),
            "bright": Program(S.QUAD_VS, S.BRIGHT_FS, "bright"),
            "down": Program(S.QUAD_VS, S.DOWN_FS, "down"),
            "blur": Program(S.QUAD_VS, S.BLUR_FS, "blur"),
            "comp": Program(S.QUAD_VS, S.COMPOSITE_FS, "composite"),
        }
        self._quad_vao = GL.glGenVertexArrays(1)
        self._build_scene()

    def resize(self, W: int, H: int):
        if not self.ok:
            return
        W, H = max(8, int(W)), max(8, int(H))
        if self._targets:
            for t in self._targets.values():
                t.delete()
        self._targets = {
            "scene": RenderTarget(W, H, self.samples),
            "b0a": RenderTarget(W // 2, H // 2), "b0b": RenderTarget(W // 2, H // 2),
            "b1a": RenderTarget(W // 4, H // 4), "b1b": RenderTarget(W // 4, H // 4),
            "b2a": RenderTarget(W // 8, H // 8), "b2b": RenderTarget(W // 8, H // 8),
        }

    # -------------------------------------------------------------- scene
    def _build_scene(self):
        self._gc = GC.GeoCache(_geometry_cache_path(self.scene, self.quality), perf.on("geometry_cache"))
        if self.scene == "atom":
            self._build_atom_scene()
        elif self.scene == "rufus":
            self._build_rufus_scene()
        else:
            self._build_jarvis_scene()
        self._gc.save()

    def _build_atom_scene(self):
        """A.T.O.M: geodesic node sphere, floating glowing orbs and electron orbits around a nucleus."""
        GC = self._gc
        rng = np.random.default_rng(11)
        L, T, P = GL.GL_LINES, GL.GL_TRIANGLES, GL.GL_POINTS
        geo = [3, 1, 1, 1]
        orb_layout = [3, 2, 1, 1]
        add = self.items.append
        pm = self.particle_mul

        def geo_mesh(arr, prim):
            return Mesh(arr, geo, prim)

        # geodesic node sphere
        sphere_rot = lambda c: chain(ry(c.t * 0.05), rx(0.25 + 0.05 * math.sin(c.t * 0.2)))
        edges, nodes = GC.icosphere_edges(1.0, 2, rng)
        add(Item(geo_mesh(edges, L), "geo", CYAN, 0.42, sphere_rot,
                 dict(uGain=2.6, uBase=0.55, uSpeed=0.3, uSeedPhase=1.0, uWidth=0.08)))
        add(Item(Mesh(GC.orb_quads(nodes, rng.uniform(0.045, 0.07, len(nodes)), rng.random(len(nodes))),
                      orb_layout, T), "orb", ICE, 0.95, sphere_rot, dict(uRing=0.0)))
        inner_rot = lambda c: chain(ry(-c.t * 0.08), rz(0.4))
        e2, n2 = GC.icosphere_edges(0.78, 1, rng)
        add(Item(geo_mesh(e2, L), "geo", NAVY, 0.30, inner_rot, dict(uPulse=0.6)))
        add(Item(Mesh(GC.orb_quads(n2, np.full(len(n2), 0.03), rng.random(len(n2))), orb_layout, T),
                 "orb", CYAN, 0.5, inner_rot, dict(uRing=0.0)))

        # floating orbs around the sphere, loosely linked
        cloud_rot = lambda c: chain(ry(c.t * 0.025))
        orbs = GC.floating_orbs(int(22 * min(1.3, max(0.6, pm))), 1.12, 1.6, rng)
        add(Item(geo_mesh(GC.nearest_links(orbs, 2, 0.9, rng, 0.5), L), "geo", CYAN, 0.22, cloud_rot,
                 dict(uGain=3.0, uBase=0.3, uSpeed=0.4, uSeedPhase=1.0, uWidth=0.1)))
        add(Item(Mesh(GC.orb_quads(orbs, rng.uniform(0.09, 0.15, len(orbs)), rng.random(len(orbs))),
                      orb_layout, T), "orb", CYAN, 0.9, cloud_rot, dict(uRing=1.0)))

        # nucleus with tick ring
        add(Item(Mesh(GC.orb_quads([(0, 0, 0)], [0.30], [0.37]), orb_layout, T), "orb", ICE, 1.0,
                 lambda c: chain(), dict(uRing=1.0, uPulse=0.06)))

        # electron orbits
        circle = geo_mesh(GC.unit_circle_lines(200), L)
        one_orb = Mesh(GC.orb_quads([(0, 0, 0)], [0.07], [0.5]), orb_layout, T)
        orbits = [(0.62, 0.24, 0.0, 0.9, 1.0), (0.62, 0.24, 2.09, -0.7, 1.3), (0.62, 0.24, 4.19, 0.8, 0.8)]
        for a, b, rot, tilt_speed, e_speed in orbits:
            def orbit_m(c, rot=rot, ts=tilt_speed):
                return chain(ry(c.t * 0.12 * ts), rz(rot), rx(0.35 * math.sin(c.t * 0.2 + rot)))
            add(Item(circle, "geo", ICE, 1.3, lambda c, om=orbit_m, a=a, b=b: chain(om(c), scale(a, b, 1)),
                     dict(uGain=3.0, uBase=0.45, uRepeat=1, uSpeed=0.5 * e_speed, uWidth=0.06)))
            add(Item(one_orb, "orb", FROST, 1.0,
                     lambda c, om=orbit_m, a=a, b=b, sp=e_speed, rot=rot: chain(
                         om(c), translate(a * math.cos(c.t * sp * 1.6 + rot), b * math.sin(c.t * sp * 1.6 + rot), 0)),
                     dict(uRing=0.0, uPulse=0.1)))

        # faint equatorial ring + dust
        add(Item(geo_mesh(GC.dashed_ring(1.32, 120, 0.35, v=0.7), L), "geo", NAVY, 0.35,
                 lambda c: chain(rx(-math.pi / 2 + 0.1), rz(c.t * 0.03))))
        add(Item(Mesh(GC.particle_shell(int(900 * pm), 0.25, 1.7, rng, (0.5, 1.6), (-0.15, 0.15)), [3, 1, 1, 1], P),
                 "pts", ICE, 0.8, lambda c: chain(), dict(uBreath=1.0, uSwirl=0.01)))

        self._reactor_base = lambda c: chain()
        quad = np.array([[-1, -1], [1, -1], [1, 1], [-1, -1], [1, 1], [-1, 1]], np.float32)
        self._quad = Mesh(quad, [2], T)
        self._glows = [
            dict(size=(0.9, 0.9), core=0.0, sharp=1, halo=0.22, fall=4.0, rings=0.0, color=NAVY),
            dict(size=(0.22, 0.22), core=1.2, sharp=30, halo=0.5, fall=5.0, rings=0.0, color=CYAN),
            dict(size=(0.05, 0.05), core=3.0, sharp=6, halo=0.6, fall=3.0, rings=0.0, color=FROST),
        ]

    def _build_rufus_scene(self):
        """R.U.F.U.S: a dot-matrix data globe with live data links, satellites and a radar table."""
        GC = self._gc
        rng = np.random.default_rng(23)
        L, T, P = GL.GL_LINES, GL.GL_TRIANGLES, GL.GL_POINTS
        geo = [3, 1, 1, 1]
        orb_layout = [3, 2, 1, 1]
        add = self.items.append
        pm = self.particle_mul
        lift = 0.12

        def geo_mesh(arr, prim):
            return Mesh(arr, geo, prim)

        globe = lambda c: chain(translate(0, lift, 0), rz(0.41), ry(c.t * 0.07))
        add(Item(geo_mesh(GC.latlong_sphere(0.82, 12, 24, 128, 0.8), L), "geo", FOREST, 0.20, globe,
                 dict(uGain=1.4, uBase=0.6, uSpeed=0.06, uSeedPhase=3.0, uWidth=0.12)))
        dots, land = GC.globe_dots(0.825, int(5200 * min(1.2, max(0.6, pm))), rng)
        add(Item(Mesh(dots, [3, 1, 1, 1], P), "pts", LIME, 1.15, globe, dict(uBreath=0.0, uSwirl=0.0)))
        hubs = land[rng.choice(len(land), min(34, len(land)), replace=False)]
        add(Item(Mesh(GC.orb_quads(hubs * 1.01, rng.uniform(0.04, 0.07, len(hubs)), rng.random(len(hubs))),
                      orb_layout, T), "orb", MINT, 0.9, globe, dict(uRing=0.0, uPulse=0.05)))
        add(Item(geo_mesh(GC.data_arcs(hubs, int(34 * min(1.3, max(0.6, pm))), rng), L), "geo", GREEN, 0.75, globe,
                 dict(uGain=4.5, uBase=0.25, uRepeat=1, uSpeed=0.55, uSeedPhase=1.0, uWidth=0.06)))

        # latitude scanner sweeping the globe ("searching")
        sc_lines, sc_tris = GC.scanner_disc()
        scan_l, scan_t = geo_mesh(sc_lines, L), geo_mesh(sc_tris, T)

        def globe_scan(c):
            y = 0.78 * math.sin(c.t * 0.6)
            r = math.sqrt(max(0.84 ** 2 - y * y, 0.0))
            return chain(translate(0, lift + y, 0), scale(r, 1, r))
        add(Item(scan_l, "geo", MINT, 1.0, globe_scan, dict(uGain=2.5, uBase=0.5, uRepeat=3, uSpeed=0.6)))
        add(Item(scan_t, "geo", GREEN, 0.25, globe_scan, dict(_vmode=1)))

        # satellite orbits
        circle = geo_mesh(GC.unit_circle_lines(240), L)
        sat = Mesh(GC.orb_quads([(0, 0, 0)], [0.06], [0.3]), orb_layout, T)
        for R, tilt, spd, ph in ((1.08, 0.5, 0.35, 0.0), (1.2, -0.35, -0.25, 2.0), (1.0, 1.25, 0.45, 4.0)):
            def om(c, tilt=tilt, ph=ph):
                return chain(translate(0, lift, 0), rx(math.pi / 2 + tilt), rz(ph + c.t * 0.03))
            add(Item(circle, "geo", GREEN, 0.45, lambda c, om=om, R=R: chain(om(c), scale(R, R, 1)),
                     dict(uGain=2.5, uBase=0.35, uRepeat=2, uSpeed=0.2, uWidth=0.05)))
            add(Item(sat, "orb", MINT, 1.0, lambda c, om=om, R=R, s=spd, ph=ph: chain(
                om(c), translate(R * math.cos(c.t * s + ph), R * math.sin(c.t * s + ph), 0)),
                dict(uRing=0.6, uPulse=0.1)))

        # radar table under the globe
        table = lambda c: chain(translate(0, -0.95, 0))
        add(Item(geo_mesh(GC.radar_table(1.25, rng), L), "geo", GREEN, 0.55, table,
                 dict(uGain=2.0, uBase=0.55, uRepeat=1, uSpeed=0.25, uWidth=0.06)))
        add(Item(geo_mesh(GC.sweep_fan(1.25), T), "geo", GREEN, 0.30, table,
                 dict(uGain=2.2, uBase=0.0, uRepeat=1, uSpeed=0.22, uWidth=0.035)))
        # light columns from table to globe
        cols = GC.LineBuilder()
        for k in range(24):
            a = TAU * k / 24
            cols.polyline([(1.25 * math.cos(a), -0.95, 1.25 * math.sin(a)), (0.6 * math.cos(a), lift - 0.55, 0.6 * math.sin(a))],
                          [0.0, 1.0], 0.5, seed=k / 24)
        add(Item(geo_mesh(cols.build(), L), "geo", FOREST, 0.18, lambda c: chain(ry(c.t * 0.05)),
                 dict(uGain=3.0, uBase=0.2, uRepeat=1, uSpeed=0.5, uSeedPhase=1.0, uWidth=0.1)))

        add(Item(Mesh(GC.particle_shell(int(800 * pm), 0.3, 1.6, rng, (0.5, 1.5), (-0.12, 0.12)), [3, 1, 1, 1], P),
                 "pts", LIME, 0.7, lambda c: chain(), dict(uBreath=1.0, uSwirl=0.015)))

        self._reactor_base = lambda c: chain(translate(0, lift, 0))
        quad = np.array([[-1, -1], [1, -1], [1, 1], [-1, -1], [1, 1], [-1, 1]], np.float32)
        self._quad = Mesh(quad, [2], T)
        self._glows = [
            dict(size=(1.1, 1.1), core=0.0, sharp=1, halo=0.16, fall=3.2, rings=0.0, color=FOREST),
            dict(size=(0.22, 0.22), core=0.0, sharp=30, halo=0.0, fall=5.0, rings=0.0, color=GREEN),
        ]

    def _build_jarvis_scene(self):
        GC = self._gc
        rng = np.random.default_rng(7)
        L, T, P = GL.GL_LINES, GL.GL_TRIANGLES, GL.GL_POINTS
        geo = [3, 1, 1, 1]
        pts_layout = [3, 1, 1, 1]
        add = self.items.append
        pm = self.particle_mul

        def geo_mesh(arr, prim):
            return Mesh(arr, geo, prim)

        # ---------------- outer sphere ---------------------------------
        sphere_rot = lambda c: chain(ry(c.t * 0.045), rx(0.18))
        add(Item(geo_mesh(GC.latlong_sphere(1.0, 14, 28), L), "geo", AMBER, 0.11, sphere_rot,
                 dict(uGain=1.6, uBase=0.55, uRepeat=1.0, uSpeed=0.05, uSeedPhase=3.0, uWidth=0.12)))
        add(Item(geo_mesh(GC.latlong_sphere(0.94, 9, 18, v=0.8), L), "geo", DEEP, 0.07,
                 lambda c: chain(ry(-c.t * 0.07), rz(0.35))))

        ico_lines, ico_nodes = GC.icosphere_edges(1.06, 2, rng)
        ico_rot = lambda c: chain(rx(0.4), ry(-c.t * 0.032), rz(c.t * 0.011))
        add(Item(geo_mesh(ico_lines, L), "geo", GOLD, 0.13, ico_rot,
                 dict(uGain=4.0, uBase=0.35, uRepeat=1.0, uSpeed=0.35, uSeedPhase=1.0, uWidth=0.05)))
        add(Item(Mesh(GC.node_points(ico_nodes, rng, (1.2, 2.4)), pts_layout, P), "pts", HOT, 0.55, ico_rot))

        plex_lines, plex_nodes = GC.plexus(1.13, int(320 * min(pm, 1.3)), 0.24, rng)
        plex_rot = lambda c: chain(ry(c.t * 0.025), rx(-0.25))
        add(Item(geo_mesh(plex_lines, L), "geo", AMBER, 0.20, plex_rot,
                 dict(uGain=3.0, uBase=0.3, uSpeed=0.6, uSeedPhase=1.0, uWidth=0.1, uPulse=0.6)))
        add(Item(Mesh(GC.node_points(plex_nodes, rng, (1.5, 3.5)), pts_layout, P), "pts", GOLD, 0.8, plex_rot))

        add(Item(geo_mesh(GC.filaments(1.0, int(110 * pm), rng), L), "geo", AMBER, 0.17,
                 lambda c: chain(ry(c.t * 0.06), rx(0.1)),
                 dict(uGain=3.5, uBase=0.25, uSpeed=0.45, uSeedPhase=1.0, uWidth=0.08, uPulse=0.7)))
        add(Item(geo_mesh(GC.filaments(0.86, int(50 * pm), rng), L), "geo", DEEP, 0.12,
                 lambda c: chain(ry(-c.t * 0.09), rz(0.5)),
                 dict(uGain=3.0, uBase=0.25, uSpeed=0.6, uSeedPhase=1.0, uWidth=0.08, uPulse=0.7)))
        add(Item(geo_mesh(GC.circuit_traces(0.98, int(90 * pm), rng), L), "geo", GOLD, 0.24,
                 lambda c: chain(ry(c.t * 0.03 + 1.0), rx(-0.15)),
                 dict(uGain=4.0, uBase=0.3, uSpeed=0.5, uSeedPhase=1.0, uWidth=0.06)))
        add(Item(geo_mesh(GC.sparks(1.03, int(70 * pm), rng), L), "geo", HOT, 1.4,
                 lambda c: chain(ry(c.t * 0.05)),
                 dict(uFlicker=7.0, uJitter=0.035)))
        add(Item(geo_mesh(GC.sparks(0.6, int(30 * pm), rng, 7), L), "geo", HOT, 1.2,
                 lambda c: chain(ry(-c.t * 0.1)),
                 dict(uFlicker=9.0, uJitter=0.025)))

        arcs = GC.outer_arcs(1.14, 1.24, 26, rng)
        add(Item(geo_mesh(arcs, L), "geo", GOLD, 0.45, lambda c: chain(ry(-c.t * 0.08), rx(c.t * 0.013)),
                 dict(uGain=2.5, uBase=0.5, uSpeed=0.25, uSeedPhase=1.0, uWidth=0.1)))

        # outer sphere scanner (latitude sweeping slowly up and down)
        sc_lines, sc_tris = GC.scanner_disc()
        self._scan_l = geo_mesh(sc_lines, L)
        self._scan_t = geo_mesh(sc_tris, T)

        def outer_scan(c):
            y = 0.92 * math.sin(c.t * 0.35)
            r = math.sqrt(max(1.0 - y * y, 0.0))
            return chain(rx(0.18), translate(0, y, 0), scale(r, 1, r))
        add(Item(self._scan_l, "geo", HOT, 0.5, outer_scan, dict(uGain=3.0, uBase=0.4, uRepeat=3, uSpeed=0.4)))
        add(Item(self._scan_t, "geo", AMBER, 0.12, outer_scan, dict(_vmode=1)))

        # orbital HUD ring system around the equator
        hud = GC.hud_ring(1.33, rng)
        add(Item(geo_mesh(hud, L), "geo", AMBER, 0.45,
                 lambda c: chain(rx(-math.pi / 2 + 0.06), rz(c.t * 0.035)),
                 dict(uGain=2.0, uBase=0.6, uRepeat=2, uSpeed=0.08, uWidth=0.05)))
        add(Item(geo_mesh(GC.dashed_ring(1.46, 90, 0.4, v=0.7), L), "geo", DEEP, 0.35,
                 lambda c: chain(rx(-math.pi / 2 + 0.06), rz(-c.t * 0.05))))

        # particles
        add(Item(Mesh(GC.particle_shell(int(2400 * pm), 0.95, 1.28, rng), pts_layout, P), "pts", PALE, 1.3,
                 lambda c: chain(rx(0.1)), dict(uBreath=1.0, uSwirl=0.0)))
        add(Item(Mesh(GC.particle_shell(int(1100 * pm), 0.35, 0.92, rng, (0.5, 1.6), (-0.25, 0.25)),
                      pts_layout, P), "pts", GOLD, 1.0, lambda c: chain(), dict(uBreath=1.0, uSwirl=0.02)))
        add(Item(Mesh(GC.particle_disc(int(900 * pm), 0.14, 0.5, 0.025, rng), pts_layout, P), "pts", HOT, 0.8,
                 lambda c: chain(rx(0.55 + 0.05 * math.sin(c.t * 0.3)), rz(0.2)), dict(uSwirl=0.06)))

        # ---------------- gyroscope rings ------------------------------
        ring_specs = [
            # radius width thick plates teeth notches  tilt   prec  spin   wob   axis
            (0.45, 0.034, 0.020, 20, 54, 4, 0.95, 0.21, 0.55, 0.30, "y"),
            (0.555, 0.050, 0.026, 32, 0, 6, 1.75, -0.16, -0.38, 0.22, "x"),
            (0.66, 0.030, 0.018, 16, 0, 5, 0.55, 0.12, 0.30, 0.40, "y"),
            (0.765, 0.044, 0.024, 40, 72, 8, 2.25, -0.09, -0.22, 0.18, "z"),
            (0.865, 0.022, 0.014, 12, 0, 3, 1.30, 0.07, 0.16, 0.25, "x"),
        ]
        for idx, (R, w, th, plates, teeth, notches, tilt, prec, spin, wob, ax) in enumerate(ring_specs):
            lines, tris = GC.gyro_ring(R, w, th, rng, plates=plates, teeth=teeth, notches=notches)
            ph = rng.random() * TAU
            axis_fn = {"x": rx, "y": ry, "z": rz}[ax]

            def model(c, tilt=tilt, prec=prec, spin=spin, wob=wob, ph=ph, axis_fn=axis_fn):
                return chain(axis_fn(c.t * prec + ph), rx(tilt + wob * math.sin(c.t * 0.27 + ph)),
                             ry(0.3 * math.sin(c.t * 0.19 + ph * 2)), rz(c.t * spin))
            col = GOLD if idx % 2 == 0 else AMBER
            add(Item(geo_mesh(tris, T), "geo", col, 0.17, model, dict(uPlates=float(plates), _vmode=1)))
            add(Item(geo_mesh(lines, L), "geo", col, 1.15, model,
                     dict(uGain=2.6, uBase=0.55, uRepeat=2 + idx % 3, uSpeed=0.18 + 0.07 * idx, uWidth=0.05)))

        # ---------------- central reactor ------------------------------
        def reactor_base(c):
            return chain(rx(0.12 * math.sin(c.t * 0.31)), ry(0.16 * math.sin(c.t * 0.23)))
        self._reactor_base = reactor_base

        reactor_rings = [
            # r      dashes duty  speed  colour alpha
            (0.085, 36, 0.6, 1.4, HOT, 1.2),
            (0.115, 12, 0.75, -0.9, GOLD, 1.0),
            (0.175, 64, 0.45, 0.55, GOLD, 0.9),
            (0.205, 6, 0.8, -0.35, AMBER, 1.0),
            (0.235, 120, 0.3, 0.25, AMBER, 0.8),
            (0.265, 3, 0.9, 0.6, GOLD, 0.7),
        ]
        for r, d, duty, sp, col, a in reactor_rings:
            mesh = geo_mesh(GC.dashed_ring(r, d, duty, jitter=0.25, rng=rng), L)
            add(Item(mesh, "geo", col, a, lambda c, sp=sp: chain(reactor_base(c), rz(c.t * sp)),
                     dict(uGain=2.0, uBase=0.6, uRepeat=1, uSpeed=0.3 * sp, uWidth=0.08)))

        mech_l, mech_t = GC.reactor_mechanism(rng)
        mech_model = lambda c: chain(reactor_base(c), rz(-c.t * 0.8))
        add(Item(geo_mesh(mech_l, L), "geo", HOT, 1.1, mech_model))
        add(Item(geo_mesh(mech_t, T), "geo", GOLD, 0.25, mech_model, dict(uPulse=0.8)))

        add(Item(geo_mesh(GC.radial_spokes(72, 0.07, 0.30, rng), L), "geo", GOLD, 0.6,
                 lambda c: chain(reactor_base(c), rz(c.t * 0.12)),
                 dict(uGain=3.5, uBase=0.25, uRepeat=1, uSpeed=0.9, uSeedPhase=1.0, uWidth=0.12)))

        # reactor shell + horizontal energy scanner
        add(Item(geo_mesh(GC.latlong_sphere(0.32, 8, 16, 96, 0.8), L), "geo", AMBER, 0.12,
                 lambda c: chain(ry(c.t * 0.25), rx(0.3))))

        def core_scan(c):
            y = 0.3 * math.sin(c.t * 1.15)
            r = math.sqrt(max(0.32 ** 2 - y * y, 0.0))
            return chain(translate(0, y, 0), scale(r, 1, r))
        add(Item(self._scan_l, "geo", HOT, 1.4, core_scan, dict(uGain=2.0, uBase=0.6, uRepeat=2, uSpeed=1.2)))
        add(Item(self._scan_t, "geo", GOLD, 0.6, core_scan, dict(_vmode=1)))

        # glow billboards
        quad = np.array([[-1, -1], [1, -1], [1, 1], [-1, -1], [1, 1], [-1, 1]], np.float32)
        self._quad = Mesh(quad, [2], T)
        self._glows = [
            # sizeX sizeY  core  sharp halo  fall rings colour
            dict(size=(0.75, 0.75), core=0.0, sharp=1, halo=0.30, fall=4.5, rings=0.0, color=AMBER),
            dict(size=(0.22, 0.22), core=1.5, sharp=26, halo=0.6, fall=5.0, rings=0.25, color=GOLD),
            dict(size=(0.06, 0.06), core=4.0, sharp=6, halo=0.8, fall=3.0, rings=0.0, color=HOT),
            dict(size=(1.25, 0.014), core=0.9, sharp=3, halo=0.3, fall=3.0, rings=0.0, color=GOLD),
            dict(size=(0.012, 0.32), core=0.4, sharp=3, halo=0.15, fall=3.0, rings=0.0, color=GOLD),
        ]

    # ---------------------------------------------------------- animation
    def _advance(self):
        now = self._clock.elapsed() / 1000.0
        dt = min(0.1, max(0.0, now - self._last))
        self._last = now
        energy_t, speed_t, tint_t, core_t = STATE_PROFILES.get(self.state, STATE_PROFILES["idle"])
        k = 1.0 - math.exp(-dt * 2.5)
        self.energy += (energy_t - self.energy) * k
        self.speed += (speed_t - self.speed) * k
        self.tint += (np.array(tint_t) - self.tint) * k
        self.core_gain += (core_t - self.core_gain) * k

        target = self._target_level
        if self._synthetic_speech:
            # engines without amplitude data: synthesise a speech-like envelope
            target = 0.35 + 0.3 * abs(math.sin(now * 9.1)) * (0.6 + 0.4 * math.sin(now * 2.3)) \
                + 0.2 * abs(math.sin(now * 23.7))
        ka = 1.0 - math.exp(-dt * (18.0 if target > self.audio_level else 6.0))
        self.audio_level += (target - self.audio_level) * ka
        if not self._synthetic_speech:
            self._target_level *= math.exp(-dt * 4.0)

        self.anim_t += dt * self.speed

        # camera inertia / idle sway
        if not self.dragging:
            self.yaw += self._vel[0] * dt
            self.pitch += self._vel[1] * dt
            self._vel *= math.exp(-dt * 3.0)
            if not self._user_cam:
                self.yaw = 0.12 * math.sin(now * 0.11)
                self.pitch = 0.22 + 0.05 * math.sin(now * 0.07)
        self.pitch = max(-1.2, min(1.2, self.pitch))
        return now

    # --------------------------------------------------------------- draw
    def render(self, dst_fbo: int, W: int, H: int) -> bool:
        """Draw one frame into framebuffer `dst_fbo` of W x H pixels. Returns False if GL is unusable."""
        if not self.ok:
            return False
        rt = self._advance()
        W, H = max(8, int(W)), max(8, int(H))
        tg = self._targets
        if tg is None or (tg["scene"].w, tg["scene"].h) != (W, H):
            self.resize(W, H)
            tg = self._targets
        sc = tg["scene"]

        aspect = sc.w / max(1, sc.h)
        fov = 38.0 if aspect >= 1 else 38.0 / max(aspect, 0.5)
        proj = perspective(fov, aspect, 0.1, 50.0)
        eye = (self.dist * math.sin(self.yaw) * math.cos(self.pitch),
               self.dist * math.sin(self.pitch),
               self.dist * math.cos(self.yaw) * math.cos(self.pitch))
        view = look_at(eye, (0, 0, 0))
        fade_near, fade_far = self.dist - 1.25, self.dist + 1.25
        pixel_scale = sc.h / (2.0 * math.tan(math.radians(fov) / 2.0)) * 0.0058

        pulse = 0.5 + 0.5 * math.sin(rt * 2.1)
        core = self.core_gain * (0.85 + 0.15 * pulse + 0.55 * self.audio_level)
        ctx = Ctx(self.anim_t, rt, self.energy, self.audio_level, core)
        e_gain = 0.65 + 0.6 * self.energy

        # ---- scene pass
        sc.bind_draw()
        GL.glClearColor(0, 0, 0, 1)
        GL.glClear(GL.GL_COLOR_BUFFER_BIT)
        GL.glDisable(GL.GL_DEPTH_TEST)
        GL.glDisable(GL.GL_CULL_FACE)
        GL.glEnable(GL.GL_BLEND)
        GL.glBlendFunc(GL.GL_ONE, GL.GL_ONE)
        GL.glEnable(GL.GL_PROGRAM_POINT_SIZE)

        current = None
        for it in self.items:
            prog = self.programs[it.prog]
            if current is not prog:
                prog.use()
                prog.m4("uView", view)
                prog.m4("uProj", proj)
                prog.f("uTime", self.anim_t if it.prog == "pts" else rt)
                prog.f("uFadeNear", fade_near)
                prog.f("uFadeFar", fade_far)
                prog.f("uWhite", *self.theme["white"])
                if it.prog == "pts":
                    prog.f("uPixelScale", pixel_scale * (1.0 + 0.25 * self.audio_level))
                current = prog
            prog.m4("uModel", it.model(ctx))
            prog.f("uColor", *it.color)
            a = it.alpha * e_gain * (it.alpha_fn(ctx) if it.alpha_fn else 1.0)
            prog.f("uAlpha", a)
            if it.prog == "geo":
                for key, dv in GEO_DEFAULTS.items():
                    val = it.u.get(key, dv)
                    if key == "uSpeed":
                        val = val * (0.6 + 0.6 * self.speed)
                    prog.f(key, val)
                prog.f("uTime", rt * (0.7 + 0.3 * self.speed))
                prog.i("uVMode", int(it.u.get("_vmode", 0)))
            elif it.prog == "orb":
                prog.f("uRing", it.u.get("uRing", 1.0))
                prog.f("uPulse", it.u.get("uPulse", 0.0) * (0.5 + 2.0 * self.audio_level))
            elif it.prog == "pts":
                prog.f("uBreath", it.u.get("uBreath", 0.0) * (1 + 2 * self.audio_level))
                prog.f("uSwirl", it.u.get("uSwirl", 0.0))
            it.mesh.draw()

        # ---- reactor glow billboards
        g = self.programs["glow"]
        g.use()
        g.m4("uView", view)
        g.m4("uProj", proj)
        g.m4("uModel", self._reactor_base(ctx))
        g.f("uTime", rt)
        g.f("uWhite", *self.theme["white"])
        for i, gl_ in enumerate(self._glows):
            boost = core if i in (1, 2) else (0.7 + 0.5 * core if i >= 3 else 0.8 + 0.4 * core)
            sx, sy = gl_["size"]
            if i == 1:
                sx = sy = 0.22 * (0.92 + 0.12 * pulse + 0.2 * self.audio_level)
            g.f("uSize", sx, sy)
            g.f("uColor", *(np.array(gl_["color"]) * boost))
            g.f("uCore", gl_["core"])
            g.f("uSharp", gl_["sharp"])
            g.f("uHalo", gl_["halo"])
            g.f("uHaloFall", gl_["fall"])
            g.f("uRings", gl_["rings"])
            self._quad.draw()

        GL.glDisable(GL.GL_BLEND)
        sc.resolve()

        # ---- bloom chain
        GL.glBindVertexArray(self._quad_vao)
        self._pass("bright", tg["b0a"], sc.tex, uTexel=(1.0 / sc.w, 1.0 / sc.h), uThreshold=0.55)
        self._blur(tg["b0a"], tg["b0b"])
        self._pass("down", tg["b1a"], tg["b0a"].tex, uTexel=(1.0 / tg["b0a"].w, 1.0 / tg["b0a"].h))
        self._blur(tg["b1a"], tg["b1b"])
        self._pass("down", tg["b2a"], tg["b1a"].tex, uTexel=(1.0 / tg["b1a"].w, 1.0 / tg["b1a"].h))
        self._blur(tg["b2a"], tg["b2b"])
        self._blur(tg["b2a"], tg["b2b"])

        # ---- composite to the destination
        GL.glBindFramebuffer(GL.GL_FRAMEBUFFER, dst_fbo)
        GL.glViewport(0, 0, W, H)
        c = self.programs["comp"]
        c.use()
        for unit, (name, tex) in enumerate((("uScene", sc.tex), ("uBloom0", tg["b0a"].tex),
                                            ("uBloom1", tg["b1a"].tex), ("uBloom2", tg["b2a"].tex))):
            GL.glActiveTexture(GL.GL_TEXTURE0 + unit)
            GL.glBindTexture(GL.GL_TEXTURE_2D, tex)
            c.i(name, unit)
        c.f("uBloomStrength", (1.1 + 0.5 * self.energy) * self.bloom_mul)
        c.f("uExposure", self.exposure)
        c.f("uTime", rt)
        c.f("uResolution", sc.w, sc.h)
        c.f("uTint", *self.tint)
        c.i("uTransparent", 1 if self.transparent else 0)
        th = self.theme
        c.f("uBgBase", *th["bg_base"])
        c.f("uBgHaze", *th["bg_haze"])
        c.f("uBgGlow", *th["bg_glow"])
        c.f("uGridCol", *th["grid"])
        c.f("uGrade", *th["grade"])
        GL.glDrawArrays(GL.GL_TRIANGLES, 0, 3)
        GL.glActiveTexture(GL.GL_TEXTURE0)
        GL.glBindVertexArray(0)
        GL.glUseProgram(0)
        return True

    def _pass(self, prog_name, target: RenderTarget, src_tex, **uniforms):
        p = self.programs[prog_name]
        p.use()
        GL.glBindFramebuffer(GL.GL_FRAMEBUFFER, target.fbo)
        GL.glViewport(0, 0, target.w, target.h)
        GL.glActiveTexture(GL.GL_TEXTURE0)
        GL.glBindTexture(GL.GL_TEXTURE_2D, src_tex)
        p.i("uTex", 0)
        for k, v in uniforms.items():
            p.f(k, *(v if isinstance(v, tuple) else (v,)))
        GL.glDrawArrays(GL.GL_TRIANGLES, 0, 3)

    def _blur(self, a: RenderTarget, b: RenderTarget):
        self._pass("blur", b, a.tex, uDir=(1.0 / a.w, 0.0))
        self._pass("blur", a, b.tex, uDir=(0.0, 1.0 / a.h))

    # --------------------------------------------------------- camera input
    def drag_by(self, dx: float, dy: float):
        self._user_cam = True
        self.yaw -= dx * 0.006
        self.pitch += dy * 0.006
        self._vel = np.array([-dx * 0.35, dy * 0.35])

    def reset_camera(self, dist: float | None = None):
        self._user_cam = False
        self._vel[:] = 0
        self.dist = dist or self.base_dist

    def zoom(self, steps: float):
        self.dist = max(2.2, min(7.0, self.dist * (0.9 ** steps)))


class HologramWidget(QOpenGLWidget):
    """The holographic core embedded in the dashboard."""

    fps_changed = Signal(float)

    def __init__(self, parent=None, quality: str = "high", fps: int = 60, scene: str = "jarvis"):
        super().__init__(parent)
        self.engine = HologramEngine(quality, scene=scene)
        self.setMinimumSize(320, 320)
        self.setFocusPolicy(Qt.NoFocus)
        self._drag: QPointF | None = None
        self._frames = 0
        self._fps_t = time.perf_counter()
        self._timer = QTimer(self)
        self._timer.setTimerType(Qt.PreciseTimer)
        self._timer.timeout.connect(self.update)
        self._timer.start(max(1, int(1000 / fps)))

    # API (delegates to the engine)
    def set_state(self, state: str):
        self.engine.set_state(state)

    def set_audio_level(self, level: float):
        self.engine.set_audio_level(level)

    def set_synthetic_speech(self, on: bool):
        self.engine.set_synthetic_speech(on)

    @property
    def ok(self) -> bool:
        return self.engine.ok

    @property
    def error(self):
        return self.engine.error

    def set_paused(self, paused: bool):
        if paused:
            self._timer.stop()
        elif not self._timer.isActive():
            self._timer.start()

    def set_fps(self, fps: int):
        self._timer.setInterval(max(1, int(1000 / max(5, fps))))

    # GL
    def initializeGL(self):
        self.engine.init_gl()

    def resizeGL(self, w, h):
        dpr = self.devicePixelRatioF()
        self.engine.resize(int(w * dpr), int(h * dpr))

    def paintGL(self):
        dpr = self.devicePixelRatioF()
        if not self.engine.render(self.defaultFramebufferObject(), int(self.width() * dpr),
                                  int(self.height() * dpr)):
            self._paint_error()
            return
        self._frames += 1
        if self._frames == 1:
            perf.record_once("startup_hologram", perf.since_start())
        now = time.perf_counter()
        if now - self._fps_t >= 1.0:
            self.fps_changed.emit(self._frames / (now - self._fps_t))
            self._frames = 0
            self._fps_t = now

    def _paint_error(self):
        from PySide6.QtGui import QColor, QPainter
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(8, 5, 3))
        p.setPen(QColor(255, 150, 60))
        p.drawText(self.rect(), Qt.AlignCenter,
                   "HOLOGRAPHIC CORE OFFLINE\nOpenGL 3.3 is required.\n" + (self.error or ""))
        p.end()

    # interaction
    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._drag = e.position()
            self.engine.dragging = True

    def mouseMoveEvent(self, e):
        if self._drag is not None:
            d = e.position() - self._drag
            self._drag = e.position()
            self.engine.drag_by(d.x(), d.y())

    def mouseReleaseEvent(self, e):
        self._drag = None
        self.engine.dragging = False

    def mouseDoubleClickEvent(self, e):
        self.engine.reset_camera()

    def wheelEvent(self, e):
        self.engine.zoom(1 if e.angleDelta().y() > 0 else -1)

    def cleanup(self):
        self._timer.stop()
