"""App configuration. Defaults can be overridden with environment variables."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo

MODE_NAMES = {"0": "Tram", "3": "Bus", "11": "Trolleybus"}


@dataclass(frozen=True)
class Settings:
    gtfs_dir: Path = Path("gtfs")
    tz: ZoneInfo = ZoneInfo("Europe/Bucharest")
    map_center: tuple[float, float] = (45.7489, 21.2087)
    zoom_start: int = 12
    map_height: int = 680
    tiles: str = "OpenStreetMap"
    tile_filter: str = "grayscale(1) contrast(0.8) brightness(1.12)"
    stop_radius: int = 8
    hit_px: int = 22
    line_weight: int = 4
    line_weight_selected: int = 7

    # need gtfs  url, none = static
    gtfs_rt_url: str | None = None
    gtfs_rt_ttl_s: float = 10.0
    gtfs_rt_timeout_s: float = 5.0

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            gtfs_dir=Path(os.getenv("TRANSIT_GTFS_DIR", "gtfs")),
            gtfs_rt_url=os.getenv("TRANSIT_GTFS_RT_URL") or None,
        )