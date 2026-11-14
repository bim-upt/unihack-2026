"""Right-hand column: network summary, line header, selected bus."""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st

from ..domain import Route, Vehicle
from ..gtfs import Feed
from ..providers.live import nearest_stop


# ---------------------------------------------------------------- formatting
def fmt_age(seconds: float | None) -> str:
    if seconds is None:
        return "—"
    if seconds < 5:
        return "just now"
    if seconds < 60:
        return f"{seconds:.0f} s ago"
    if seconds < 3600:
        return f"{seconds / 60:.0f} min ago"
    return f"{seconds / 3600:.1f} h ago"


def fmt_uptime(ms: int | None) -> str:
    if ms is None:
        return "—"
    s = ms // 1000
    h, m = s // 3600, (s % 3600) // 60
    return f"{h} h {m:02d} min" if h else f"{m} min {s % 60:02d} s"


def fmt_time(dt: datetime | None, tz: ZoneInfo) -> str:
    return dt.astimezone(tz).strftime("%H:%M:%S") if dt else "—"


def kv_table(rows: dict) -> None:
    st.table(pd.DataFrame({"Value": [str(v) for v in rows.values()]}, index=list(rows.keys())))


# ---------------------------------------------------------------- panels
def render_network(n_routes: int, n_services: int, buses_fetched) -> None:
    st.subheader("Network")
    st.metric("Routes", n_routes)
    st.metric("Active services today", n_services)
    buses, err = buses_fetched
    if err:
        st.caption(f"API: {err}")
    elif buses is not None:
        st.metric("Buses on the server", len(buses))
    st.info("Click a line on the map (or use the sidebar).")


def render_route_header(route: Route, *, live_count: int | None, scheduled_count: int, notice: str | None) -> None:
    st.subheader(f"{route.mode_name} {route.short_name}")
    st.caption(route.long_name)
    if notice:
        st.warning(notice)
    c1, c2 = st.columns(2)
    c1.metric("Live buses", "—" if live_count is None else live_count)
    c2.metric("Scheduled now", scheduled_count)


def render_live_bus(feed: Feed, route: Route, bus_id: int, info_fetched, frame_fetched,
                    history_fetched, tz: ZoneInfo) -> None:
    info, err = info_fetched
    if err or info is None:
        st.error(f"Bus data unavailable: {err or 'no answer'}")
        return

    p = info.position
    if p is None:
        st.warning("The server has no position for this bus.")
    else:
        age = p.age_s()
        st.caption(" · ".join([
            "simulated" if p.sim else "real GPS",
            "GPS fix" if p.fix_ok is not False else "no GPS fix",
            f"updated {fmt_age(age)}" if age is not None else "device clock not set",
        ]))

    c1, c2 = st.columns(2)
    c1.metric("Speed", f"{p.speed_kmh:.0f} km/h" if p and p.speed_kmh is not None else "—")
    c2.metric("Passengers", "—" if info.occupancy is None else info.occupancy)

    if p and p.latlon:
        ns = nearest_stop(feed, route.route_id, *p.latlon)
        if ns:
            st.write(f"**Nearest stop:** {ns[0].name} ({ns[1]:.0f} m)")

    if p:
        kv_table({
            "Position": f"{p.lat:.6f}, {p.lon:.6f}" if p.latlon else "—",
            "Position time": fmt_time(p.time, tz),
            "API route": "—" if p.route_id is None else p.route_id,
            "Device": p.device_id or "—",
            "Boot ID": p.boot_id or "—",
            "Message seq": "—" if p.seq is None else p.seq,
            "Device uptime": fmt_uptime(p.uptime_ms),
        })

    st.markdown("**Camera**")
    if info.capture_interval_ms:
        st.caption(f"Capture interval: {info.capture_interval_ms / 1000:.1f} s")
    frame, ferr = frame_fetched
    if ferr:
        st.caption(f"Photo unavailable: {ferr}")
    elif frame is None:
        st.caption("No photo.")
    else:
        cap = []
        if frame.meta and frame.meta.captured_at:
            cap.append(fmt_time(frame.meta.captured_at, tz))
        if frame.meta and frame.meta.width and frame.meta.height:
            cap.append(f"{frame.meta.width}x{frame.meta.height}")
        st.image(frame.jpeg, caption=" · ".join(cap) or None)

    history, herr = history_fetched
    with st.expander(f"Recent positions ({len(history) if history else 0})"):
        if herr:
            st.caption(herr)
        elif not history:
            st.caption("No history.")
        else:
            rows = sorted(history, key=lambda t: (t.ts or 0, t.uptime_ms or 0), reverse=True)
            st.dataframe(pd.DataFrame([{
                "Time": fmt_time(t.time, tz),
                "Speed (km/h)": t.speed_kmh,
                "Lat": t.lat,
                "Lon": t.lon,
                "Fix": t.fix_ok,
                "Simulated": t.sim,
            } for t in rows]), hide_index=True, width="stretch")

    for part, e in info.errors.items():
        st.caption(f"{part}: {e}")


def render_schedule_vehicle(v: Vehicle) -> None:
    st.divider()
    st.subheader(f"Trip {v.trip_id}")
    kv_table({
        "Heading to": v.headsign or "—",
        "Previous stop": v.prev_stop or "—",
        "Next stop": v.next_stop or "—",
        "Scheduled speed": f"{v.speed_kmh:.1f} km/h" if v.speed_kmh is not None else "—",
    })
    st.caption("Position interpolated from the timetable.")