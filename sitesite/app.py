from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from pathlib import Path

import folium
import streamlit as st
from streamlit_folium import st_folium

from transit.colors import contrast_path_colors
from transit.config import Settings
from transit.domain import Vehicle
from transit.geometry import pad_bounds, view_for
from transit.gtfs import Feed, load_feed
from transit.mapview.layers import (
    add_hit_lines, add_route_lines, add_stops, add_trail, add_vehicles, build_base_map, hover_stops,
)
from transit.providers import ScheduleProvider, live_vehicles, trail_coords
from transit.ui import data, state
from transit.ui.devices import render_bus_devices, render_devices_view
from transit.ui.info_panel import (
    render_bus_history, render_live_bus, render_network, render_route_header, render_schedule_vehicle,
)
from transit.ui.line_tabs import render_bus_list, render_line_picker, render_line_tabs
from transit.ui.sidebar import render_sidebar
from transit.ui.users import render_users_view

st.set_page_config(page_title="Transit Admin", layout="wide")
SETTINGS = Settings.from_env()
MAP_WIDTH_PX = 850   # rough width of the map column, only used to pick a zoom that fits the line


@st.cache_resource(show_spinner="Loading GTFS...")
def get_feed(gtfs_dir: str, simplify_m: float) -> Feed:
    return load_feed(Path(gtfs_dir), simplify_m=simplify_m)


@st.cache_resource
def get_hover_payload(gtfs_dir: str, _feed: Feed) -> dict:
    return hover_stops(_feed)


# - Streamlit fades elements that are being re-run ("stale"). With live updates every second that makes the
#   map and the panel flicker, so the fade is turned off.
# - A table that scrolls keeps the mouse wheel: at its top or bottom the wheel no longer carries on to the page
#   (.dvn-scroller is the scroll area of st.dataframe). Tables that fit have nothing to scroll: the page scrolls.
PAGE_CSS = """<style>
[data-stale="true"] { opacity: 1 !important; transition: none !important; }
.stDataFrame .dvn-scroller { overscroll-behavior: contain; }
</style>"""


def main() -> None:
    st.html(PAGE_CSS)
    gtfs_key = str(SETTINGS.gtfs_dir)
    feed = get_feed(gtfs_key, SETTINGS.simplify_m)
    state.init_state(SETTINGS.vehicle_source)
    controls = render_sidebar(feed, SETTINGS)
    link, link_err = data.route_link(gtfs_key, feed)

    if controls.view == "users":
        render_users_view(SETTINGS.tz, live=controls.live_updates, list_every_s=SETTINGS.devices_list_refresh_s,
                          user_every_s=SETTINGS.users_refresh_s)
        return

    if controls.view == "devices":
        def line_of(api_rid: int | None) -> str:
            rid = link.gtfs_id(api_rid)
            return feed.routes[rid].short_name if rid in feed.routes else f"? (API route {api_rid})"
        render_devices_view(line_of, SETTINGS.tz, SETTINGS.device_online_s, live=controls.live_updates,
                            list_every_s=SETTINGS.devices_list_refresh_s, card_every_s=SETTINGS.live_refresh_s,
                            photo_every_s=SETTINGS.photo_refresh_s)
        return

    # live updates only with a line open: the network overview has nothing that moves
    live_part = controls.live_updates and state.selected_route_id()
    (_live_map_view if live_part else _map_view)(gtfs_key, feed, link, link_err, controls)


