from __future__ import annotations

import time
from datetime import datetime

import numpy as np

from ..domain import Trip, Vehicle
from ..geometry import bearing
from ..gtfs import Feed


class GtfsRtProvider:
    source = "realtime"

    def __init__(self, feed: Feed, url: str, ttl_s: float = 10.0, timeout_s: float = 5.0,
                 headers: dict[str, str] | None = None):
        from google.transit import gtfs_realtime_pb2  # fail fast if dependency missing

        self._pb = gtfs_realtime_pb2
        self.feed = feed
        self.url = url
        self.ttl_s = ttl_s
        self.timeout_s = timeout_s
        self.headers = headers or {}
        self._cache: tuple[float, object] | None = None

    def _message(self):
        now = time.monotonic()
        if self._cache and now - self._cache[0] < self.ttl_s:
            return self._cache[1]
        import requests

        resp = requests.get(self.url, timeout=self.timeout_s, headers=self.headers)
        resp.raise_for_status()
        msg = self._pb.FeedMessage()
        msg.ParseFromString(resp.content)
        self._cache = (now, msg)
        return msg

    def vehicles(self, route_id: str, now: datetime) -> list[Vehicle]:
        out = []
        for ent in self._message().entity:
            if not ent.HasField("vehicle"):
                continue
            vp = ent.vehicle
            if not vp.HasField("position"):
                continue
            trip = self.feed.trips.get(vp.trip.trip_id)
            rid = vp.trip.route_id or (trip.route_id if trip else "")
            if rid == route_id:
                out.append(self._to_vehicle(ent.id, vp, trip, rid))
        return out

    def _next_stop_index(self, trip: Trip, vp, d_here: float | None, lat: float, lon: float) -> int:
        n = len(trip.stop_ids)
        if vp.stop_id and vp.stop_id in trip.stop_ids:
            i = trip.stop_ids.index(vp.stop_id)
            stopped = vp.current_status == self._pb.VehiclePosition.STOPPED_AT
            return min(i + 1 if stopped else i, n - 1)
        ds = self.feed.stop_distances(trip)
        if ds is not None and d_here is not None:
            return int(min(np.searchsorted(ds, d_here, side="right"), n - 1))
        return int(np.argmin(np.linalg.norm(trip.coords - [lat, lon], axis=1)))

    def _to_vehicle(self, entity_id: str, vp, trip: Trip | None, route_id: str) -> Vehicle:
        pos = vp.position
        lat, lon = float(pos.latitude), float(pos.longitude)
        heading = float(pos.bearing) if pos.HasField("bearing") else None
        speed = round(float(pos.speed) * 3.6, 1) if pos.HasField("speed") else None
        path: list[list[float]] = [[lat, lon]]
        prev_stop = next_stop = "—"

        if trip is not None:
            shape = self.feed.shape_for(trip)
            ds = self.feed.stop_distances(trip)
            d_here = float(shape.project([[lat, lon]], monotonic=False)[0]) if shape else None
            i = self._next_stop_index(trip, vp, d_here, lat, lon)
            next_stop = self.feed.stop_name(trip.stop_ids[i])
            prev_stop = self.feed.stop_name(trip.stop_ids[i - 1]) if i > 0 else "—"
            if shape is not None and ds is not None and d_here is not None:
                path = shape.path_between(d_here, max(float(ds[i]), d_here))
                heading = heading if heading is not None else shape.heading_at(d_here)
            else:
                path = [[lat, lon], trip.coords[i].tolist()]
            if heading is None:
                heading = bearing(lat, lon, *trip.coords[i])

        vid = vp.vehicle.id or entity_id
        return Vehicle(
            vehicle_id=vid,
            route_id=route_id,
            trip_id=trip.trip_id if trip else (vp.trip.trip_id or None),
            lat=lat,
            lon=lon,
            heading=heading or 0.0,
            path=path,
            headsign=trip.headsign if trip else "",
            prev_stop=prev_stop,
            next_stop=next_stop,
            speed_kmh=speed,
            source=self.source,
            label=vp.vehicle.label or None,
        )