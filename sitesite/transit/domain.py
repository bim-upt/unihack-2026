"""Core data types shared by every layer."""
from __future__ import annotations

import re
from dataclasses import dataclass

import numpy as np

from .config import MODE_NAMES


@dataclass(frozen=True)
class Route:
    route_id: str
    short_name: str
    long_name: str
    route_type: str
    color: str        # resolved "#rrggbb"
    text_color: str   # resolved "#rrggbb"

    @property
    def mode_name(self) -> str:
        return MODE_NAMES.get(self.route_type, "Vehicle")

    @property
    def label(self) -> str:
        return f"{self.short_name} · {self.long_name}"


@dataclass(frozen=True)
class Stop:
    stop_id: str
    name: str
    lat: float
    lon: float


@dataclass(frozen=True, eq=False)
class Trip:
    trip_id: str
    route_id: str
    service_id: str
    shape_id: str | None
    headsign: str
    times: np.ndarray            # seconds after service-day start, one per stop
    stop_ids: tuple[str, ...]
    coords: np.ndarray           # (n, 2) lat, lon per stop

    @property
    def start(self) -> float:
        return float(self.times[0])

    @property
    def end(self) -> float:
        return float(self.times[-1])


@dataclass(frozen=True)
class Vehicle:
    vehicle_id: str              
    route_id: str                
    trip_id: str | None
    lat: float
    lon: float
    heading: float | None        
    path: list[list[float]]      
    headsign: str
    prev_stop: str
    next_stop: str
    speed_kmh: float | None
    source: str                
    label: str | None = None

    @property
    def css_id(self) -> str:
        return re.sub(r"[^A-Za-z0-9_-]", "_", self.vehicle_id)