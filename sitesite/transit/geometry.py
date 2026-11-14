from __future__ import annotations

import math

from dataclasses import dataclass

import numpy as np

from .config import Bounds

M_PER_DEG = 111_320.0


def _scale(lat0: float) -> np.ndarray:
    return np.array([M_PER_DEG, M_PER_DEG * np.cos(np.radians(lat0))])


def bearing(lat1: float, lon1: float, lat2: float, lon2: float) -> float | None:
    #0 = north
    dy = (lat2 - lat1) * M_PER_DEG
    dx = (lon2 - lon1) * M_PER_DEG * np.cos(np.radians(lat1))
    if abs(dx) < 1e-6 and abs(dy) < 1e-6:
        return None
    return float(np.degrees(np.arctan2(dx, dy)) % 360)


def pad_bounds(bounds: Bounds, km: float) -> Bounds:
    (s, w), (n, e) = bounds
    dlat = km * 1000 / M_PER_DEG
    dlon = km * 1000 / (M_PER_DEG * np.cos(np.radians((s + n) / 2)))
    return (s - dlat, w - dlon), (n + dlat, e + dlon)


def simplify(coords: np.ndarray, scale: np.ndarray, tol_m: float) -> np.ndarray:
    n = len(coords)
    if tol_m <= 0 or n < 3:
        return coords
    pts = coords * scale
    keep = np.zeros(n, dtype=bool)
    keep[0] = keep[-1] = True
    stack = [(0, n - 1)]
    while stack:
        i, j = stack.pop()
        if j <= i + 1:
            continue
        a, ab = pts[i], pts[j] - pts[i]
        seg = pts[i + 1:j]
        l2 = float(ab @ ab)
        if l2 == 0.0:
            d = np.linalg.norm(seg - a, axis=1)
        else:
            t = np.clip(((seg - a) @ ab) / l2, 0.0, 1.0)
            d = np.linalg.norm(seg - (a + t[:, None] * ab), axis=1)
        k = int(d.argmax())
        if d[k] > tol_m:
            m = i + 1 + k
            keep[m] = True
            stack.extend(((i, m), (m, j)))
    return coords[keep]


@dataclass(frozen=True, eq=False)
class Shape:
    coords: np.ndarray            
    cum: np.ndarray
    scale: np.ndarray
    lonlat: list[list[float]]

    @classmethod
    def from_coords(cls, coords, simplify_m: float = 0.0) -> "Shape":
        coords = np.asarray(coords, dtype=float)
        scale = _scale(float(coords[:, 0].mean()))
        seg = np.linalg.norm(np.diff(coords * scale, axis=0), axis=1)

        cum = np.concatenate([[0.0], np.cumsum(seg)])
        display = simplify(coords, scale, simplify_m)
        return cls(coords, cum, scale, display[:, ::-1].tolist())

    @property
    def length(self) -> float:
        return float(self.cum[-1])

    def project(self, pts, monotonic: bool = True) -> np.ndarray:
        pts = np.atleast_2d(np.asarray(pts, dtype=float))
        d = np.linalg.norm(
            (pts * self.scale)[:, None, :] - (self.coords * self.scale)[None, :, :], axis=2
        )
        idx = d.argmin(axis=1)
        if monotonic:
            idx = np.maximum.accumulate(idx)
        return self.cum[idx]

    def point_at(self, d: float) -> tuple[float, float]:
        return (
            float(np.interp(d, self.cum, self.coords[:, 0])),
            float(np.interp(d, self.cum, self.coords[:, 1])),
        )

    def heading_at(self, d: float, lookahead: float = 20.0) -> float | None:
        ahead = min(d + lookahead, self.length)
        if ahead <= d:
            return bearing(*self.point_at(max(d - lookahead, 0.0)), *self.point_at(d))
        return bearing(*self.point_at(d), *self.point_at(ahead))

    def path_between(self, d0: float, d1: float) -> list[list[float]]:
        mid = self.coords[(self.cum > d0) & (self.cum < d1)]
        return [list(self.point_at(d0)), *mid.tolist(), list(self.point_at(d1))]

def view_for(bounds: Bounds, width_px: int, height_px: int, max_zoom: int, pad: float = 0.1
             ) -> tuple[tuple[float, float], int]:
    """Center and the largest whole zoom (Web Mercator, 256 px tiles) that shows `bounds` in a frame of the
    given size, with `pad` of the frame left free around it."""
    (s, w), (n, e) = bounds
    merc = lambda lat: math.log(math.tan(math.pi / 4 + math.radians(lat) / 2))   # noqa: E731
    dx = max(e - w, 1e-6) / 360.0
    dy = max(merc(n) - merc(s), 1e-6) / (2 * math.pi)
    usable_w, usable_h = width_px * (1 - 2 * pad), height_px * (1 - 2 * pad)
    zoom = math.floor(min(math.log2(usable_w / (256 * dx)), math.log2(usable_h / (256 * dy))))
    return ((s + n) / 2, (w + e) / 2), max(0, min(zoom, max_zoom))
