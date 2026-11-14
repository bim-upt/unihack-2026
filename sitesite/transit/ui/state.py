from __future__ import annotations

import streamlit as st

from ..mapview.layers import parse_vehicle_tooltip

K_ROUTE = "route_id"
K_VEHICLE = "vehicle_id"
K_LAST_TIP = "last_vehicle_tip"
K_LAST_DRAWING = "last_drawing_rid"
K_BLOCK = "block_rid"           
K_PENDING = "pending_route"    


def init_state() -> None:
    ss = st.session_state
    for k, v in ((K_ROUTE, ""), (K_VEHICLE, None), (K_LAST_TIP, None),
                 (K_LAST_DRAWING, None), (K_BLOCK, None)):
        ss.setdefault(k, v)
    if K_PENDING in ss:
        ss[K_ROUTE] = ss.pop(K_PENDING)
        ss[K_VEHICLE] = None


def selected_route_id() -> str | None:
    return st.session_state[K_ROUTE] or None


def selected_vehicle_id() -> str | None:
    return st.session_state[K_VEHICLE]


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