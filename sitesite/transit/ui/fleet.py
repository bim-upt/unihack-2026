"""Fleet page: every bus at once, coloured by how recently it was heard from, and the devices that went quiet."""
from __future__ import annotations

import functools
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable
from zoneinfo import ZoneInfo

import folium
import pandas as pd
import streamlit as st
from streamlit_folium import st_folium

from ..api.models import Telemetry
from ..config import Bounds, Settings
from ..mapview.layers import build_base_map
from . import data, state
from .devices import K_SELECTED_DEVICE, fleet_statuses, picked_row
from .info_panel import fmt_age

MOVING_KMH = 3.0   # slower than this counts as stopped

# status -> (colour, label); the order is the legend's
STATUSES = {
    "moving": ("#2e7d32", "moving"),
    "stopped": ("#1565c0", "stopped"),
    "silent": ("#ef6c00", "silent > 1 min"),
    "offline": ("#c62828", "offline > 10 min"),
}
TIP = re.compile(r"^Bus (\d+)\b")


@dataclass(frozen=True)
class BusState:
    bus_id: int
    line: str
    gtfs_route_id: str | None
    status: str                 # a key of STATUSES, or "never" (no position at all)
    age_s: float | None
    position: Telemetry | None


def classify(t: Telemetry | None, now: datetime, silent_s: float, offline_s: float) -> tuple[str, float | None]:
    """Status of a bus from its newest message. Pure."""
    if t is None:
        return "never", None
    seen = t.received_at or t.time
    if seen is None:
        return "offline", None
    age = (now - seen).total_seconds()
    if age > offline_s:
        return "offline", age
    if age > silent_s:
        return "silent", age
    return ("moving" if (t.speed_kmh or 0) >= MOVING_KMH else "stopped"), age


def render_fleet_view(settings: Settings, area: Bounds, line_of: Callable[[int | None], str],
                      gtfs_of: Callable[[int | None], str | None], *, live: bool) -> None:
    st.title("Fleet")
    _fragment(settings.fleet_refresh_s if live else None)(settings, area, line_of, gtfs_of)


@functools.lru_cache(maxsize=None)
def _fragment(every_s: float | None):
    return st.fragment(run_every=every_s)(_fleet)


def _fleet(settings: Settings, area: Bounds, line_of: Callable[[int | None], str],
           gtfs_of: Callable[[int | None], str | None]) -> None:
    data.require_server()   # server gone: the whole app shows the 'connection lost' banner
    silent_s, offline_s = settings.fleet_silent_s, settings.fleet_offline_s
    try:
        devices, route_of, notes = fleet_statuses(with_cameras=True)
    except RuntimeError as exc:
        st.error(str(exc))
        return
    telemetry, terr = data.latest_telemetry()
    if terr:
        notes.append(f"Positions unavailable: {terr}")
    newest = {t.bus_id: t for t in telemetry or []}
    now = datetime.now(timezone.utc)

    buses = []
    for bid in sorted(route_of):
        status, age = classify(newest.get(bid), now, silent_s, offline_s)
        buses.append(BusState(bid, line_of(route_of[bid]), gtfs_of(route_of[bid]), status, age, newest.get(bid)))
    count = {k: sum(b.status == k for b in buses) for k in (*STATUSES, "never")}

    cols = st.columns(5)
    cols[0].metric("Buses", len(buses))
    cols[1].metric("Moving", count["moving"])
    cols[2].metric("Stopped", count["stopped"])
    cols[3].metric("Silent > 1 min", count["silent"])
    cols[4].metric("Offline / no data", count["offline"] + count["never"])
    for n in notes:
        st.caption(n)

    col_map, col_side = st.columns([3, 2], gap="medium")
    with col_map:
        _map(settings, area, buses)
        st.markdown(" &nbsp; ".join(f"<span style='color:{c}'>●</span> {label}" for c, label in STATUSES.values()),
                    unsafe_allow_html=True)
        st.caption(f"Updated every {settings.fleet_refresh_s:g} s · {now.astimezone(settings.tz):%H:%M:%S}. "
                   "Click a bus to open it on the map.")
    with col_side:
        _attention(devices, route_of, line_of, now, silent_s)


