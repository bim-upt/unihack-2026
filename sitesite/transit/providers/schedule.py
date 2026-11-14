from __future__ import annotations

from datetime import datetime, timedelta

import numpy as np

from ..domain import Trip, Vehicle
from ..geometry import bearing
from ..gtfs import Feed


class ScheduleProvider:
    source = "schedule"

    def __init__(self, feed: Feed):
        self.feed = feed

    def vehicles(self, route_id: str, now: datetime) -> list[Vehicle]:
        secs = now.hour * 3600 + now.minute * 60 + now.second
        today = now.date()
        found: dict[str, Vehicle] = {}

        for day, t in ((today, secs), (today - timedelta(days=1), secs + 86_400)):
            services = self.feed.services_on(day)
            for tid in self.feed.trips_by_route.get(route_id, ()):
                trip = self.feed.trips[tid]
                if tid not in found and trip.service_id in services and trip.start <= t <= trip.end:
                    found[tid] = self._position(trip, t)
        return list(found.values())

    def _position(self, trip: Trip, t: float) -> Vehicle:
        times, coords = trip.times, trip.coords
        j = int(np.clip(np.searchsorted(times, t, side="right") - 1, 0, len(times) - 2))
        dt = max(float(times[j + 1] - times[j]), 1.0)
        frac = float(np.clip((t - times[j]) / dt, 0.0, 1.0))
        nxt = (float(coords[j + 1, 0]), float(coords[j + 1, 1]))

        shape = self.feed.shape_for(trip)
        ds = self.feed.stop_distances(trip)
        if shape is not None and ds is not None:
            d_here = ds[j] + frac * (ds[j + 1] - ds[j])
            d_next = max(ds[j + 1], d_here)
            lat, lon = shape.point_at(d_here)
            heading = shape.heading_at(d_here)
            path = shape.path_between(d_here, d_next)
            speed = round(float((ds[j + 1] - ds[j]) / dt * 3.6), 1)
        else:
            lat, lon = (float(x) for x in coords[j] + frac * (coords[j + 1] - coords[j]))
            heading, speed = None, None
            path = [[lat, lon], list(nxt)]

        if heading is None:
            heading = bearing(lat, lon, *nxt) or 0.0

        return Vehicle(
            vehicle_id=trip.trip_id,
            route_id=trip.route_id,
            trip_id=trip.trip_id,
            lat=lat,
            lon=lon,
            heading=heading,
            path=path,
            headsign=trip.headsign,
            prev_stop=self.feed.stop_name(trip.stop_ids[j]),
            next_stop=self.feed.stop_name(trip.stop_ids[j + 1]),
            speed_kmh=speed,
            source=self.source,
        )