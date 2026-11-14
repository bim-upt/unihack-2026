from __future__ import annotations

from pathlib import Path

import folium
import streamlit as st
from streamlit_folium import st_folium

from transit.colors import contrast_path_colors
from transit.config import Settings
from transit.domain import Vehicle
from transit.geometry import pad_bounds
from transit.gtfs import Feed, load_feed
from transit.mapview.layers import (
    add_hit_lines, add_route_lines, add_stops, add_trail, add_vehicles, build_base_map, hover_stops,
)
from transit.providers import ScheduleProvider, live_vehicles, trail_coords
from transit.ui import data, state
from transit.ui.devices import render_bus_devices, render_devices_view
from transit.ui.info_panel import render_live_bus, render_network, render_route_header, render_schedule_vehicle
from transit.ui.line_tabs import render_line_tabs
from transit.ui.sidebar import render_sidebar

st.set_page_config(page_title="Transit Admin", layout="wide")
SETTINGS = Settings.from_env()


@st.cache_resource(show_spinner="Loading GTFS...")
def get_feed(gtfs_dir: str, simplify_m: float) -> Feed:
    return load_feed(Path(gtfs_dir), simplify_m=simplify_m)


@st.cache_resource
def get_hover_payload(gtfs_dir: str, _feed: Feed) -> dict:
    return hover_stops(_feed)


def main() -> None:
    gtfs_key = str(SETTINGS.gtfs_dir)
    feed = get_feed(gtfs_key, SETTINGS.simplify_m)
    state.init_state(SETTINGS.vehicle_source)
    controls = render_sidebar(feed, SETTINGS)
    link, link_err = data.route_link(gtfs_key, feed)

    if controls.view == "devices":
        def line_of(api_rid: int | None) -> str:
            rid = link.gtfs_id(api_rid)
            return feed.routes[rid].short_name if rid in feed.routes else f"? (API route {api_rid})"
        render_devices_view(line_of, SETTINGS.tz, SETTINGS.device_online_s)
        return

    sel_id = state.selected_route_id()
    route = feed.routes.get(sel_id) if sel_id else None
    selected = state.selected_vehicle_id()
    live = controls.source == "live"

    fg = folium.FeatureGroup(name="transit")
    vehicles: list[Vehicle] = []
    scheduled: list[Vehicle] = []
    positions = None
    notice = None
    history_fetched = (None, None)
    snapshot = (None, None)

    if route:
        add_route_lines(fg, feed, [route.route_id], SETTINGS.line_weight_selected)
        add_stops(fg, feed, route, SETTINGS.stop_radius)
        scheduled = ScheduleProvider(feed).vehicles(route.route_id, controls.now)
        if live:
            api_rid = link.api_id(route.route_id)
            if link_err:
                notice = f"Could not match the server's routes to the GTFS lines: {link_err}"
            elif api_rid is None:
                notice = "No bus on the server drives this line."
            else:
                positions, err = data.route_positions(api_rid)
                if err:
                    notice = f"Live positions unavailable: {err}"
                elif positions is None:
                    notice = "The live positions endpoint is disabled."
                else:
                    vehicles = live_vehicles(positions, route, feed, state.heading_tracker(), SETTINGS.live_max_age_s)
            if selected and selected.isdigit():
                snapshot = data.bus_snapshot(int(selected), SETTINGS.history_limit)
                snap, snap_err = snapshot
                history_fetched = (snap.history, snap.errors.get("history")) if snap else (None, snap_err)
                if history_fetched[0]:
                    add_trail(fg, trail_coords(history_fetched[0]), contrast_path_colors(route.color)[0])
        else:
            vehicles = scheduled
        add_vehicles(fg, route, vehicles, selected)
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
            render_network(len(feed.routes), len(feed.services_on(controls.now.date())), data.buses())
        else:
            render_route_header(
                route,
                live_count=len(positions) if live and positions is not None else None,
                scheduled_count=len(scheduled),
                notice=notice,
            )
            if not selected:
                st.info("Click a bus on the map or in the Buses tab below.")
            elif live:
                if selected.isdigit():
                    bid = int(selected)
                    st.divider()
                    st.subheader(f"Bus {bid}")
                    t_overview, t_devices = st.tabs(["Overview", "Devices"])
                    snap, snap_err = snapshot
                    info_fetched = (snap.info, None) if snap else (None, snap_err)
                    frame_fetched = (snap.frame, snap.errors.get("frame")) if snap else (None, snap_err)
                    devices_fetched = ((snap.devices, snap.errors), None) if snap else (None, snap_err)
                    with t_overview:
                        render_live_bus(feed, route, bid, info_fetched, frame_fetched, history_fetched, SETTINGS.tz)
                    with t_devices:
                        render_bus_devices(devices_fetched, SETTINGS.tz, SETTINGS.device_online_s)
            else:
                v = next((x for x in vehicles if x.vehicle_id == selected), None)
                if v:
                    render_schedule_vehicle(v)
                else:
                    st.info("This trip is no longer running.")

    if route:
        render_line_tabs(feed, route, link, live=live, vehicles=vehicles, positions=positions,
                         scheduled=scheduled, now=controls.now, tz=SETTINGS.tz)


main()