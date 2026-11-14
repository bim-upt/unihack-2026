from __future__ import annotations

from pathlib import Path

import folium
import streamlit as st
from streamlit_folium import st_folium

from transit.config import Settings
from transit.domain import Vehicle
from transit.geometry import pad_bounds
from transit.gtfs import Feed, load_feed
from transit.mapview.layers import (
    add_hit_lines, add_route_lines, add_stops, add_vehicles, build_base_map, hover_stops,
)
from transit.providers import DummyDetailsProvider, ScheduleProvider
from transit.ui import state
from transit.ui.info_panel import render_network, render_route
from transit.ui.sidebar import Controls, render_sidebar

st.set_page_config(page_title="Transit Admin", layout="wide")
SETTINGS = Settings.from_env()
DETAILS = DummyDetailsProvider()


@st.cache_resource(show_spinner="Loading GTFS...")
def get_feed(gtfs_dir: str, simplify_m: float) -> Feed:
    return load_feed(Path(gtfs_dir), simplify_m=simplify_m)


@st.cache_resource
def get_hover_payload(gtfs_dir: str, _feed: Feed) -> dict:
    return hover_stops(_feed)


@st.cache_resource
def get_rt_provider(gtfs_dir: str, url: str, _feed: Feed):
    from transit.providers.gtfs_rt import GtfsRtProvider
    return GtfsRtProvider(_feed, url, ttl_s=SETTINGS.gtfs_rt_ttl_s, timeout_s=SETTINGS.gtfs_rt_timeout_s)


def get_vehicles(feed: Feed, route_id: str, controls: Controls) -> tuple[list[Vehicle], str | None]:
    schedule = ScheduleProvider(feed)
    if controls.simulated or not SETTINGS.gtfs_rt_url:
        return schedule.vehicles(route_id, controls.now), None
    try:
        rt = get_rt_provider(str(SETTINGS.gtfs_dir), SETTINGS.gtfs_rt_url, feed)
        return rt.vehicles(route_id, controls.now), None
    except Exception as exc:  # network/parse/dependency errors -> degrade to schedule
        return schedule.vehicles(route_id, controls.now), f"Live feed unavailable ({exc}); showing schedule."


def main() -> None:
    gtfs_key = str(SETTINGS.gtfs_dir)
    feed = get_feed(gtfs_key, SETTINGS.simplify_m)
    state.init_state()
    controls = render_sidebar(feed, SETTINGS)

    sel_id = state.selected_route_id()
    route = feed.routes.get(sel_id) if sel_id else None

    fg = folium.FeatureGroup(name="transit")
    vehicles: list[Vehicle] = []
    notice = None
    if route:
        add_route_lines(fg, feed, [route.route_id], SETTINGS.line_weight_selected)
        add_stops(fg, feed, route, SETTINGS.stop_radius)
        vehicles, notice = get_vehicles(feed, route.route_id, controls)
        add_vehicles(fg, route, vehicles, state.selected_vehicle_id())
    else:
        ids = feed.drawable_route_ids()
        add_route_lines(fg, feed, ids, SETTINGS.line_weight)
        add_hit_lines(fg, feed, ids, SETTINGS.hit_px)

    area = SETTINGS.area_bounds or pad_bounds(feed.bounds(), SETTINGS.area_pad_km)
    base_map = build_base_map(SETTINGS, area, get_hover_payload(gtfs_key, feed))

    col_map, col_info = st.columns([3, 1])
    with col_map:
        result = st_folium(
            base_map,
            feature_group_to_add=fg,
            key="map",
            height=SETTINGS.map_height,
            use_container_width=True,
            returned_objects=["last_active_drawing", "last_object_clicked_tooltip"],
        )

    state.handle_map_result(result, sel_id, {v.vehicle_id for v in vehicles})

    with col_info:
        if route is None:
            render_network(len(feed.routes), len(feed.services_on(controls.now.date())))
        else:
            render_route(route, vehicles, state.selected_vehicle_id(), DETAILS, notice)


main()