"""All session-state keys and transitions live here."""
from __future__ import annotations

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
K_VIEW = "view"                 # "map" | "devices"


def init_state(default_source: str = "live") -> None:
    ss = st.session_state
    for k, v in ((K_ROUTE, ""), (K_VEHICLE, None), (K_LAST_TIP, None), (K_LAST_DRAWING, None),
                 (K_BLOCK, None), (K_SOURCE, default_source), (K_BUS_TABLE_IDS, []), (K_VIEW, "map")):
        ss.setdefault(k, v)
    if K_PENDING in ss:
        ss[K_ROUTE] = ss.pop(K_PENDING)
        ss[K_VEHICLE] = None


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
        ss[K_ROUTE] = ids[rows[0]]
        ss[K_VEHICLE] = None


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