from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from pathlib import Path

import folium
import streamlit as st
from streamlit_folium import st_folium

from transit.api.errors import CONNECTION_LOST
from transit.colors import contrast_path_colors
from transit.config import Settings
from transit.domain import Vehicle
from transit.geometry import pad_bounds
from transit.gtfs import Feed, load_feed
from transit.nextstop import next_stop
from transit.mapview.layers import (
    add_hit_lines, add_next_stop, add_route_lines, add_stops, add_trail, add_vehicles, build_base_map, hover_stops,
)
from transit.providers import ScheduleProvider, live_vehicles, trail_coords
from transit.ui import data, state
from transit.ui.bus_sightings import render_bus_sightings
from transit.ui.devices import render_bus_devices, render_devices_view
from transit.ui.info_panel import (
    render_bus_history, render_live_bus, render_network, render_route_header, render_schedule_vehicle,
)
from transit.ui.line_tabs import render_bus_list, render_line_picker, render_line_tabs
from transit.ui import nav
from transit.ui.sidebar import VIEW_LABELS, render_sidebar
from transit.ui.users import render_users_view
from transit.ui.fleet import render_fleet_view
from transit.ui.money import render_money_view
from transit.ui.health import render_health_view
from transit.ui.sightings import render_sightings_view
from transit.ui.inspector import render_inspector_view

st.set_page_config(page_title="Transit Admin", layout="wide", initial_sidebar_state="expanded")
SETTINGS = Settings.from_env()


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
/* hide Streamlit's own chrome: the coloured line on top, the menu, Deploy, and the "running" figure that would
   blink on every live update. The sidebar's open/close arrow stays. */
[data-testid="stDecoration"], [data-testid="stMainMenu"], #MainMenu, [data-testid="stAppDeployButton"],
[data-testid="stStatusWidget"], [data-testid="stToolbarActions"] { display: none !important; }
header[data-testid="stHeader"] { background: transparent; }
/* dropdowns show up to 70% of the window before scrolling (Streamlit's own limit is 300 px: 7 items), so the
   View list never scrolls */
