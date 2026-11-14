"""Live vehicles from API positions."""
from __future__ import annotations

from datetime import datetime

import numpy as np

from ..api.models import Position, Telemetry
from ..domain import Route, Stop, Vehicle
from ..geometry import M_PER_DEG, bearing
from ..gtfs import Feed

MIN_MOVE_M = 5.0   


def distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    dy = (lat2 - lat1) * M_PER_DEG
    dx = (lon2 - lon1) * M_PER_DEG * np.cos(np.radians((lat1 + lat2) / 2))
    return float(np.hypot(dx, dy))


def nearest_stop(feed: Feed, route_id: str, lat: float, lon: float) -> tuple[Stop, float] | None:
    sids = feed.stops_by_route.get(route_id) or []
    if not sids:
        return None
    stops = [feed.stops[s] for s in sids]
    d = [distance_m(lat, lon, s.lat, s.lon) for s in stops]
    i = int(np.argmin(d))
    return stops[i], d[i]


class HeadingTracker:

    def __init__(self) -> None:
        self._last: dict[int, tuple[float, float, float | None]] = {}

    def update(self, bus_id: int, lat: float, lon: float) -> float | None:
        prev = self._last.get(bus_id)
        if prev is None:
            self._last[bus_id] = (lat, lon, None)
            return None
        if distance_m(prev[0], prev[1], lat, lon) >= MIN_MOVE_M:
            heading = bearing(prev[0], prev[1], lat, lon)
            self._last[bus_id] = (lat, lon, heading)
            return heading
        return prev[2]


def live_vehicles(positions: list[Position], route: Route, feed: Feed, tracker: HeadingTracker,
                  max_age_s: float | None, now: datetime | None = None) -> list[Vehicle]:
    out = []
    for p in positions:
        ll = p.latlon
        if ll is None:
            continue
        age = p.age_s(now)
        if max_age_s is not None and age is not None and age > max_age_s:
            continue
        ns = nearest_stop(feed, route.route_id, *ll)
        out.append(Vehicle(
            vehicle_id=str(p.bus_id),
            route_id=route.route_id,
            trip_id=None,
            lat=ll[0],
            lon=ll[1],
            heading=tracker.update(p.bus_id, *ll),
            path=[],
            headsign="",
            prev_stop="",
            next_stop=ns[0].name if ns else "",
            speed_kmh=p.speed_kmh,
            source="live",
            label=f"Bus {p.bus_id}",
        ))
    return out


def trail_coords(history: list[Telemetry], seconds: float | None = None) -> list[list[float]]:
    """The bus's recent track, oldest to newest. With `seconds`: only the positions received in that long
    before the newest one, so the trail keeps one length and its old end drops off as the bus moves."""
    pts = [t for t in history if t.latlon]
    if seconds is not None:
        newest = max((t.received_at for t in pts if t.received_at), default=None)
        if newest is not None:
            pts = [t for t in pts if t.received_at and (newest - t.received_at).total_seconds() <= seconds]
    pts.sort(key=lambda t: (t.received_at.timestamp() if t.received_at else 0, t.ts or 0, t.uptime_ms or 0))
    return [[t.lat, t.lon] for t in pts]