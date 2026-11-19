"""Procedural geometry for the holographic core.

Every builder returns float32 vertex arrays in the GEO layout
(x, y, z, u, v, seed) or the POINT layout (x, y, z, seed, size, speed).
"""
from __future__ import annotations

import math
import numpy as np

TAU = 2.0 * math.pi


class LineBuilder:
    """Accumulates GL_LINES segments."""

    def __init__(self) -> None:
        self._chunks: list[np.ndarray] = []

    def polyline(self, pts, u=None, v=1.0, seed=0.0, closed=False):
        pts = np.asarray(pts, dtype=np.float32)
        n = len(pts)
        if n < 2:
            return
        if u is None:
            u = np.linspace(0.0, 1.0, n, dtype=np.float32)
        u = np.asarray(u, dtype=np.float32)
        if np.isscalar(v):
            v = np.full(n, v, dtype=np.float32)
        v = np.asarray(v, dtype=np.float32)
        if closed:
            pts = np.vstack([pts, pts[:1]])
            u = np.append(u, 1.0).astype(np.float32)
            v = np.append(v, v[0]).astype(np.float32)
        a = np.column_stack([pts[:-1], u[:-1], v[:-1], np.full(len(pts) - 1, seed, np.float32)])
        b = np.column_stack([pts[1:], u[1:], v[1:], np.full(len(pts) - 1, seed, np.float32)])
        seg = np.empty((2 * len(a), 6), dtype=np.float32)
        seg[0::2] = a
        seg[1::2] = b
        self._chunks.append(seg)

    def segment(self, p0, p1, v=1.0, seed=0.0, u0=0.0, u1=1.0):
        self._chunks.append(
            np.array([[*p0, u0, v, seed], [*p1, u1, v, seed]], dtype=np.float32)
        )

    def extend(self, arr: np.ndarray):
        if arr is not None and len(arr):
            self._chunks.append(arr.astype(np.float32))

    def build(self) -> np.ndarray:
        if not self._chunks:
            return np.zeros((0, 6), dtype=np.float32)
        return np.ascontiguousarray(np.vstack(self._chunks), dtype=np.float32)


class TriBuilder:
    """Accumulates GL_TRIANGLES."""

    def __init__(self) -> None:
        self._chunks: list[np.ndarray] = []

    def strip(self, a_pts, b_pts, u, va=0.0, vb=1.0, seed=0.0):
        """Quad strip between two matching polylines (closed loop expected to repeat first point)."""
        a_pts = np.asarray(a_pts, np.float32)
        b_pts = np.asarray(b_pts, np.float32)
        u = np.asarray(u, np.float32)
        n = len(a_pts) - 1
        out = np.empty((n * 6, 6), dtype=np.float32)
        for k, (p, uu, vv) in enumerate(
            [(a_pts[:-1], u[:-1], va), (b_pts[:-1], u[:-1], vb), (b_pts[1:], u[1:], vb),
             (a_pts[:-1], u[:-1], va), (b_pts[1:], u[1:], vb), (a_pts[1:], u[1:], va)]
        ):
            out[k::6, :3] = p
            out[k::6, 3] = uu
            out[k::6, 4] = vv
            out[k::6, 5] = seed
        self._chunks.append(out)

    def build(self) -> np.ndarray:
        if not self._chunks:
            return np.zeros((0, 6), dtype=np.float32)
        return np.ascontiguousarray(np.vstack(self._chunks), dtype=np.float32)


# ---------------------------------------------------------------------------
# primitives
# ---------------------------------------------------------------------------
def circle(r: float, n: int, z: float = 0.0, a0: float = 0.0, a1: float = TAU):
    t = np.linspace(a0, a1, n, endpoint=(a1 - a0) < TAU - 1e-6, dtype=np.float64)
    pts = np.column_stack([r * np.cos(t), r * np.sin(t), np.full_like(t, z)]).astype(np.float32)
    return pts, ((t - 0.0) / TAU).astype(np.float32)


def _closed(pts, u):
    return np.vstack([pts, pts[:1]]), np.append(u, 1.0).astype(np.float32)


