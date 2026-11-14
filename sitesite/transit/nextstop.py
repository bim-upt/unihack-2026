"""The next stop of a live bus, and the path to it along its line.

The API doesn't say which way a bus runs, so it is worked out: the bus is projected on each of its line's GTFS
shapes (one per direction); the shape it is on and moves forward along (an earlier position projects behind the
current one) is its direction. The next stop is the first stop of that shape at or ahead of the bus.
"""
from __future__ import annotations

import functools
from dataclasses import dataclass

import numpy as np

from .domain import Stop
from .gtfs import Feed

MAX_OFF_M = 60.0        # further from a shape than this: not on it
MIN_MOVE_M = 20.0       # an earlier position at least this far away tells the direction
MAX_LOOKBACK_S = 180    # ... and no older than this
AT_STOP_M = 15.0        # a stop this close behind still counts (the bus is at it)


@dataclass(frozen=True)
class NextStop:
    stop: Stop
    distance_m: float               # along the line, bus to stop
    path: list[list[float]]         # [lat, lon] points from the bus to the stop, along the line


@functools.lru_cache(maxsize=512)
def _shape_stops(feed: Feed, route_id: str, shape_id: str) -> tuple[tuple[str, ...], np.ndarray] | None:
    """Stops of a trip that uses this shape, with their distance along it."""
    for tid in feed.trips_by_route.get(route_id, ()):
        trip = feed.trips[tid]
        if trip.shape_id == shape_id:
            dist = feed.stop_distances(trip)
            if dist is not None:
                return trip.stop_ids, dist
    return None


def _locate(shape, latlon: tuple[float, float]) -> tuple[float, float]:
    """(distance along the shape, distance off it) in metres, at the nearest shape point."""
    d = np.linalg.norm((np.asarray(latlon) * shape.scale) - (shape.coords * shape.scale), axis=1)
    i = int(d.argmin())
    return float(shape.cum[i]), float(d[i])


def next_stop(feed: Feed, route_id: str, history) -> NextStop | None:
    """`history`: the bus's recent positions (Telemetry, any order). None when it can't be told."""
    pts = sorted((t for t in history or [] if t.latlon and t.received_at), key=lambda t: t.received_at)
    if not pts:
        return None
    now = pts[-1]
    earlier = None
    for t in reversed(pts[:-1]):
        if (now.received_at - t.received_at).total_seconds() > MAX_LOOKBACK_S:
            break
        dy = (t.lat - now.lat) * 111_320
        dx = (t.lon - now.lon) * 111_320 * np.cos(np.radians(now.lat))
        if (dx * dx + dy * dy) ** 0.5 >= MIN_MOVE_M:
            earlier = t
            break

    best = None
    for sid in feed.shapes_by_route.get(route_id, ()):
        shape = feed.shapes.get(sid)
        stops = _shape_stops(feed, route_id, sid)
        if shape is None or stops is None:
            continue
        along, off = _locate(shape, now.latlon)
        if off > MAX_OFF_M:
            continue
        if earlier is not None:
            before, _ = _locate(shape, earlier.latlon)
            if along - before <= 0:          # going the other way on this shape
                continue
            score = off
        else:
            score = off + 1000.0              # standing still since long: direction unknown, any fitting shape
        stop_ids, dist = stops
        ahead = [i for i, d in enumerate(dist) if d >= along - AT_STOP_M]
        if not ahead:
            continue                          # past the last stop of this shape
        i = ahead[0]
        if best is None or score < best[0]:
            best = (score, shape, along, stop_ids[i], float(dist[i]))
    if best is None:
        return None
    _, shape, along, stop_id, stop_d = best
    stop = feed.stops.get(stop_id)
    if stop is None:
        return None
    return NextStop(stop, max(stop_d - along, 0.0), shape.path_between(along, max(stop_d, along)))
