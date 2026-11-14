from __future__ import annotations

from dataclasses import dataclass
from functools import cached_property

import numpy as np

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


@dataclass(frozen=True, eq=False)
class Shape:
    coords: np.ndarray   
    cum: np.ndarray      
    scale: np.ndarray    

    @classmethod
    def from_coords(cls, coords) -> "Shape":
        coords = np.asarray(coords, dtype=float)
        scale = _scale(float(coords[:, 0].mean()))
        seg = np.linalg.norm(np.diff(coords * scale, axis=0), axis=1)
        return cls(coords, np.concatenate([[0.0], np.cumsum(seg)]), scale)

    @property
    def length(self) -> float:
        return float(self.cum[-1])

    @cached_property
    def lonlat(self) -> list[list[float]]:
        return self.coords[:, ::-1].tolist()

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