"""Device views: the sidebar's Devices page and the Devices tab of a selected bus."""
from __future__ import annotations

import functools
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
K_SELECTED_DEVICE = "selected_device"   # (device_id, bus_id) shown in the card
K_LAST_PICK = "device_last_pick"       # the last row clicked, so a click is told apart from a refresh
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
    for e in dict.fromkeys(errors.values()):   # each message once
        st.caption(e)


def fleet_statuses(with_cameras: bool = False) -> tuple[list[DeviceStatus], dict[int, int | None], list[str]]:
    """Every device of every bus: one call for every bus's newest message (the beacons) and the device lists
    (cached for minutes); with `with_cameras`, also each camera's newest photo metadata (a call per bus)."""
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

    # cameras: their newest photo's metadata (no image), one call per bus. The Fleet page asks for it; the
    # Devices list doesn't (there only the selected device is checked, by its card)
    frames_by_bus = {}
    if with_cameras:
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
            notes.append(f"Bus {bid}: camera unavailable: {ferr}")
        statuses.extend(combine_device_statuses(devices or [], newest.get(bid), (frames or [None])[0]))
    return statuses, route_of, notes


def render_devices_view(line_of: Callable[[int | None], str], tz: ZoneInfo, online_s: float, *,
                        live: bool, list_every_s: float, card_every_s: float, photo_every_s: float) -> None:
    """Full page: every device of every bus on the server. `line_of` names the GTFS line of an API route.
    With `live`, the list re-runs on its own every `list_every_s` and the selected device's card every
    `card_every_s`; each part re-runs alone, the rest of the page stays as it is."""
    st.title("Devices")
    list_part, card_part = _fragments(list_every_s if live else None, card_every_s if live else None)
    # list on the left, the picked device beside it: visible at once, also when arriving from the Fleet page
    col_list, col_card = st.columns([3, 2], gap="medium")
    with col_list:
        list_part(line_of, online_s)
    with col_card:
        card_part(tz, online_s, photo_every_s)


@functools.lru_cache(maxsize=None)
def _fragments(list_every_s: float | None, card_every_s: float | None):
    """The two self-updating parts, made once per interval setting."""
    return st.fragment(run_every=list_every_s)(_device_list), st.fragment(run_every=card_every_s)(_device_card)


def _is_selected(ds: DeviceStatus) -> bool:
    picked = st.session_state.get(K_SELECTED_DEVICE)
    return bool(picked) and picked[0] == ds.device.device_id


def _with_selected_camera(statuses: list[DeviceStatus]) -> list[DeviceStatus]:
    """The selected camera's row gets the same data its card shows (fetched for the card anyway, cached)."""
    picked = st.session_state.get(K_SELECTED_DEVICE)
    if not picked or picked[1] is None:
        return statuses
    snap, _err = data.bus_snapshot(picked[1], 1)
    fresh = {d.device.device_id: d for d in (snap.devices if snap else [])}
    return [fresh.get(ds.device.device_id, ds) if ds.device.is_camera and _is_selected(ds) else ds for ds in statuses]


def _device_list(line_of: Callable[[int | None], str], online_s: float) -> None:
    data.require_server()   # server gone: the whole app shows the 'connection lost' banner
    try:
        statuses, route_of, notes = fleet_statuses()
    except RuntimeError as exc:
        st.error(str(exc))
        return
    if not route_of:
        st.info("The server has no buses.")
        return

    now = datetime.now(timezone.utc)
    statuses = _with_selected_camera(statuses)
    shown = [not ds.device.is_camera or ds.frame is not None or _is_selected(ds) for ds in statuses]
    ages = [_age_s(ds, now) if ok else None for ds, ok in zip(statuses, shown)]
    states = [_status(a, online_s) if ok else "—" for a, ok in zip(ages, shown)]
    beacons = [s for ds, s in zip(statuses, states) if not ds.device.is_camera]
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Buses", len(route_of))
    c2.metric("Devices", len(statuses))
    c3.metric("Beacons online", beacons.count("online"))
    c4.metric("Beacons offline", len(beacons) - beacons.count("online"))
    st.caption(f"Online = the server heard from it in the last {online_s:.0f} s. A camera's status is shown "
               "for the selected device (checking every camera here would take a call per bus).")
    for n in notes:
        st.caption(n)
    if not statuses:
        st.info("No devices are registered.")
        return

    ss = st.session_state
    event = st.dataframe(
        pd.DataFrame([{
            "Device": ds.device.device_id,
            "Role": ds.device.role or "—",
            "Bus": ds.device.bus_id,
            "Line": line_of(route_of.get(ds.device.bus_id)),
            "Status": s,
            "Last seen": fmt_age(a) if ok else "—",
        } for ds, a, s, ok in zip(statuses, ages, states, shown)]),
        key=K_DEVICE_TABLE,
        on_select="rerun",
        selection_mode=["single-row", "single-cell"],   # a click on any cell picks its row
        hide_index=True,
        width="stretch",
    )
    row = picked_row(event.selection if event else None)
    picked = statuses[row].device if row is not None and 0 <= row < len(statuses) else None
    picked_key = (picked.device_id, picked.bus_id) if picked else None
    # a click on another device: remember it (by id, the list order may change) and redraw the card at once.
    # An empty selection never clears it, so a list refresh can't close the card; the card has a Close button.
    if picked_key is not None and picked_key != ss.get(K_LAST_PICK):
        ss[K_LAST_PICK] = picked_key
        ss[K_SELECTED_DEVICE] = picked_key
        st.rerun()


def _device_card(tz: ZoneInfo, online_s: float, photo_every_s: float) -> None:
    data.require_server()   # server gone: the whole app shows the 'connection lost' banner
    picked = st.session_state.get(K_SELECTED_DEVICE)
    if not picked:
        st.caption("Click a device to see it.")
        return
    device_id, bus_id = picked
    if st.button("✕ Close", key="close_device"):
        st.session_state[K_SELECTED_DEVICE] = None
        st.rerun()
    if bus_id is None:
        st.caption(f"Device {device_id} has no bus.")
        return
    snap, err = data.bus_snapshot(bus_id, 1, photo_every_s=photo_every_s)
    if err or snap is None:
        st.error(f"Device data unavailable: {err or 'no answer'}")
        return
    ds = next((d for d in snap.devices if d.device.device_id == device_id), None)
    if ds is None:
        st.warning(f"Device {device_id} is no longer registered on bus {bus_id}.")
        return
    render_device_card(ds, tz, online_s)
