"""Device views: the sidebar's Devices page and the Devices tab of a selected bus."""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from typing import Callable
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st

from ..api import combine_device_statuses
from ..api.models import DeviceStatus
from . import data
from .info_panel import fmt_age, fmt_time, fmt_uptime, kv_table

K_DEVICE_TABLE = "device_table"
ROLE_ICONS = {"beacon": "📡", "camera": "📷"}


def picked_row(selection) -> int | None:
    """Row of a dataframe selection: a selected row, or the row of a clicked cell."""
    if not selection:
        return None
    rows = selection.get("rows") or []
    if rows:
        return rows[0]
    cells = selection.get("cells") or []
    return cells[0][0] if cells else None


def _age_s(ds: DeviceStatus, now: datetime) -> float | None:
    seen = ds.last_seen
    return None if seen is None else (now - seen).total_seconds()


def _status(age: float | None, max_age_s: float) -> str:
    if age is None:
        return "never seen"
    return "online" if age <= max_age_s else "offline"


def _badge(status: str) -> str:
    return {"online": ":green[● online]", "offline": ":red[● offline]"}.get(status, ":gray[● never seen]")


def _yes_no(v: bool | None) -> str:
    return "—" if v is None else ("yes" if v else "no")


def render_device_card(ds: DeviceStatus, tz: ZoneInfo, max_age_s: float, now: datetime | None = None) -> None:
    now = now or datetime.now(timezone.utc)
    d = ds.device
    age = _age_s(ds, now)
    role = d.role or "unknown role"
    with st.container(border=True):
        st.markdown(f"{ROLE_ICONS.get(d.role or '', '🔌')} **{role.capitalize()}** `{d.device_id}`  \n"
                    f"{_badge(_status(age, max_age_s))} · last seen {fmt_age(age)}")
        rows: dict = {"Bus": "—" if d.bus_id is None else d.bus_id}
        t = ds.telemetry
        if t:
            rows.update({
                "Last message": fmt_time(t.received_at, tz),
                "Device clock": fmt_time(t.time, tz),
                "Message seq": "—" if t.seq is None else t.seq,
                "Boot ID": t.boot_id or "—",
                "Uptime": fmt_uptime(t.uptime_ms),
                "GPS fix": _yes_no(t.fix_ok),
                "Simulated": _yes_no(t.sim),
                "Last position": f"{t.lat:.6f}, {t.lon:.6f}" if t.latlon else "—",
            })
        f = ds.frame
        if f:
            rows.update({
                "Last frame": fmt_time(f.received_at, tz),
                "Captured": fmt_time(f.captured_at, tz),
                "Frame seq": "—" if f.frame_seq is None else f.frame_seq,
                "Size": f"{f.width}x{f.height}" if f.width and f.height else "—",
            })
        if ds.capture_interval_ms:
            rows["Capture interval"] = f"{ds.capture_interval_ms / 1000:.1f} s"
        if not t and not f:
            rows["Data"] = "nothing received recently"
        kv_table(rows)


def render_bus_devices(fetched, tz: ZoneInfo, max_age_s: float) -> None:
    """Devices tab of the selected bus."""
    result, err = fetched
    if err:
        st.error(f"Devices unavailable: {err}")
        return
    statuses, errors = result or ([], {})
    if not statuses:
        st.info("No devices are registered for this bus.")
    now = datetime.now(timezone.utc)
    for ds in statuses:
        render_device_card(ds, tz, max_age_s, now)
    for part, e in errors.items():
        st.caption(f"{part}: {e}")


def _fleet_statuses() -> tuple[list[DeviceStatus], dict[int, int | None], list[str]]:
    """Every device of every bus. About 2 + (number of buses x 2) calls, all but two in parallel, and the
    device lists are cached for minutes, so a rerun (clicking a row) is usually free."""
    buses, err = data.buses()
    if err:
        raise RuntimeError(f"Buses unavailable: {err}")
    route_of = {b.bus_id: b.route_id for b in buses or []}
    ids = tuple(sorted(route_of))
    notes: list[str] = []

    devices_by_bus = data.fleet_devices(ids)
    telemetry, terr = data.latest_telemetry()
    if terr:
        notes.append(f"Newest messages unavailable: {terr}")
    newest = {t.bus_id: t for t in telemetry or []}

    with_camera = tuple(b for b in ids if any(d.is_camera for d in (devices_by_bus[b][0] or [])))
    frames_by_bus = data.latest_frames(with_camera) if with_camera else {}

    statuses: list[DeviceStatus] = []
    for bid in ids:
        devices, derr = devices_by_bus[bid]
        if derr:
            notes.append(f"Bus {bid}: devices unavailable: {derr}")
            continue
        frames, ferr = frames_by_bus.get(bid, (None, None))
        if ferr:
            notes.append(f"Bus {bid}: frames unavailable: {ferr}")
        statuses.extend(combine_device_statuses(devices or [], newest.get(bid), (frames or [None])[0]))
    return statuses, route_of, notes


def render_devices_view(line_of: Callable[[int | None], str], tz: ZoneInfo, online_s: float) -> None:
    """Full page: every device of every bus on the server. `line_of` names the GTFS line of an API route."""
    st.title("Devices")
    try:
        statuses, route_of, notes = _fleet_statuses()
    except RuntimeError as exc:
        st.error(str(exc))
        return
    if not route_of:
        st.info("The server has no buses.")
        return

    now = datetime.now(timezone.utc)
    ages = [_age_s(ds, now) for ds in statuses]
    states = [_status(a, online_s) for a in ages]
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Buses", len(route_of))
    c2.metric("Devices", len(statuses))
    c3.metric("Online", states.count("online"))
    c4.metric("Offline", len(states) - states.count("online"))
    st.caption(f"Online = the server heard from it in the last {online_s:.0f} s.")
    for n in notes:
        st.caption(n)
    if not statuses:
        st.info("No devices are registered.")
        return

    event = st.dataframe(
        pd.DataFrame([{
            "Device": ds.device.device_id,
            "Role": ds.device.role or "—",
            "Bus": ds.device.bus_id,
            "Line": line_of(route_of.get(ds.device.bus_id)),
            "Status": s,
            "Last seen": fmt_age(a),
        } for ds, a, s in zip(statuses, ages, states)]),
        key=K_DEVICE_TABLE,
        on_select="rerun",
        selection_mode=["single-row", "single-cell"],   # a click on any cell picks its row
        hide_index=True,
        width="stretch",
    )
    row = picked_row(event.selection if event else None)
    if row is None or not 0 <= row < len(statuses):
        st.caption("Click a device to see it.")
        return
    ds = statuses[row]
    if ds.device.is_camera and ds.device.bus_id is not None:
        interval, _ = data.capture_interval(ds.device.bus_id)   # only for the device being looked at
        if interval:
            ds = replace(ds, capture_interval_ms=interval.next_capture_ms)
    render_device_card(ds, tz, online_s, now)
