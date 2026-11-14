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
    history_limit: int = 50                         
    # positions older than this are hidden from the map; None = show the last position however old
    # (test data comes in bursts, so hiding it would leave the map empty)
    live_max_age_s: float | None = None
    # a device counts as online when the server heard from it within this many seconds
    device_online_s: float = 300.0
    # live updates: how often the map and the side panel fetch new positions (the photo follows the camera)
    live_refresh_s: float = 1.0
    photo_refresh_s: float = 5.0          # photo refresh when the server reports no capture interval
    devices_list_refresh_s: float = 10.0  # the Devices page list (the selected device follows live_refresh_s)
    users_refresh_s: float = 5.0          # the Users page: the selected user (the list follows devices_list_refresh_s)

    @classmethod
    def from_env(cls) -> "Settings":
        source = os.getenv("TRANSIT_VEHICLE_SOURCE", "live")
        return cls(
            gtfs_dir=Path(os.getenv("TRANSIT_GTFS_DIR", "gtfs")),
            vehicle_source=source if source in VEHICLE_SOURCES else "live",
            live_max_age_s=float(os.environ["TRANSIT_LIVE_MAX_AGE_S"]) if os.getenv("TRANSIT_LIVE_MAX_AGE_S") else None,
            device_online_s=float(os.getenv("TRANSIT_DEVICE_ONLINE_S") or 300),
            live_refresh_s=float(os.getenv("TRANSIT_LIVE_REFRESH_S") or 1),
            photo_refresh_s=float(os.getenv("TRANSIT_PHOTO_REFRESH_S") or 5),
            devices_list_refresh_s=float(os.getenv("TRANSIT_DEVICES_LIST_REFRESH_S") or 10),
            users_refresh_s=float(os.getenv("TRANSIT_USERS_REFRESH_S") or 5),
        )