def random_unit(rng: np.random.Generator, n: int) -> np.ndarray:
    v = rng.normal(size=(n, 3))
    return (v / np.linalg.norm(v, axis=1, keepdims=True)).astype(np.float32)


def fibonacci_sphere(n: int) -> np.ndarray:
    i = np.arange(n) + 0.5
    phi = np.arccos(1 - 2 * i / n)
    theta = math.pi * (1 + 5 ** 0.5) * i
    return np.column_stack(
        [np.cos(theta) * np.sin(phi), np.cos(phi), np.sin(theta) * np.sin(phi)]
    ).astype(np.float32)


# ---------------------------------------------------------------------------
# Gyroscope ring: a thick mechanical band with plates, ticks, struts and teeth
# ---------------------------------------------------------------------------
def gyro_ring(radius: float, width: float, thick: float, rng: np.random.Generator,
              plates: int = 24, teeth: int = 0, ticks: int = 180, res: int = 256,
              notches: int = 6):
    lines = LineBuilder()
    tris = TriBuilder()
    r_in, r_out = radius - width / 2, radius + width / 2
    zf, zb = thick / 2, -thick / 2

    # band faces (front and back) + outer rim surface
    for z in (zf, zb):
        a, u = _closed(*circle(r_in, res, z))
        b, _ = _closed(*circle(r_out, res, z))
        tris.strip(a, b, u, 0.0, 1.0)
    a, u = _closed(*circle(r_out, res, zf))
    b, _ = _closed(*circle(r_out, res, zb))
    tris.strip(a, b, u, 0.0, 1.0)

    # edges
    for z in (zf, zb):
        for r, br in ((r_in, 0.9), (r_out, 1.0)):
            p, u = circle(r, res, z)
            lines.polyline(p, u, br, closed=True)
    # inner engraved track (dashed)
    mid = (r_in + r_out) / 2
    dashes = plates * 2
    for k in range(dashes):
        a0 = TAU * k / dashes
        a1 = a0 + TAU / dashes * 0.55
        p, u = circle(mid, 8, zf * 1.02, a0, a1)
        lines.polyline(p, u, 0.45)

    # plate separators (radial on face + struts through thickness)
    for k in range(plates):
        ang = TAU * k / plates
        c, s = math.cos(ang), math.sin(ang)
        uu = k / plates
        lines.segment((r_in * c, r_in * s, zf), (r_out * c, r_out * s, zf), 0.7, u0=uu, u1=uu)
        lines.segment((r_out * c, r_out * s, zf), (r_out * c, r_out * s, zb), 0.6, u0=uu, u1=uu)

    # fine ticks around the outer edge
    for k in range(ticks):
        ang = TAU * k / ticks
        major = k % 10 == 0
        ln = width * (0.9 if major else 0.35)
        c, s = math.cos(ang), math.sin(ang)
        lines.segment((r_out * c, r_out * s, zf), ((r_out + ln) * c, (r_out + ln) * s, zf),
                      0.9 if major else 0.4, u0=k / ticks, u1=k / ticks)

    # mechanical teeth (cog profile) on the outer edge
    if teeth:
        pts = []
        us = []
        r_t = r_out + width * 0.45
        for k in range(teeth):
            a0 = TAU * k / teeth
            step = TAU / teeth
            for frac, r in ((0.0, r_out), (0.12, r_t), (0.5, r_t), (0.62, r_out)):
                ang = a0 + step * frac
                pts.append((r * math.cos(ang), r * math.sin(ang), 0.0))
                us.append(ang / TAU)
        lines.polyline(pts, us, 0.85, closed=True)

    # notch blocks (little mechanical housings)
    for k in range(notches):
        ang = TAU * (k + rng.random() * 0.3) / notches
        span = 0.05 + rng.random() * 0.06
        h = width * 0.9
        corners = []
        for aa, rr in ((ang, r_in - h * 0.35), (ang + span, r_in - h * 0.35),
                       (ang + span, r_out + h * 0.35), (ang, r_out + h * 0.35)):
            corners.append((rr * math.cos(aa), rr * math.sin(aa), zf))
        lines.polyline(corners, [ang / TAU] * 4, 1.1, closed=True)
        # crossed brace
        lines.segment(corners[0], corners[2], 0.6, u0=ang / TAU, u1=ang / TAU)

    return lines.build(), tris.build()


