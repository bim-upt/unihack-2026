"""The bus panel's Sightings tab: the phones that heard this bus's beacon, newest first."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st

from . import data, state
from .devices import picked_row
from .info_panel import fmt_age
from .sightings import REASONS
from .users import K_LAST_USER_PICK, K_SELECTED_USER

WINDOWS = {"15 min": 15, "1 h": 60, "All kept": None}
K_WINDOW = "bus_sight_window"   # kept with the Map page's settings (nav.PAGE_SETTINGS)


def render_bus_sightings(bus_id: int, tz: ZoneInfo) -> None:
    ss = st.session_state
    ss.setdefault(K_WINDOW, "15 min")
    window = st.segmented_control("Window", list(WINDOWS), key=K_WINDOW, label_visibility="collapsed") or "15 min"
    rows, err = data.bus_sightings(bus_id)   # the newest 1000 of this bus (shared with the Inspector's cache)
    if err:
        st.error(f"Sightings unavailable: {err}")
        return
    now = datetime.now(timezone.utc)
    minutes = WINDOWS[window]
    shown = []
    for s in rows or []:
        when = pd.to_datetime(s.get("received_at"), utc=True, errors="coerce")
        if pd.isna(when) or (minutes is not None and now - when > timedelta(minutes=minutes)):
            continue
        shown.append((when, s))
    if not shown:
        st.info(f"No phone heard this bus in the last {window}." if minutes else "No sightings of this bus.")
        return

    accepted = sum(bool(s.get("accepted")) for _, s in shown)
    phones = {s.get("user_id") for _, s in shown if s.get("user_id")}
    st.markdown(f"**{len(shown)}** sightings · **{accepted / len(shown):.0%}** accepted · **{len(phones)}** phones")

    users = data.users_by_id(tuple(sorted(phones))) if phones else {}
    def name(uid):
        u = (users.get(uid) or (None, None))[0] or {}
        return u.get("name") or ("(anonymous)" if u.get("tier") == "anonymous" else (uid or "—")[:8])

    ids = [s.get("user_id") for _, s in shown]
    event = st.dataframe(
        pd.DataFrame([{
            "Heard": when.astimezone(tz).strftime("%H:%M:%S"),
            "Ago": fmt_age((now - when).total_seconds()),
            "User": name(s.get("user_id")),
            "Result": "✅ accepted" if s.get("accepted") else f"❌ {REASONS.get(s.get('reject_reason'), s.get('reject_reason') or 'rejected')}",
            "RSSI": s.get("rssi"),
            "Slot offset": s.get("slot_offset"),
            "Clock synced": "yes" if s.get("clock_synced") else "no",
        } for when, s in shown]),
        key=f"bus_sightings_{bus_id}_{hash(tuple(ids))}",   # follows the rows: a refresh can't turn into a click
        on_select="rerun", selection_mode=["single-row", "single-cell"],
        hide_index=True, width="stretch", height=(min(len(shown), 10) + 1) * 35 + 18,
    )
    st.caption("Click a sighting to open its user.")
    row = picked_row(event.selection if event else None)
    uid = ids[row] if row is not None and 0 <= row < len(ids) else None
    if uid and uid != ss.get("bus_sight_last_pick"):
        ss["bus_sight_last_pick"] = uid
        ss[K_SELECTED_USER] = ss[K_LAST_USER_PICK] = uid
        ss[state.K_PENDING_VIEW] = "users"   # another page: a browser history entry, Back returns to the bus
        st.rerun()
