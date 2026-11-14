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


# ---------------------------------------------------------------- photo
PHOTO_POSITION_MAX_S = 15   # a beacon position further than this from the photo in time is not used


def photo_position(photo_meta, history) -> tuple[float, float] | None:
    """Where the bus was when the photo was taken: the beacon position nearest in time. Photos carry no
    location, so this is the best there is. Both sides use the server's receive time when they have it
    (same clock), else the device clocks."""
    if photo_meta is None or not history:
        return None
    use_server = photo_meta.received_at is not None
    taken = photo_meta.received_at if use_server else photo_meta.captured_at
    if taken is None:
        return None
    best, best_dt = None, None
    for t in history:
        when = t.received_at if use_server else t.time
        if when is None or t.latlon is None:
            continue
        dt = abs((when - taken).total_seconds())
        if best_dt is None or dt < best_dt:
            best, best_dt = t, dt
    return best.latlon if best is not None and best_dt <= PHOTO_POSITION_MAX_S else None


def photo_caption(photo_meta, history, tz: ZoneInfo) -> str:
    if photo_meta is None:
        return "Capture time and place unknown."
    when = photo_meta.captured_at or photo_meta.received_at
    parts = [f"Captured {fmt_time(when, tz)}" if when else "Capture time unknown"]
    ll = photo_position(photo_meta, history)
    parts.append(f"at lat {ll[0]:.6f}, lon {ll[1]:.6f}" if ll else "location unknown")
    return " ".join(parts) + (f" · frame #{photo_meta.frame_seq}" if photo_meta.frame_seq is not None else "")


# ---------------------------------------------------------------- panels
def render_network(n_routes: int, n_services: int, n_buses: int | None, api_err: str | None) -> None:
    st.subheader("Network")
    parts = [f"**{n_routes}** lines", f"**{n_services}** services today"]
    if n_buses is not None:
        parts.append(f"**{n_buses}** buses on the server")
    st.markdown(" · ".join(parts))
    if api_err:
        st.caption(f"API: {api_err}")


def render_route_header(route: Route, *, live_count: int | None, scheduled_count: int, notice: str | None) -> None:
    st.subheader(f"Line {route.short_name}")
    live = "—" if live_count is None else live_count
    st.caption(f"{route.mode_name} · {route.long_name}  \n**{live}** live · **{scheduled_count}** scheduled now")
    if notice:
        st.warning(notice)


def render_live_bus(feed: Feed, route: Route, bus_id: int, info_fetched, frame_fetched, tz: ZoneInfo, *,
                    photo_meta=None, history=None) -> None:
    """Overview tab of the selected bus."""
    info, err = info_fetched
    if err or info is None:
        st.error(f"Bus data unavailable: {err or 'no answer'}")
        return

    p = info.position
    facts = []
    if p is None:
        st.warning("The server has no position for this bus.")
    else:
        age = p.age_s()
        facts.append(f"**{p.speed_kmh:.0f} km/h**" if p.speed_kmh is not None else "speed —")
    facts.append(f"**{'—' if info.occupancy is None else info.occupancy}** passengers")
    if p and p.latlon:
        ns = nearest_stop(feed, route.route_id, *p.latlon)
        if ns:
            facts.append(f"near **{ns[0].name}** ({ns[1]:.0f} m)")
    st.markdown(" · ".join(facts))
    if p:
        st.caption(" · ".join([
            "simulated" if p.sim else "real GPS",
            "GPS fix" if p.fix_ok is not False else "no GPS fix",
            f"updated {fmt_age(age)}" if age is not None else "device clock not set",
        ]))

    frame, ferr = frame_fetched
    if ferr:
        st.caption(f"Photo unavailable: {ferr}")
    elif frame is not None:
        st.image(frame.jpeg, width="stretch")
        st.caption(photo_caption(photo_meta, history, tz))
    else:
        st.caption("No photo yet.")

    if p:
        with st.expander("Details"):
            kv_table({
                "Position": f"{p.lat:.6f}, {p.lon:.6f}" if p.latlon else "—",
                "Position time": fmt_time(p.time, tz),
                "API route": "—" if p.route_id is None else p.route_id,
                "Beacon": p.device_id or "—",
                "Boot ID": p.boot_id or "—",
                "Message seq": "—" if p.seq is None else p.seq,
                "Device uptime": fmt_uptime(p.uptime_ms),
                "Capture interval": f"{info.capture_interval_ms / 1000:.1f} s" if info.capture_interval_ms else "—",
            })
    for e in dict.fromkeys(info.errors.values()):   # each message once
        st.caption(e)


def render_bus_history(history_fetched, tz: ZoneInfo) -> None:
    """History tab of the selected bus."""
    history, herr = history_fetched
    if herr:
        st.caption(herr)
    elif not history:
        st.caption("No history.")
    else:
        rows = sorted(history, key=lambda t: (t.ts or 0, t.uptime_ms or 0), reverse=True)
        st.dataframe(pd.DataFrame([{
            "Time": fmt_time(t.time, tz),
            "km/h": t.speed_kmh,
            "Lat": t.lat,
            "Lon": t.lon,
            "Fix": t.fix_ok,
        } for t in rows]), hide_index=True, width="stretch", height=260)


def render_schedule_vehicle(v: Vehicle) -> None:
    st.markdown(f"**Trip {v.trip_id}**")
    kv_table({
        "Heading to": v.headsign or "—",
        "Previous stop": v.prev_stop or "—",
        "Next stop": v.next_stop or "—",
        "Scheduled speed": f"{v.speed_kmh:.1f} km/h" if v.speed_kmh is not None else "—",
    })
    st.caption("Position interpolated from the timetable.")