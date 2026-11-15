"""All session-state keys and transitions live here."""
from __future__ import annotations

import time as _time
from datetime import datetime, timedelta

import streamlit as st

from ..mapview.layers import parse_vehicle_tooltip
from ..providers.live import HeadingTracker

K_ROUTE = "route_id"
K_VEHICLE = "vehicle_id"
K_LAST_TIP = "last_vehicle_tip"
K_LAST_DRAWING = "last_drawing_rid"
K_BLOCK = "block_rid"           
K_PENDING = "pending_route"     
K_SOURCE = "vehicle_source"     
K_BUS_TABLE = "bus_table"       
K_BUS_TABLE_IDS = "bus_table_ids"
K_TRACKER = "heading_tracker"
K_LINE_TABLE = "line_table"
K_LINE_TABLE_IDS = "line_table_ids"
K_LIVE_UPDATES = "live_updates"
K_BUS_TAB = "bus_tab_choice"   # the bus panel's open tab, kept when another bus is picked
K_VIEW = "view"                 # "map" | "fleet" | "devices" | "users"
K_SIM_ON, K_SIM_HOUR, K_SIM_MIN, K_SIM_SET_AT = "sim_on", "sim_hour", "sim_min", "sim_set_at"   # "what if" time
K_PENDING_VIEW = "pending_view"          # set by a click elsewhere, applied on the next full run
K_PENDING_VEHICLE = "pending_vehicle"


def init_state(default_source: str = "live") -> None:
    ss = st.session_state
    for k, v in ((K_ROUTE, ""), (K_VEHICLE, None), (K_LAST_TIP, None), (K_LAST_DRAWING, None),
                 (K_BLOCK, None), (K_SOURCE, default_source), (K_BUS_TABLE_IDS, []), (K_VIEW, "map"), (K_LIVE_UPDATES, True)):
        ss.setdefault(k, v)
    if K_PENDING in ss:
        ss[K_ROUTE] = ss.pop(K_PENDING)
        ss[K_VEHICLE] = None
    if K_PENDING_VEHICLE in ss:
        ss[K_VEHICLE] = ss.pop(K_PENDING_VEHICLE)
    if K_PENDING_VIEW in ss:
        ss[K_VIEW] = ss.pop(K_PENDING_VIEW)


def open_bus_on_map(gtfs_route_id: str | None, bus_id: str | None) -> None:
    """From another page: show this line (and bus, when given) in the map view (applied on the next full run)."""
    ss = st.session_state
    if gtfs_route_id:
        ss[K_PENDING] = gtfs_route_id
    if bus_id:
        ss[K_PENDING_VEHICLE] = bus_id
    ss[K_PENDING_VIEW] = "map"


def selected_route_id() -> str | None:
    return st.session_state[K_ROUTE] or None


def selected_vehicle_id() -> str | None:
    return st.session_state[K_VEHICLE]


def view() -> str:
    return st.session_state[K_VIEW]


def vehicle_source() -> str:
    return st.session_state[K_SOURCE]


def heading_tracker() -> HeadingTracker:
    return st.session_state.setdefault(K_TRACKER, HeadingTracker())


# ---------------------------------------------------------------- callbacks
def show_all_routes() -> None:
    ss = st.session_state
    ss[K_ROUTE] = ""
    ss[K_VEHICLE] = None
    ss[K_BLOCK] = ss.get(K_LAST_DRAWING)


def on_route_change() -> None:
    ss = st.session_state
    ss[K_VEHICLE] = None
    if not ss[K_ROUTE]:
        ss[K_BLOCK] = ss.get(K_LAST_DRAWING)


def on_source_change() -> None:
    ss = st.session_state
    ss[K_VEHICLE] = None
    ss[K_LAST_TIP] = None


def on_bus_table_select() -> None:
    """Runs before the rerun, so the map already highlights the picked bus."""
    ss = st.session_state
    sel = (ss.get(K_BUS_TABLE) or {}).get("selection") or {}
    rows = sel.get("rows") or [c[0] for c in sel.get("cells") or []]   # a clicked cell picks its row
    ids = ss.get(K_BUS_TABLE_IDS) or []
    if rows and 0 <= rows[0] < len(ids):
        ss[K_VEHICLE] = ids[rows[0]]


def on_line_table_select() -> None:
    """A line picked in the side panel's list opens like a line clicked on the map."""
    ss = st.session_state
    sel = (ss.get(K_LINE_TABLE) or {}).get("selection") or {}
    rows = sel.get("rows") or [c[0] for c in sel.get("cells") or []]
    ids = ss.get(K_LINE_TABLE_IDS) or []
    if rows and 0 <= rows[0] < len(ids):
        ss[K_PENDING] = ids[rows[0]]   # applied by init_state on the full rerun (the sidebar shows the line)


def remember_bus_tab(tab_key: str) -> None:
    label = st.session_state.get(tab_key)
    if label:
        st.session_state[K_BUS_TAB] = label


# ---------------------------------------------------------------- map clicks
def handle_map_result(result: dict | None, selected_route: str | None, vehicle_ids: set[str]) -> None:
    if not result:
        return
    ss = st.session_state

    drawing = result.get("last_active_drawing") or {}
    rid = (drawing.get("properties") or {}).get("route_id")
    if rid:
        ss[K_LAST_DRAWING] = rid
        if ss[K_BLOCK] and rid != ss[K_BLOCK]:
            ss[K_BLOCK] = None
        if not selected_route and rid != ss[K_BLOCK]:
            ss[K_PENDING] = rid
            st.rerun()

    tip = result.get("last_object_clicked_tooltip")
    vid = parse_vehicle_tooltip(tip)
    if selected_route and vid in vehicle_ids and tip != ss[K_LAST_TIP]:
        ss[K_LAST_TIP] = tip
        ss[K_VEHICLE] = vid
        st.rerun()

# ---------------------------------------------------------------- simulated time (timetable mode)
def start_sim_clock() -> None:
    """The simulated clock starts at the chosen time now and then runs on like a normal clock."""
    st.session_state[K_SIM_SET_AT] = _time.time()


def sim_now(tz) -> datetime:
    """The chosen time, plus how long ago it was chosen (so the timetable buses keep moving)."""
    ss = st.session_state
    chosen = datetime.now(tz).replace(hour=int(ss[K_SIM_HOUR]), minute=int(ss[K_SIM_MIN]), second=0, microsecond=0)
    return chosen + timedelta(seconds=_time.time() - ss.get(K_SIM_SET_AT, _time.time()))