# ---------------------------------------------------------------------------
# Reactor
# ---------------------------------------------------------------------------
def dashed_ring(r: float, dashes: int, duty: float, z: float = 0.0, res_per_dash: int = 8,
                v: float = 1.0, jitter: float = 0.0, rng=None):
    lines = LineBuilder()
    for k in range(dashes):
        frac = duty if not jitter else duty * (1 - jitter + 2 * jitter * rng.random())
        a0 = TAU * k / dashes
        a1 = a0 + TAU / dashes * frac
        p, u = circle(r, res_per_dash, z, a0, a1)
        lines.polyline(p, u, v, seed=k / dashes)
    return lines.build()


def reactor_mechanism(rng: np.random.Generator):
    """Inner rotating turbine: blades + hub."""
    lines = LineBuilder()
    tris = TriBuilder()
    blades = 10
    r0, r1 = 0.07, 0.15
    for k in range(blades):
        a = TAU * k / blades
        w0, w1 = 0.16, 0.08
        sweep = 0.35
        quad = [
            (r0 * math.cos(a - w0), r0 * math.sin(a - w0), 0.0),
            (r1 * math.cos(a - w1 + sweep), r1 * math.sin(a - w1 + sweep), 0.0),
            (r1 * math.cos(a + w1 + sweep), r1 * math.sin(a + w1 + sweep), 0.0),
            (r0 * math.cos(a + w0), r0 * math.sin(a + w0), 0.0),
        ]
        lines.polyline(quad, [k / blades] * 4, 1.0, seed=k / blades, closed=True)
        q = np.array(quad, np.float32)
        tri = np.array([q[0], q[1], q[2], q[0], q[2], q[3]], np.float32)
        tris._chunks.append(np.column_stack([tri, np.full(6, k / blades), np.full(6, 0.5),
                                             np.full(6, k / blades)]).astype(np.float32))
    for r in (0.055, 0.062, 0.155):
        p, u = circle(r, 96)
        lines.polyline(p, u, 1.0, closed=True)
    return lines.build(), tris.build()


def radial_spokes(n: int, r0: float, r1: float, rng: np.random.Generator, z: float = 0.0):
    lines = LineBuilder()
    for k in range(n):
        a = TAU * k / n + rng.random() * 0.02
        ra = r0 * (1 + rng.random() * 0.05)
        rb = r1 * (0.8 + rng.random() * 0.2)
        pts = [(ra * math.cos(a), ra * math.sin(a), z), (rb * math.cos(a), rb * math.sin(a), z)]
        lines.polyline(pts, [0.0, 1.0], 0.8, seed=rng.random())
    return lines.build()


def unit_circle_lines(res: int = 160, v: float = 1.0):
    lines = LineBuilder()
    p, u = circle(1.0, res)
    lines.polyline(p, u, v, closed=True)
    return lines.build()


def scanner_disc(res: int = 160):
    """A thin horizontal band (in XZ plane) for the energy scanner."""
    tris = TriBuilder()
    t = np.linspace(0, TAU, res + 1)
    a = np.column_stack([np.cos(t) * 0.92, np.zeros_like(t), np.sin(t) * 0.92]).astype(np.float32)
    b = np.column_stack([np.cos(t), np.zeros_like(t), np.sin(t)]).astype(np.float32)
    tris.strip(a, b, (t / TAU).astype(np.float32), 0.0, 1.0)
    lines = LineBuilder()
    lines.polyline(b[:-1], (t[:-1] / TAU).astype(np.float32), 1.0, closed=True)
    return lines.build(), tris.build()


