from __future__ import annotations

from typing import Iterable

import folium

from ..colors import contrast_path_colors
from ..config import Bounds, Settings
from ..domain import Route, Vehicle
from ..gtfs import Feed
from .area import LockToArea
from .hover import HoverInfo
from .icons import ICON_PX, vehicle_html

VEH_CLASS, VEH_ID_PREFIX = "veh", "vid-"
PATH_CLASS, PATH_ID_PREFIX = "vpath", "vp-"
TOOLTIP_SEP = "·"


# -------------------------------- tooltip contract
def vehicle_tooltip(route: Route, v: Vehicle) -> str:
    return f"{route.short_name} {TOOLTIP_SEP} {v.vehicle_id}"


def parse_vehicle_tooltip(tip: str | None) -> str | None:
    if not tip or TOOLTIP_SEP not in tip:
        return None
    return tip.split(TOOLTIP_SEP, 1)[1].strip()


# ----------------------------------- routes
def _route_feature(route: Route, coords: list, hit: bool) -> dict:
    return {
        "type": "Feature",
        "properties": {"route_id": route.route_id, "short": route.short_name,
                       "color": route.color, "tcolor": route.text_color, "hit": hit},
        "geometry": {"type": "LineString", "coordinates": coords},
    }


def _add_lines(fg, feed: Feed, route_ids: Iterable[str], weight: int, opacity: float, hit: bool) -> None:
    for rid in route_ids:
        route = feed.routes[rid]
        for shape_id in feed.shapes_by_route.get(rid, ()):
            folium.GeoJson(
                _route_feature(route, feed.shapes[shape_id].lonlat, hit),
                style_function=lambda _f, c=route.color, w=weight, o=opacity: {
                    "color": c, "weight": w, "opacity": o},
            ).add_to(fg)


def add_route_lines(fg, feed: Feed, route_ids: Iterable[str], weight: int) -> None:
    _add_lines(fg, feed, route_ids, weight, 0.95, hit=False)


def add_hit_lines(fg, feed: Feed, route_ids: Iterable[str], hit_px: int) -> None:
    """Wide, near-invisible lines used by hover.js for hit-testing."""
    _add_lines(fg, feed, route_ids, hit_px, 0.01, hit=True)


# ------------------------------- stops / vehicles
def add_stops(fg, feed: Feed, route: Route, radius: int) -> None:
    for sid in feed.stops_by_route.get(route.route_id, ()):
        s = feed.stops[sid]
        folium.CircleMarker(
            (s.lat, s.lon), radius=radius, color="#ffffff", weight=2,
            fill=True, fill_color=route.color, fill_opacity=1, tooltip=s.name,
        ).add_to(fg)


def add_trail(fg, coords: list[list[float]], color: str) -> None:
    """Recent track of the selected bus, oldest to newest."""
    if len(coords) >= 2:
        folium.PolyLine(coords, color=color, weight=4, opacity=0.85, dash_array="6 8").add_to(fg)


def add_vehicles(fg, route: Route, vehicles: list[Vehicle], selected_id: str | None) -> None:
    path_color, casing = contrast_path_colors(route.color)
    for v in vehicles:
        if len(v.path) < 2:
            continue
        cls = f"{PATH_CLASS} {PATH_ID_PREFIX}{v.css_id}" + (" sel" if v.vehicle_id == selected_id else "")
        folium.PolyLine(v.path, color=casing, weight=13, opacity=1, class_name=cls).add_to(fg)
        folium.PolyLine(v.path, color=path_color, weight=8, opacity=1, class_name=cls).add_to(fg)
    for v in vehicles:
        folium.Marker(
            (v.lat, v.lon),
            icon=folium.DivIcon(
                html=vehicle_html(route, v.heading, v.vehicle_id == selected_id),
                icon_size=(ICON_PX, ICON_PX),
                icon_anchor=(ICON_PX // 2, ICON_PX // 2),
                class_name=f"{VEH_CLASS} {VEH_ID_PREFIX}{v.css_id}",
            ),
            tooltip=vehicle_tooltip(route, v),
        ).add_to(fg)


# ------------------- base map
def hover_stops(feed: Feed) -> dict[str, list[list[float]]]:
    return {
        rid: [[round(feed.stops[s].lat, 5), round(feed.stops[s].lon, 5)] for s in sids]
        for rid, sids in feed.stops_by_route.items()
    }


def _tile_options(settings: Settings, box: list) -> dict:
    """Leaflet option names in camelCase so they apply regardless of folium version."""
    opts = {
        "noWrap": True,
        "bounds": box,
        "updateWhenIdle": True,
        "updateWhenZooming": False,
        "keepBuffer": 4,
    }
    if settings.tile_native_zoom is not None:
        opts["minNativeZoom"] = settings.tile_native_zoom
    return opts


def build_base_map(settings: Settings, area: Bounds, stops_payload: dict) -> folium.Map:
    (s, w), (n, e) = area
    box = [[s, w], [n, e]]
    m = folium.Map(
        location=((s + n) / 2, (w + e) / 2),
        zoom_start=12,
        tiles=None,
        max_bounds=True, min_lat=s, max_lat=n, min_lon=w, max_lon=e,
        max_bounds_viscosity=1.0,
    )
    folium.TileLayer(
        settings.tiles,
        max_zoom=settings.max_zoom,
        max_native_zoom=settings.tile_native_zoom or settings.max_zoom,
        control=False,
        **_tile_options(settings, box),
    ).add_to(m)
    m.fit_bounds(box)
    LockToArea(area).add_to(m)
    HoverInfo(stops_payload, settings.tile_filter).add_to(m)
    return m