"""Links GTFS lines to the server's numeric route ids.

The API gives no route names to the display, so the link is made from geometry: the recent positions of
the buses on each API route are compared with the shapes of every GTFS line, and the line they lie on wins.
Built at startup (and again every few minutes), so nothing is hard-coded.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .gtfs import Feed

ON_LINE_M = 25.0          # a point this close to a line's shape counts as on it


@dataclass(frozen=True, eq=False)
class RouteLink:
    gtfs_to_api: dict[str, int] = field(default_factory=dict)
    api_to_gtfs: dict[int, str] = field(default_factory=dict)
    median_m: dict[int, float] = field(default_factory=dict)    # api route -> how well it matched
    unmatched: tuple[int, ...] = ()                               # api routes no line fits

    def api_id(self, gtfs_route_id: str) -> int | None:
        return self.gtfs_to_api.get(gtfs_route_id)

    def gtfs_id(self, api_route_id: int | None) -> str | None:
        return None if api_route_id is None else self.api_to_gtfs.get(api_route_id)


@dataclass(frozen=True)
class _Segments:
    a: np.ndarray       # (k, 2) segment starts, metres
    ab: np.ndarray      # (k, 2) segment vectors
    len2: np.ndarray    # (k,)
    lo: np.ndarray      # bbox, metres
    hi: np.ndarray


def _scale(lat0: float) -> np.ndarray:
    return np.array([111_320.0, 111_320.0 * np.cos(np.radians(lat0))])


def _route_segments(feed: Feed, scale: np.ndarray) -> dict[str, _Segments]:
    out = {}
    for rid, shape_ids in feed.shapes_by_route.items():
        starts, ends = [], []
        for sid in shape_ids:
            shape = feed.shapes.get(sid)
            if shape is None or len(shape.coords) < 2:
                continue
            c = shape.coords * scale
            starts.append(c[:-1])
            ends.append(c[1:])
        if not starts:
            continue
        a, b = np.concatenate(starts), np.concatenate(ends)
        ab = b - a
        len2 = np.maximum((ab ** 2).sum(1), 1e-9)
        pts = np.concatenate([a, b])
        out[rid] = _Segments(a, ab, len2, pts.min(0), pts.max(0))
    return out


def _distances(points: np.ndarray, seg: _Segments) -> np.ndarray:
    """Distance (m) from each point to the nearest segment."""
    ap = points[:, None, :] - seg.a[None]
    t = np.clip((ap * seg.ab[None]).sum(2) / seg.len2[None], 0.0, 1.0)
    d = ap - t[..., None] * seg.ab[None]
    return np.sqrt((d ** 2).sum(2)).min(1)


def match_routes(feed: Feed, samples: dict[int, np.ndarray], *, max_median_m: float = 30.0,
                 min_on_line: float = 0.8) -> RouteLink:
    """`samples`: api route id -> (n, 2) lat/lon of its buses' recent positions."""
    samples = {r: np.asarray(p, dtype=float) for r, p in samples.items() if len(p)}
    if not samples:
        return RouteLink()
    (lat_lo, _), (lat_hi, _) = feed.bounds()
    scale = _scale((lat_lo + lat_hi) / 2)
    segments = _route_segments(feed, scale)

    candidates: list[tuple[float, int, str]] = []
    for api_rid, latlon in samples.items():
        pts = latlon * scale
        for rid, seg in segments.items():
            inside = ((pts >= seg.lo - ON_LINE_M) & (pts <= seg.hi + ON_LINE_M)).all(1).mean()
            if inside < min_on_line:
                continue
            d = _distances(pts, seg)
            med = float(np.median(d))
            if med <= max_median_m and (d <= ON_LINE_M).mean() >= min_on_line:
                candidates.append((med, api_rid, rid))

    # best fits first; each api route and each GTFS line is used once
    gtfs_to_api: dict[str, int] = {}
    api_to_gtfs: dict[int, str] = {}
    median_m: dict[int, float] = {}
    for med, api_rid, rid in sorted(candidates):
        if api_rid in api_to_gtfs or rid in gtfs_to_api:
            continue
        gtfs_to_api[rid] = api_rid
        api_to_gtfs[api_rid] = rid
        median_m[api_rid] = med
    unmatched = tuple(sorted(r for r in samples if r not in api_to_gtfs))
    return RouteLink(gtfs_to_api, api_to_gtfs, median_m, unmatched)
