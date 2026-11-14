"""App configuration. Defaults can be overridden with environment variables."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo

MODE_NAMES = {"0": "Tram", "3": "Bus", "11": "Trolleybus"}
Bounds = tuple[tuple[float, float], tuple[float, float]]
VEHICLE_SOURCES = ("live", "schedule")


@dataclass(frozen=True)
class Settings:
    gtfs_dir: Path = Path("gtfs")
    tz: ZoneInfo = ZoneInfo("Europe/Bucharest")
    map_height: int = 680
    tiles: str = "OpenStreetMap"
    tile_filter: str = "grayscale(1) contrast(0.8) brightness(1.12)"

    area_bounds: Bounds | None = None
    area_pad_km: float = 2.0
    max_zoom: int = 17
    # single tile LOD used at every zoom (scaled); None = normal per-zoom tiles
    tile_native_zoom: int | None = 14
    simplify_m: float = 2.0

    stop_radius: int = 8
    hit_px: int = 22
    line_weight: int = 4
    line_weight_selected: int = 7

    # vehicles
    vehicle_source: str = "live"                    
    route_map_file: Path = Path("route_map.csv")                       
    history_limit: int = 50                         

    @classmethod
    def from_env(cls) -> "Settings":
        source = os.getenv("TRANSIT_VEHICLE_SOURCE", "live")
        return cls(
            gtfs_dir=Path(os.getenv("TRANSIT_GTFS_DIR", "gtfs")),
            route_map_file=Path(os.getenv("TRANSIT_ROUTE_MAP", "route_map.csv")),
            vehicle_source=source if source in VEHICLE_SOURCES else "live",
        )