def _map(settings: Settings, area: Bounds, buses: list[BusState]) -> None:
    base = build_base_map(settings, area, {})
    fg = folium.FeatureGroup(name="fleet")
    for b in buses:
        p = b.position
        if p is None or p.latlon is None:
            continue
        colour = STATUSES.get(b.status, ("#757575", ""))[0]
        speed = f"{p.speed_kmh:.0f} km/h" if p.speed_kmh is not None else "speed —"
        folium.CircleMarker(
            p.latlon, radius=8, color="#ffffff", weight=2, fill=True, fill_color=colour, fill_opacity=0.95,
            tooltip=(f"Bus {b.bus_id} · line {b.line} · {STATUSES[b.status][1]} · {speed} · "
                     f"heard {fmt_age(b.age_s)}" + (" · simulated" if p.sim else "")),
        ).add_to(fg)
    result = st_folium(base, feature_group_to_add=fg, key="fleet_map", height=settings.map_height,
                       use_container_width=True, returned_objects=["last_object_clicked_tooltip"])
    tip = (result or {}).get("last_object_clicked_tooltip")
    m = TIP.match(tip or "")
    ss = st.session_state
    if m and tip != ss.get("fleet_last_tip"):
        ss["fleet_last_tip"] = tip
        bus = next((b for b in buses if b.bus_id == int(m.group(1))), None)
        if bus:
            state.open_bus_on_map(bus.gtfs_route_id, str(bus.bus_id))
            st.rerun()


def _attention(devices, route_of: dict, line_of: Callable[[int | None], str], now: datetime,
               silent_s: float) -> None:
    """Devices with no sign of life for more than `silent_s`, longest silence first; never-seen ones apart."""
    st.subheader("Needs attention")
    quiet, never = [], []
    for ds in devices:
        seen = ds.last_seen
        row = {"Device": ds.device.device_id, "Role": ds.device.role or "—", "Bus": ds.device.bus_id,
               "Line": line_of(route_of.get(ds.device.bus_id))}
        if seen is None:
            never.append(row)
        else:
            age = (now - seen).total_seconds()
            if age > silent_s:
                quiet.append({**row, "Silent for": fmt_age(age).replace(" ago", ""), "_age": age})
    if quiet:
        quiet.sort(key=lambda r: -r["_age"])
        _device_table(quiet, "fleet_quiet")
        st.caption(f"{len(quiet)} device(s) silent for more than {silent_s:.0f} s. Click one to open it. "
                   "Cameras: from their newest photo's time (no images are downloaded here).")
    else:
        st.success(f"Every device that has reported was heard in the last {silent_s:.0f} s.")
    if never:
        with st.expander(f"Never sent anything ({len(never)})"):
            _device_table(never, "fleet_never")


def _device_table(rows: list[dict], key: str) -> None:
    """A clickable list of devices: a click opens that device on the Devices page."""
    keys = [(r["Device"], r["Bus"]) for r in rows]
    df = pd.DataFrame(rows).drop(columns="_age", errors="ignore")
    event = st.dataframe(
        df, hide_index=True, width="stretch", height=min(len(df) + 1, 12) * 35 + 18,
        key=f"{key}_{hash(tuple(keys))}",   # follows the rows, so a refresh can't turn into a click
        on_select="rerun", selection_mode=["single-row", "single-cell"],
    )
    row = picked_row(event.selection if event else None)
    picked = keys[row] if row is not None and 0 <= row < len(keys) else None
    ss = st.session_state
    if picked is not None and picked != ss.get(f"{key}_last_pick"):
        ss[f"{key}_last_pick"] = picked
        ss[K_SELECTED_DEVICE] = picked
        ss[state.K_PENDING_VIEW] = "devices"
        st.rerun()   # the whole app: the sidebar's View switch must change too
