"""Small 4x4 matrix helpers (row-major numpy, uploaded with transpose=GL_TRUE)."""
from __future__ import annotations

import math
import numpy as np


def identity() -> np.ndarray:
    return np.eye(4, dtype=np.float32)


def perspective(fovy_deg: float, aspect: float, near: float, far: float) -> np.ndarray:
    f = 1.0 / math.tan(math.radians(fovy_deg) / 2.0)
    m = np.zeros((4, 4), dtype=np.float32)
    m[0, 0] = f / aspect
    m[1, 1] = f
    m[2, 2] = (far + near) / (near - far)
    m[2, 3] = (2.0 * far * near) / (near - far)
    m[3, 2] = -1.0
    return m


def look_at(eye, target, up=(0.0, 1.0, 0.0)) -> np.ndarray:
    eye = np.asarray(eye, dtype=np.float32)
    target = np.asarray(target, dtype=np.float32)
    up = np.asarray(up, dtype=np.float32)
    f = target - eye
    f /= np.linalg.norm(f)
    s = np.cross(f, up)
    s /= np.linalg.norm(s)
    u = np.cross(s, f)
    m = identity()
    m[0, :3] = s
    m[1, :3] = u
    m[2, :3] = -f
    m[0, 3] = -np.dot(s, eye)
    m[1, 3] = -np.dot(u, eye)
    m[2, 3] = np.dot(f, eye)
    return m


def rotation(axis, angle: float) -> np.ndarray:
    axis = np.asarray(axis, dtype=np.float64)
    axis = axis / np.linalg.norm(axis)
    x, y, z = axis
    c, s = math.cos(angle), math.sin(angle)
    C = 1.0 - c
    m = identity()
    m[:3, :3] = np.array(
        [
            [c + x * x * C, x * y * C - z * s, x * z * C + y * s],
            [y * x * C + z * s, c + y * y * C, y * z * C - x * s],
            [z * x * C - y * s, z * y * C + x * s, c + z * z * C],
        ],
        dtype=np.float32,
    )
    return m


def rx(a: float) -> np.ndarray:
    return rotation((1, 0, 0), a)


def ry(a: float) -> np.ndarray:
    return rotation((0, 1, 0), a)


def rz(a: float) -> np.ndarray:
    return rotation((0, 0, 1), a)


def translate(x: float, y: float, z: float) -> np.ndarray:
    m = identity()
    m[:3, 3] = (x, y, z)
    return m


def scale(sx: float, sy: float | None = None, sz: float | None = None) -> np.ndarray:
    sy = sx if sy is None else sy
    sz = sx if sz is None else sz
    m = identity()
    m[0, 0], m[1, 1], m[2, 2] = sx, sy, sz
    return m


def chain(*ms: np.ndarray) -> np.ndarray:
    out = identity()
    for m in ms:
        out = out @ m
    return out
