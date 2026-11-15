"""Tabbed view under the map for the selected line."""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st

from ..api.models import Position
from ..domain import Route, Vehicle
from ..gtfs import Feed
from ..linking import RouteLink
from ..providers.live import nearest_stop
from . import data, state
from .info_panel import fmt_age, fmt_time, kv_table


def render_line_tabs(feed: Feed, route: Route, link: RouteLink, *, live: bool, positions: list[Position] | None,
                     scheduled: list[Vehicle], now: datetime, tz: ZoneInfo) -> None:
    """Reference info about the line, under the map."""
    t_reports, t_stops, t_line = st.tabs(["Reports", "Stops", "Line"])
    with t_reports:
        _reports(route, tz)
    with t_stops:
        _stops(feed, route)
    with t_line:
        _line(feed, route, link, live, positions, scheduled, now)


def _bus_table(rows: list[dict], ids: list[str], selected: str | None) -> None:
    st.session_state[state.K_BUS_TABLE_IDS] = ids
    if not rows:
        st.info("No buses on this line right now.")
        return
    # the selected bus is marked so the list and the details below it read as one
    for row, vid in zip(rows, ids):
        row[" "] = "▶" if vid == selected else ""
    df = pd.DataFrame(rows)
    st.dataframe(
        df[[" "] + [c for c in df.columns if c != " "]],
        key=state.K_BUS_TABLE,
        on_select=state.on_bus_table_select,
        selection_mode=["single-row", "single-cell"],   # a click on any cell picks its bus
        hide_index=True,
        width="stretch",
        height=min(36 * (len(rows) + 1) + 3, 220),
        column_config={" ": st.column_config.TextColumn(width=24)},
    )


def render_bus_list(feed: Feed, route: Route, live: bool, vehicles: list[Vehicle],
                    positions: list[Position] | None, selected: str | None) -> None:
    """The buses of the line, in the side panel. Clicking one selects it (same as clicking it on the map)."""
    if not live:
        _bus_table([{
            "Trip": v.trip_id,
            "To": v.headsign,
            "Next stop": v.next_stop,
        } for v in vehicles], [v.vehicle_id for v in vehicles], selected)
        return

    if positions is None:
        st.session_state[state.K_BUS_TABLE_IDS] = []
        return

    ordered = sorted(positions, key=lambda p: p.bus_id)
    rows = []
    for p in ordered:
        ns = nearest_stop(feed, route.route_id, *p.latlon) if p.latlon else None
        age = p.age_s()
        rows.append({
            "Bus": p.bus_id,
            "km/h": None if p.speed_kmh is None else round(p.speed_kmh),
            "Updated": fmt_age(age) if age is not None else "clock not set",
            "Near": ns[0].name if ns else "—",
        })
    _bus_table(rows, [str(p.bus_id) for p in ordered], selected)


def render_line_picker(feed: Feed, link: RouteLink, buses) -> None:
    """With no line selected: the lines that have buses on the server. Clicking one opens it."""
    counts: dict[str, int] = {}
    for b in buses or []:
        rid = link.gtfs_id(b.route_id)
        if rid in feed.routes:
            counts[rid] = counts.get(rid, 0) + 1
    if not counts:
        st.caption("No line has buses on the server.")
        return
    ids = [r for r in feed.sorted_route_ids() if r in counts]
    st.session_state[state.K_LINE_TABLE_IDS] = ids
    st.markdown("**Lines with buses**")
    st.dataframe(
        pd.DataFrame([{
            "Line": feed.routes[r].short_name,
            "Mode": feed.routes[r].mode_name,
            "Buses": counts[r],
            "Name": feed.routes[r].long_name,
        } for r in ids]),
        key=state.K_LINE_TABLE,
        on_select=state.on_line_table_select,
        selection_mode=["single-row", "single-cell"],
        hide_index=True,
        width="stretch",
    )
    st.caption("Click a line (here or on the map) to open it.")
    if state.K_PENDING in st.session_state:
        st.rerun()   # whole app, not just the live part: the sidebar's Route box must change too


def _reports(route: Route, tz: ZoneInfo) -> None:
    items, err = data.reports(route.short_name)
    if err:
        st.error(f"Reports unavailable: {err}")
    elif items is None:
        st.info("The reports endpoint is disabled.")
    elif not items:
        st.info("No active reports on this line.")
    else:
        st.dataframe(pd.DataFrame([{
            "Kind": r.kind,
            "Stop": r.stop_name or "—",
            "Confirmations": r.confirmations,
            "Reported": fmt_time(r.created_at, tz),
            "Expires": fmt_time(r.expires_at, tz),
        } for r in items]), hide_index=True, width="stretch")


def _stops(feed: Feed, route: Route) -> None:
    sids = feed.stops_by_route.get(route.route_id, [])
    if not sids:
        st.info("No stops in the timetable for this line.")
        return
    st.dataframe(pd.DataFrame([{
        "#": i + 1,
        "Stop": feed.stops[s].name,
        "Stop ID": s,
        "Lat": round(feed.stops[s].lat, 6),
        "Lon": round(feed.stops[s].lon, 6),
    } for i, s in enumerate(sids)]), hide_index=True, width="stretch")
    st.caption("Order of first appearance in the timetable's trips.")


def _line(feed: Feed, route: Route, link: RouteLink, live: bool, positions: list[Position] | None,
          scheduled: list[Vehicle], now: datetime) -> None:
    services = feed.services_on(now.date())
    trip_ids = feed.trips_by_route.get(route.route_id, [])
    api_rid = link.api_id(route.route_id)
    kv_table({
        "Line": route.short_name,
        "Name": route.long_name or "—",
        "Mode": route.mode_name,
        "GTFS route_id": route.route_id,
        "API route_id": api_rid if api_rid is not None else "no bus on the server drives this line",
        "Color": route.color,
        "Stops": len(feed.stops_by_route.get(route.route_id, [])),
        "Shapes": len(feed.shapes_by_route.get(route.route_id, [])),
        "Trips today (timetable)": sum(1 for t in trip_ids if feed.trips[t].service_id in services),
        "Running now (timetable)": len(scheduled),
        "Live buses (API)": len(positions) if live and positions is not None else "—",
    })