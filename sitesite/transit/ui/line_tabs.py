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


def render_line_tabs(feed: Feed, route: Route, link: RouteLink, *, live: bool, vehicles: list[Vehicle],
                     positions: list[Position] | None, scheduled: list[Vehicle], now: datetime, tz: ZoneInfo) -> None:
    st.subheader(f"Line {route.short_name}")
    t_buses, t_reports, t_stops, t_line = st.tabs(["Buses", "Reports", "Stops", "Line"])
    with t_buses:
        _buses(feed, route, live, vehicles, positions)
    with t_reports:
        _reports(route, tz)
    with t_stops:
        _stops(feed, route)
    with t_line:
        _line(feed, route, link, live, positions, scheduled, now)


def _bus_table(rows: list[dict], ids: list[str]) -> None:
    st.session_state[state.K_BUS_TABLE_IDS] = ids
    if not rows:
        st.info("No buses.")
        return
    st.dataframe(
        pd.DataFrame(rows),
        key=state.K_BUS_TABLE,
        on_select=state.on_bus_table_select,
        selection_mode=["single-row", "single-cell"],   # a click on any cell picks its bus
        hide_index=True,
        width="stretch",
    )
    st.caption("Click a bus to show it.")


def _buses(feed: Feed, route: Route, live: bool, vehicles: list[Vehicle], positions: list[Position] | None) -> None:
    if not live:
        rows = [{
            "Trip": v.trip_id,
            "Heading to": v.headsign,
            "Previous stop": v.prev_stop,
            "Next stop": v.next_stop,
            "Speed (km/h)": v.speed_kmh,
        } for v in vehicles]
        _bus_table(rows, [v.vehicle_id for v in vehicles])
        return

    if positions is None:
        st.session_state[state.K_BUS_TABLE_IDS] = []
        st.info("No live data for this line (see the note beside the map).")
        return

    on_map = {v.vehicle_id for v in vehicles}
    ordered = sorted(positions, key=lambda p: p.bus_id)
    rows = []
    for p in ordered:
        ns = nearest_stop(feed, route.route_id, *p.latlon) if p.latlon else None
        age = p.age_s()
        rows.append({
            "Bus": p.bus_id,
            "Speed (km/h)": None if p.speed_kmh is None else round(p.speed_kmh),
            "GPS": "fix" if p.fix_ok is not False else "no fix",
            "Source": "simulated" if p.sim else "real",
            "Last update": fmt_age(age) if age is not None else "clock not set",
            "Nearest stop": ns[0].name if ns else "—",
            "On map": str(p.bus_id) in on_map,
        })
    _bus_table(rows, [str(p.bus_id) for p in ordered])


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