# ---------------------------------------------------------------------------
# Outer sphere
# ---------------------------------------------------------------------------
def latlong_sphere(R: float, n_lat: int, n_lon: int, res: int = 128, v: float = 1.0):
    lines = LineBuilder()
    for i in range(1, n_lat):
        phi = math.pi * i / n_lat
        y = R * math.cos(phi)
        r = R * math.sin(phi)
        t = np.linspace(0, TAU, res, endpoint=False)
        pts = np.column_stack([r * np.cos(t), np.full_like(t, y), r * np.sin(t)])
        lines.polyline(pts, t / TAU, v, seed=i / n_lat, closed=True)
    for j in range(n_lon):
        th = TAU * j / n_lon
        p = np.linspace(0, math.pi, res // 2)
        pts = np.column_stack([R * np.sin(p) * math.cos(th), R * np.cos(p), R * np.sin(p) * math.sin(th)])
        lines.polyline(pts, p / math.pi, v, seed=j / n_lon)
    return lines.build()


def icosphere_edges(R: float, subdiv: int, rng: np.random.Generator, v: float = 1.0):
    t = (1 + 5 ** 0.5) / 2
    verts = [(-1, t, 0), (1, t, 0), (-1, -t, 0), (1, -t, 0), (0, -1, t), (0, 1, t), (0, -1, -t),
             (0, 1, -t), (t, 0, -1), (t, 0, 1), (-t, 0, -1), (-t, 0, 1)]
    verts = [np.array(p, np.float64) / np.linalg.norm(p) for p in verts]
    faces = [(0, 11, 5), (0, 5, 1), (0, 1, 7), (0, 7, 10), (0, 10, 11), (1, 5, 9), (5, 11, 4),
             (11, 10, 2), (10, 7, 6), (7, 1, 8), (3, 9, 4), (3, 4, 2), (3, 2, 6), (3, 6, 8),
             (3, 8, 9), (4, 9, 5), (2, 4, 11), (6, 2, 10), (8, 6, 7), (9, 8, 1)]
    for _ in range(subdiv):
        cache = {}

        def mid(a, b):
            key = (min(a, b), max(a, b))
            if key not in cache:
                m = verts[a] + verts[b]
                verts.append(m / np.linalg.norm(m))
                cache[key] = len(verts) - 1
            return cache[key]

        nf = []
        for a, b, c in faces:
            ab, bc, ca = mid(a, b), mid(b, c), mid(c, a)
            nf += [(a, ab, ca), (b, bc, ab), (c, ca, bc), (ab, bc, ca)]
        faces = nf
    edges = set()
    for a, b, c in faces:
        for e in ((a, b), (b, c), (c, a)):
            edges.add((min(e), max(e)))
    lines = LineBuilder()
    for a, b in edges:
        # draw as great-circle arc with a few points so it hugs the sphere
        pa, pb = verts[a], verts[b]
        ts = np.linspace(0, 1, 4)
        pts = np.array([(pa * (1 - s) + pb * s) for s in ts])
        pts = pts / np.linalg.norm(pts, axis=1, keepdims=True) * R
        lines.polyline(pts, ts, v, seed=rng.random())
    nodes = np.array(verts, np.float32) * R
    return lines.build(), nodes


def outer_arcs(R0: float, R1: float, count: int, rng: np.random.Generator):
    """Partial rotating latitude arcs at random tilts – outer structures."""
    lines = LineBuilder()
    for _ in range(count):
        R = R0 + (R1 - R0) * rng.random()
        axis = random_unit(rng, 1)[0]
        lat = (rng.random() - 0.5) * 0.6
        span = 0.3 + rng.random() * 1.9
        a0 = rng.random() * TAU
        t = np.linspace(a0, a0 + span, max(8, int(span * 40)))
        r = R * math.cos(lat)
        y = R * math.sin(lat)
        pts = np.column_stack([r * np.cos(t), np.full_like(t, y), r * np.sin(t)])
        # orient by axis
        up = np.array([0, 1, 0], np.float64)
        k = np.cross(up, axis)
        s = np.linalg.norm(k)
        if s > 1e-6:
            k /= s
            ang = math.acos(np.clip(np.dot(up, axis), -1, 1))
            c, sn = math.cos(ang), math.sin(ang)
            pts = pts * c + np.cross(k, pts) * sn + np.outer(pts @ k, k) * (1 - c)
        lines.polyline(pts, np.linspace(0, 1, len(t)), 0.6 + 0.6 * rng.random(), seed=rng.random())
        # end caps
        for idx in (0, -1):
            p = pts[idx]
            n = p / np.linalg.norm(p)
            lines.segment(p - n * 0.025, p + n * 0.025, 1.0, seed=rng.random())
    return lines.build()


def filaments(R: float, count: int, rng: np.random.Generator):
    """Irregular random-walk energy filaments hugging the sphere."""
    lines = LineBuilder()
    for _ in range(count):
        p = random_unit(rng, 1)[0].astype(np.float64)
        d = np.cross(p, random_unit(rng, 1)[0])
        d /= np.linalg.norm(d)
        steps = rng.integers(14, 48)
        step = 0.018 + rng.random() * 0.02
        pts = []
        for i in range(steps):
            rr = R * (1.0 + 0.035 * math.sin(i * 0.7 + rng.random()) + rng.normal() * 0.006)
            pts.append(p * rr)
            # turn randomly within tangent plane
            turn = rng.normal() * 0.45
            side = np.cross(p, d)
            d = d * math.cos(turn) + side * math.sin(turn)
            p = p + d * step
            p /= np.linalg.norm(p)
            d = d - p * np.dot(d, p)
            d /= np.linalg.norm(d)
        lines.polyline(np.array(pts), np.linspace(0, 1, len(pts)), 0.5 + rng.random() * 0.7,
                       seed=rng.random())
    return lines.build()


def plexus(R: float, n_nodes: int, max_dist: float, rng: np.random.Generator, max_links: int = 3):
    nodes = fibonacci_sphere(n_nodes)
    nodes += rng.normal(scale=0.03, size=nodes.shape).astype(np.float32)
    nodes = nodes / np.linalg.norm(nodes, axis=1, keepdims=True)
    nodes *= (R * (1 + rng.normal(scale=0.015, size=(n_nodes, 1)))).astype(np.float32)
    lines = LineBuilder()
    counts = np.zeros(n_nodes, int)
    d = np.linalg.norm(nodes[:, None, :] - nodes[None, :, :], axis=2)
    for i in range(n_nodes):
        order = np.argsort(d[i])
        for j in order[1:6]:
            if d[i, j] > max_dist or counts[i] >= max_links or counts[j] >= max_links or j < i:
                continue
            if rng.random() < 0.25:
                continue
            lines.polyline([nodes[i], nodes[j]], [0, 1], 0.7, seed=rng.random())
            counts[i] += 1
            counts[j] += 1
    return lines.build(), nodes


def circuit_traces(R: float, count: int, rng: np.random.Generator):
    """Right-angled 'PCB' traces along latitude / longitude on the sphere."""
    lines = LineBuilder()
    stepd = math.radians(4.0)
    for _ in range(count):
        th = rng.random() * TAU
        ph = 0.35 + rng.random() * (math.pi - 0.7)
        pts = []
        horiz = rng.random() < 0.5
        for _seg in range(rng.integers(3, 7)):
            n = rng.integers(2, 7)
            sgn = 1 if rng.random() < 0.5 else -1
            for _k in range(n):
                pts.append((th, ph))
                if horiz:
                    th += stepd * sgn
                else:
                    ph = float(np.clip(ph + stepd * sgn, 0.3, math.pi - 0.3))
            horiz = not horiz
        pts.append((th, ph))
        xyz = np.array([(R * math.sin(p) * math.cos(t), R * math.cos(p), R * math.sin(p) * math.sin(t))
                        for t, p in pts])
        seed = rng.random()
        lines.polyline(xyz, np.linspace(0, 1, len(xyz)), 0.8, seed=seed)
        # terminal pad
        e = xyz[-1]
        n = e / np.linalg.norm(e)
        a = np.cross(n, [0, 1, 0])
        if np.linalg.norm(a) < 1e-3:
            a = np.cross(n, [1, 0, 0])
        a /= np.linalg.norm(a)
        b = np.cross(n, a)
        s = 0.011
        sq = [e + (a + b) * s, e + (a - b) * s, e + (-a - b) * s, e + (-a + b) * s]
        lines.polyline(sq, [1, 1, 1, 1], 1.2, seed=seed, closed=True)
    return lines.build()


def sparks(R: float, count: int, rng: np.random.Generator, segs: int = 9):
    lines = LineBuilder()
    for _ in range(count):
        a = random_unit(rng, 1)[0]
        b = a + random_unit(rng, 1)[0] * (0.08 + rng.random() * 0.18)
        b = b / np.linalg.norm(b)
        ts = np.linspace(0, 1, segs)
        pts = np.array([(a * (1 - t) + b * t) for t in ts])
        pts = pts / np.linalg.norm(pts, axis=1, keepdims=True) * R
        lines.polyline(pts, ts, 1.0, seed=rng.random())
    return lines.build()


def hud_ring(R: float, rng: np.random.Generator):
    """Flat orbital ring system with ticks and labelled segments."""
    lines = LineBuilder()
    for r, v in ((R, 0.8), (R * 1.035, 0.5), (R * 1.11, 0.35)):
        p, u = circle(r, 360)
        lines.polyline(p, u, v, closed=True)
    for k in range(240):
        a = TAU * k / 240
        ln = 0.045 if k % 20 == 0 else (0.022 if k % 5 == 0 else 0.01)
        c, s = math.cos(a), math.sin(a)
        r0 = R * 1.035
        lines.segment((r0 * c, r0 * s, 0), ((r0 + ln) * c, (r0 + ln) * s, 0),
                      1.0 if k % 20 == 0 else 0.5, u0=k / 240, u1=k / 240)
    # thick arc segments
    for k in range(7):
        a0 = rng.random() * TAU
        span = 0.15 + rng.random() * 0.7
        for dr in (0.0, 0.006, 0.012):
            p, u = circle(R * 1.07 + dr, 48, 0.0, a0, a0 + span)
            lines.polyline(p, u, 0.9, seed=k / 7)
    return lines.build()


# ---------------------------------------------------------------------------
# Particles
# ---------------------------------------------------------------------------
def particle_shell(n: int, r0: float, r1: float, rng: np.random.Generator,
                   size=(0.6, 2.2), speed=(-0.12, 0.12)) -> np.ndarray:
    dirs = random_unit(rng, n)
    radii = (r0 + (r1 - r0) * rng.random(n) ** 0.7).astype(np.float32)
    pos = dirs * radii[:, None]
    seeds = rng.random(n).astype(np.float32)
    sizes = (size[0] + (size[1] - size[0]) * rng.random(n) ** 3).astype(np.float32)
    speeds = (speed[0] + (speed[1] - speed[0]) * rng.random(n)).astype(np.float32)
    return np.ascontiguousarray(np.column_stack([pos, seeds, sizes, speeds]), dtype=np.float32)


def particle_disc(n: int, r0: float, r1: float, thick: float, rng: np.random.Generator) -> np.ndarray:
    a = rng.random(n) * TAU
    r = r0 + (r1 - r0) * rng.random(n)
    y = rng.normal(scale=thick, size=n)
    pos = np.column_stack([r * np.cos(a), y, r * np.sin(a)]).astype(np.float32)
    seeds = rng.random(n).astype(np.float32)
    sizes = (0.5 + 1.6 * rng.random(n) ** 3).astype(np.float32)
    speeds = (0.15 + 0.35 * rng.random(n)).astype(np.float32)
    return np.ascontiguousarray(np.column_stack([pos, seeds, sizes, speeds]), dtype=np.float32)


def node_points(nodes: np.ndarray, rng: np.random.Generator, size=(1.5, 3.0)) -> np.ndarray:
    n = len(nodes)
    seeds = rng.random(n).astype(np.float32)
    sizes = (size[0] + (size[1] - size[0]) * rng.random(n)).astype(np.float32)
    speeds = np.zeros(n, np.float32)
    return np.ascontiguousarray(np.column_stack([nodes, seeds, sizes, speeds]), dtype=np.float32)


# ---------------------------------------------------------------------------
# A.T.O.M scene helpers
# ---------------------------------------------------------------------------
def orb_quads(centers, sizes, seeds) -> np.ndarray:
    """Camera-facing quads for glowing orbs. Layout: centre(3) corner(2) seed(1) size(1)."""
    corners = np.array([[-1, -1], [1, -1], [1, 1], [-1, -1], [1, 1], [-1, 1]], np.float32)
    centers = np.asarray(centers, np.float32).reshape(-1, 3)
    n = len(centers)
    out = np.empty((n * 6, 7), np.float32)
    for k in range(6):
        out[k::6, 0:3] = centers
        out[k::6, 3:5] = corners[k]
        out[k::6, 5] = np.asarray(seeds, np.float32)
        out[k::6, 6] = np.asarray(sizes, np.float32)
    return np.ascontiguousarray(out)


def floating_orbs(n: int, r0: float, r1: float, rng: np.random.Generator, min_sep: float = 0.32):
    pts = []
    tries = 0
    while len(pts) < n and tries < n * 200:
        tries += 1
        d = random_unit(rng, 1)[0]
        d[1] *= 0.75  # flatter cloud
        p = d / np.linalg.norm(d) * (r0 + (r1 - r0) * rng.random())
        if all(np.linalg.norm(p - q) >= min_sep for q in pts):
            pts.append(p)
    return np.array(pts, np.float32)


def nearest_links(points: np.ndarray, k: int, max_dist: float, rng: np.random.Generator, v: float = 0.6):
    lines = LineBuilder()
    seen = set()
    d = np.linalg.norm(points[:, None, :] - points[None, :, :], axis=2)
    for i in range(len(points)):
        for j in np.argsort(d[i])[1:k + 1]:
            key = (min(i, j), max(i, j))
            if key in seen or d[i, j] > max_dist:
                continue
            seen.add(key)
            lines.polyline([points[i], points[j]], [0.0, 1.0], v, seed=rng.random())
    return lines.build()


# ---------------------------------------------------------------------------
# R.U.F.U.S scene helpers (data globe / radar table)
# ---------------------------------------------------------------------------
def land_mask(p: np.ndarray) -> np.ndarray:
    """Procedural 'continents' – smooth noise on the sphere (not a real map)."""
    x, y, z = p[:, 0], p[:, 1], p[:, 2]
    f = (np.sin(3.1 * x + 1.3) * np.cos(2.3 * y - 0.5) + 0.8 * np.sin(2.7 * z + 0.7)
         + 0.35 * np.sin(5.1 * (x + z)) + 0.25 * np.cos(6.3 * y + 2.0 * x))
    return f


def globe_dots(R: float, n: int, rng: np.random.Generator):
    """Dot-matrix globe: bright dots on 'land', faint sparse dots on 'sea'."""
    p = fibonacci_sphere(n)
    m = land_mask(p)
    land = m > 0.35
    sea = (~land) & (rng.random(n) < 0.18)
    keep = land | sea
    pts = p[keep] * R
    sizes = np.where(land[keep], 1.6 + 0.9 * rng.random(keep.sum()), 0.8).astype(np.float32)
    seeds = rng.random(keep.sum()).astype(np.float32)
    speeds = np.zeros(keep.sum(), np.float32)
    land_pts = (p[land] * R).astype(np.float32)
    return np.ascontiguousarray(np.column_stack([pts, seeds, sizes, speeds]), dtype=np.float32), land_pts


def data_arcs(nodes: np.ndarray, count: int, rng: np.random.Generator, lift: float = 0.35, res: int = 40):
    """Raised great-circle arcs between surface nodes (data links)."""
    lines = LineBuilder()
    R = float(np.linalg.norm(nodes[0]))
    for _ in range(count):
        i, j = rng.choice(len(nodes), 2, replace=False)
        a, b = nodes[i] / R, nodes[j] / R
        ang = math.acos(float(np.clip(np.dot(a, b), -1, 1)))
        if ang < 0.35:
            continue
        ts = np.linspace(0, 1, res)
        so = math.sin(ang)
        pts = np.array([(math.sin((1 - t) * ang) * a + math.sin(t * ang) * b) / so for t in ts])
        h = 1.0 + lift * (ang / math.pi) * np.sin(math.pi * ts)
        pts = pts * (R * h)[:, None]
        lines.polyline(pts, ts, 0.9, seed=rng.random())
    return lines.build()


def radar_table(R: float, rng: np.random.Generator):
    """Flat radar display (XZ plane): rings, spokes and ticks."""
    lines = LineBuilder()
    for k, r in enumerate(np.linspace(R * 0.25, R, 5)):
        t = np.linspace(0, TAU, 220, endpoint=False)
        pts = np.column_stack([r * np.cos(t), np.zeros_like(t), r * np.sin(t)])
        lines.polyline(pts, t / TAU, 0.9 if k == 4 else 0.45, seed=k / 5, closed=True)
    for k in range(12):
        a = TAU * k / 12
        lines.segment((0.08 * math.cos(a), 0, 0.08 * math.sin(a)), (R * math.cos(a), 0, R * math.sin(a)),
                      0.25, u0=k / 12, u1=k / 12)
    for k in range(180):
        a = TAU * k / 180
        ln = 0.06 if k % 15 == 0 else 0.025
        lines.segment((R * math.cos(a), 0, R * math.sin(a)), ((R + ln) * math.cos(a), 0, (R + ln) * math.sin(a)),
                      0.8 if k % 15 == 0 else 0.4, u0=k / 180, u1=k / 180)
    # scattered contacts
    for _ in range(14):
        a, r = rng.random() * TAU, R * (0.2 + 0.75 * rng.random())
        c = np.array([r * math.cos(a), 0, r * math.sin(a)])
        s = 0.02
        lines.polyline([c + (-s, 0, -s), c + (s, 0, -s), c + (s, 0, s), c + (-s, 0, s)], [a / TAU] * 4, 1.2,
                       seed=rng.random(), closed=True)
    return lines.build()


def sweep_fan(R: float, res: int = 180):
    """Full disc as a triangle fan; the shader's travelling highlight turns it into a radar sweep."""
    t = np.linspace(0, TAU, res + 1)
    out = []
    for k in range(res):
        u0, u1 = t[k] / TAU, t[k + 1] / TAU
        out += [(0, 0, 0, u0, 0.05, 0), (R * math.cos(t[k]), 0, R * math.sin(t[k]), u0, 1.0, 0),
                (R * math.cos(t[k + 1]), 0, R * math.sin(t[k + 1]), u1, 1.0, 0)]
    return np.array(out, np.float32)


# ---------------------------------------------------------------------------
# On-disk cache of generated geometry (Settings › Performance › Cache hologram geometry)
# ---------------------------------------------------------------------------
class GeoCache:
    """Proxy for this module: calls like ``GC.gyro_ring(...)`` are computed once, then loaded from disk.

    Results are keyed by call order, so a scene builder must call geometry functions in the same
    order every time (it does). The file name includes a hash of the source code, so any change
    to the geometry or scene code invalidates the cache automatically.
    """

    def __init__(self, path, enabled: bool = True):
        import sys
        self._mod = sys.modules[__name__]
        self._path = path
        self._enabled = enabled
        self._i = 0
        self._data: dict[str, list] = {}
        self._new: dict[str, object] = {}
        if enabled and path is not None and path.exists():
            try:
                with np.load(path) as z:
                    for k in z.files:
                        key, _, part = k.partition("__")
                        self._data.setdefault(key, []).append((part, z[k]))
            except Exception:
                self._data = {}

    def __getattr__(self, name):
        attr = getattr(self._mod, name)
        if isinstance(attr, type) or not callable(attr):
            return attr

        def call(*args, **kwargs):
            key = f"{self._i:03d}_{name}"
            self._i += 1
            if key in self._data:
                parts = sorted(self._data[key], key=lambda p: p[0])
                if len(parts) == 1 and parts[0][0] == "a":
                    return parts[0][1]
                return tuple(arr for _p, arr in parts)
            res = attr(*args, **kwargs)
            self._new[key] = res
            return res
        return call

    def save(self):
        if not (self._enabled and self._new and self._path is not None):
            return
        flat = {}
        for key, (parts) in list(self._data.items()):
            for part, arr in parts:
                flat[f"{key}__{part}"] = arr
        for key, res in self._new.items():
            if isinstance(res, tuple):
                for j, arr in enumerate(res):
                    flat[f"{key}__t{j}"] = np.asarray(arr)
            else:
                flat[f"{key}__a"] = np.asarray(res)
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_suffix(".tmp.npz")
            np.savez(tmp, **flat)
            tmp.replace(self._path)
        except Exception:
            pass