[data-testid="stSelectboxVirtualDropdown"] { max-height: 70vh !important; }
/* the invisible Back / Forward helper takes no room */
.st-key-nav_listener { position: absolute !important; height: 0 !important; overflow: hidden; margin: 0; }
</style>"""


def main() -> None:
    st.html(PAGE_CSS)
    gtfs_key = str(SETTINGS.gtfs_dir)
    feed = get_feed(gtfs_key, SETTINGS.simplify_m)
    state.init_state(SETTINGS.vehicle_source)
    nav.sync(VIEW_LABELS, feed.routes)   # browser Back / Forward, refresh, links: the place lives in the URL
    previous_page = st.session_state.get("_page_shown")
    st.session_state["_page_shown"] = st.session_state.get(state.K_VIEW)
    if st.session_state["_page_shown"] == "map" and previous_page != "map":
        st.session_state["map_entering"] = True   # the map is drawn anew: start it where the user left it
    controls = render_sidebar(feed, SETTINGS)
    if not data.server_up_now():
        data.render_connection_lost()   # one banner, /health every second, no other calls until it's back
        return
    link, link_err = data.route_link(gtfs_key, feed)
    if link_err == CONNECTION_LOST:   # the first call of a page load found the server down: banner straight away
        data.render_connection_lost()
        return

    if controls.view == "inspector":
        def inspector_line(api_rid: int | None) -> str:
            rid = link.gtfs_id(api_rid)
            return feed.routes[rid].short_name if rid in feed.routes else f"? (API route {api_rid})"
        render_inspector_view(SETTINGS, inspector_line, live=controls.live_updates)
        return

    if controls.view == "sightings":
        def sightings_line(api_rid: int | None) -> str:
            rid = link.gtfs_id(api_rid)
            return feed.routes[rid].short_name if rid in feed.routes else f"? (API route {api_rid})"
        area = SETTINGS.area_bounds or pad_bounds(feed.bounds(), SETTINGS.area_pad_km)
        render_sightings_view(SETTINGS, area, sightings_line, feed, get_hover_payload(gtfs_key, feed),
                              live=controls.live_updates)
        return

    if controls.view == "health":
        def health_gtfs_of(api_rid: int | None) -> str | None:
            rid = link.gtfs_id(api_rid)
            return rid if rid in feed.routes else None
        area = SETTINGS.area_bounds or pad_bounds(feed.bounds(), SETTINGS.area_pad_km)
        render_health_view(feed, SETTINGS, area, health_gtfs_of, live=controls.live_updates)
        return

    if controls.view == "money":
        render_money_view(SETTINGS.tz, live=controls.live_updates, every_s=SETTINGS.money_refresh_s)
        return

    if controls.view == "fleet":
        def gtfs_of(api_rid: int | None) -> str | None:
            rid = link.gtfs_id(api_rid)
            return rid if rid in feed.routes else None

        def fleet_line(api_rid: int | None) -> str:
            rid = gtfs_of(api_rid)
            return feed.routes[rid].short_name if rid else f"? (API route {api_rid})"
        area = SETTINGS.area_bounds or pad_bounds(feed.bounds(), SETTINGS.area_pad_km)
        render_fleet_view(SETTINGS, area, fleet_line, gtfs_of, live=controls.live_updates)
        return

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


def _note_map_view(result: dict | None) -> None:
    """Where the user has the map (center, zoom), as the map reports it."""
    c, z = (result or {}).get("center"), (result or {}).get("zoom")
    if c and z:
        st.session_state["map_view_now"] = ((c["lat"], c["lng"]), z)


def _map_start_view(base_key: str):
    """The view the map is drawn with. The map is rebuilt when its base changes (another line) or when the page is
    entered; it then starts where the user was (no reset, no jump to the line). In between it's left alone: the
    same start view keeps the base unchanged, so panning never rebuilds it. A fresh page: the whole area."""
    ss = st.session_state
    if ss.pop("map_entering", False) or ss.get("map_base_key") != base_key:
        ss["map_base_key"] = base_key
        ss["map_base_view"] = ss.get("map_view_now")
    return ss.get("map_base_view")


def _map_view(gtfs_key: str, feed: Feed, link, link_err: str | None, controls) -> None:
    """Map and side panel. With live updates on, this part alone re-runs every few seconds: the sidebar and
    the map's base layer stay as they are, only the buses, the trail and the panel are redrawn."""
    data.require_server()
    # every update: the real time, or the simulated clock (set in the sidebar, running on from there)
    controls = replace(controls, now=state.sim_now(SETTINGS.tz) if controls.simulated else datetime.now(SETTINGS.tz))
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
    upcoming = None   # the selected live bus's next stop

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
                    path_color = contrast_path_colors(route.color)[0]
                    add_trail(fg, trail_coords(history_fetched[0], SETTINGS.trail_s), path_color)
                    upcoming = next_stop(feed, route.route_id, history_fetched[0])
                    if upcoming:
                        add_next_stop(fg, upcoming.path, upcoming.stop.name, (upcoming.stop.lat, upcoming.stop.lon),
                                      path_color, route.color)
        else:
            vehicles = scheduled
        add_vehicles(fg, route, vehicles, selected)
    else:
        ids = feed.drawable_route_ids()
        add_route_lines(static, feed, ids, SETTINGS.line_weight)
        add_hit_lines(static, feed, ids, SETTINGS.hit_px)

    area = SETTINGS.area_bounds or pad_bounds(feed.bounds(), SETTINGS.area_pad_km)
    # hover stop data: with a line open only that line's stops (the map is re-sent on every live update)
    hover = get_hover_payload(gtfs_key, feed)
    # with a line open the map opens fitted to it (in the browser, at the map's real width)
    base_map = build_base_map(SETTINGS, area, {route.route_id: hover.get(route.route_id, [])} if route else hover,
                              view=_map_start_view(route.route_id if route else ""))
    static.add_to(base_map)
    # left: the map with the line's reference tabs under it; right: the line, its buses, the picked bus
    col_map, col_side = st.columns([3, 2], gap="medium")
    with col_map:
        result = st_folium(
            base_map,
            feature_group_to_add=fg,
            key="map",
            height=SETTINGS.map_height,
            use_container_width=True,
            returned_objects=["last_active_drawing", "last_object_clicked_tooltip", "center", "zoom"],
        )

        if route:
            render_line_tabs(feed, route, link, live=live, positions=positions,
                             scheduled=scheduled, now=controls.now, tz=SETTINGS.tz)

    _note_map_view(result)
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
                tz=SETTINGS.tz,
            )
            render_bus_list(feed, route, live, vehicles, positions, selected)
            if not selected:
                st.caption("Click a bus in the list or on the map.")
            elif live:
                if selected.isdigit():
                    bid = int(selected)
                    st.subheader(f"Bus {bid}")
                    snap, snap_err = snapshot
                    if snap_err or (snap and snap.errors.get("position") == CONNECTION_LOST):
                        st.warning(CONNECTION_LOST)   # one note, not one per part of the panel
                        return
                    info_fetched = (snap.info, None) if snap else (None, snap_err)
                    frame_fetched = (snap.frame, snap.errors.get("frame")) if snap else (None, snap_err)
                    devices_fetched = ((snap.devices, snap.errors), None) if snap else (None, snap_err)
                    # the tab open for the last bus stays open for the next one
                    tab_names = ["Overview", "Devices", "History", "Sightings"]
                    remembered = st.session_state.get(state.K_BUS_TAB, "Overview")
                    t_overview, t_devices, t_history, t_sightings = st.tabs(
                        tab_names, key=f"bus_tabs_{bid}", default=remembered if remembered in tab_names else None,
                        on_change=state.remember_bus_tab, args=(f"bus_tabs_{bid}",))
                    with t_overview:
                        render_live_bus(feed, route, bid, info_fetched, frame_fetched, SETTINGS.tz, upcoming=upcoming,
                                        headcount=snap.headcount if snap else None,
                                        photo_meta=snap.photo_meta if snap else None,
                                        history=snap.history if snap else None)
                    with t_devices:
                        render_bus_devices(devices_fetched, SETTINGS.tz, SETTINGS.device_online_s)
                    with t_history:
                        render_bus_history(history_fetched, SETTINGS.tz)
                    with t_sightings:
                        render_bus_sightings(bid, SETTINGS.tz)
            else:
                v = next((x for x in vehicles if x.vehicle_id == selected), None)
                if v:
                    render_schedule_vehicle(v)
                else:
                    st.info("This trip is no longer running.")


_live_map_view = st.fragment(run_every=SETTINGS.live_refresh_s)(_map_view)


main()