def _map_view(gtfs_key: str, feed: Feed, link, link_err: str | None, controls) -> None:
    """Map and side panel. With live updates on, this part alone re-runs every few seconds: the sidebar and
    the map's base layer stay as they are, only the buses, the trail and the panel are redrawn."""
    if not controls.simulated:
        controls = replace(controls, now=datetime.now(SETTINGS.tz))
    sel_id = state.selected_route_id()
    route = feed.routes.get(sel_id) if sel_id else None
    selected = state.selected_vehicle_id()
    live = controls.source == "live"

    # static: line paths and stops, part of the base map, sent to the browser once per line choice.
    # fg: what moves (buses, trail), the only layer re-sent on each live update.
    static = folium.FeatureGroup(name="lines")
    fg = folium.FeatureGroup(name="transit")
    vehicles: list[Vehicle] = []
    scheduled: list[Vehicle] = []
    positions = None
    notice = None
    history_fetched = (None, None)
    snapshot = (None, None)

    if route:
        add_route_lines(static, feed, [route.route_id], SETTINGS.line_weight_selected)
        add_stops(static, feed, route, SETTINGS.stop_radius)
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
                snapshot = data.bus_snapshot(int(selected), SETTINGS.history_limit,
                                             photo_every_s=SETTINGS.photo_refresh_s)
                snap, snap_err = snapshot
                history_fetched = (snap.history, snap.errors.get("history")) if snap else (None, snap_err)
                if history_fetched[0]:
                    add_trail(fg, trail_coords(history_fetched[0]), contrast_path_colors(route.color)[0])
        else:
            vehicles = scheduled
        add_vehicles(fg, route, vehicles, selected)
    else:
        ids = feed.drawable_route_ids()
        add_route_lines(static, feed, ids, SETTINGS.line_weight)
        add_hit_lines(static, feed, ids, SETTINGS.hit_px)

    area = SETTINGS.area_bounds or pad_bounds(feed.bounds(), SETTINGS.area_pad_km)
    base_map = build_base_map(SETTINGS, area, get_hover_payload(gtfs_key, feed))
    static.add_to(base_map)
    # zoom to the selected line; with none, zoom 0 is raised to the smallest zoom that fills the frame
    route_box = feed.route_bounds(route.route_id) if route else None
    if route_box:
        center, zoom = view_for(route_box, MAP_WIDTH_PX, SETTINGS.map_height, SETTINGS.max_zoom)
    else:
        (s_, w_), (n_, e_) = area
        center, zoom = ((s_ + n_) / 2, (w_ + e_) / 2), 0

    # left: the map with the line's reference tabs under it; right: the line, its buses, the picked bus
    col_map, col_side = st.columns([3, 2], gap="medium")
    with col_map:
        result = st_folium(
            base_map,
            feature_group_to_add=fg,
            key="map",
            height=SETTINGS.map_height,
            use_container_width=True,
            center=center,
            zoom=zoom,
            returned_objects=["last_active_drawing", "last_object_clicked_tooltip"],
        )

        if route:
            render_line_tabs(feed, route, link, live=live, positions=positions,
                             scheduled=scheduled, now=controls.now, tz=SETTINGS.tz)

    state.handle_map_result(result, sel_id, {v.vehicle_id for v in vehicles})

    with col_side:
        if route is None:
            buses, buses_err = data.buses()
            render_network(len(feed.routes), len(feed.services_on(controls.now.date())),
                           None if buses is None else len(buses), buses_err)
            render_line_picker(feed, link, buses)
        else:
            render_route_header(
                route,
                live_count=len(positions) if live and positions is not None else None,
                scheduled_count=len(scheduled),
                notice=notice,
            )
            render_bus_list(feed, route, live, vehicles, positions, selected)
            if not selected:
                st.caption("Click a bus in the list or on the map.")
            elif live:
                if selected.isdigit():
                    bid = int(selected)
                    st.subheader(f"Bus {bid}")
                    snap, snap_err = snapshot
                    info_fetched = (snap.info, None) if snap else (None, snap_err)
                    frame_fetched = (snap.frame, snap.errors.get("frame")) if snap else (None, snap_err)
                    devices_fetched = ((snap.devices, snap.errors), None) if snap else (None, snap_err)
                    t_overview, t_devices, t_history = st.tabs(["Overview", "Devices", "History"])
                    with t_overview:
                        render_live_bus(feed, route, bid, info_fetched, frame_fetched, SETTINGS.tz,
                                        photo_meta=snap.photo_meta if snap else None,
                                        history=snap.history if snap else None)
                    with t_devices:
                        render_bus_devices(devices_fetched, SETTINGS.tz, SETTINGS.device_online_s)
                    with t_history:
                        render_bus_history(history_fetched, SETTINGS.tz)
            else:
                v = next((x for x in vehicles if x.vehicle_id == selected), None)
                if v:
                    render_schedule_vehicle(v)
                else:
                    st.info("This trip is no longer running.")


_live_map_view = st.fragment(run_every=SETTINGS.live_refresh_s)(_map_view)


main()
