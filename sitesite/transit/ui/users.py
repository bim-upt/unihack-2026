"""Users page: who uses the app and what each user did (rides, charges, trips, passes, sightings)."""
from __future__ import annotations

import functools
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st

from . import data
from .devices import picked_row
from .info_panel import fmt_age, kv_table

K_SEARCH, K_TIER, K_RIDING = "user_search", "user_tier", "user_riding_only"
K_SELECTED_USER, K_LAST_USER_PICK = "selected_user", "user_last_pick"
K_SELECTED_RIDE, K_LAST_RIDE_PICK = "selected_ride", "ride_last_pick"
K_USER_TAB = "user_tab_choice"   # name of the tab open in the user panel ("Rides", "Sightings", ...)
TIERS = ("all", "account", "anonymous")
SETTING_LABELS = {
    "autoCharge": "Setting: pay automatically",
    "tripReminders": "Setting: trip reminders",
    "arrivalAlerts": "Setting: arrival alerts",
    "disruptions": "Setting: disruption alerts",
    "locationSharing": "Setting: share location",
}


# ---------------------------------------------------------------- formatting
def _dt(v) -> datetime | None:
    if not v:
        return None
    try:
        d = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def _when(v, tz: ZoneInfo) -> str:
    d = _dt(v)
    return d.astimezone(tz).strftime("%Y-%m-%d %H:%M") if d else "—"


def _ago(v) -> str:
    d = _dt(v)
    return fmt_age((datetime.now(timezone.utc) - d).total_seconds()) if d else "never"


def _yes(v) -> str:
    return "—" if v is None else ("yes" if v else "no")


def _mask(v, keep: int = 4) -> str:
    """Show only the end of a card number or token."""
    s = str(v or "")
    return "—" if not s else ("•••• " + s[-keep:] if len(s) > keep else s)


def _lines(v) -> str:
    return ", ".join(v) if isinstance(v, list) else str(v or "—")


def _name(u: dict) -> str:
    return u.get("name") or ("(anonymous)" if u.get("tier") == "anonymous" else "(no name)")


ROW_PX, MAX_ROWS_SHOWN = 35, 10


def fit_height(n_rows: int) -> int:
    """Tall enough for every row plus room for a sideways scrollbar, so a short table never scrolls by a few
    pixels; past MAX_ROWS_SHOWN rows the table scrolls inside itself."""
    return (min(n_rows, MAX_ROWS_SHOWN) + 1) * ROW_PX + 18


def _table(rows: list[dict], empty: str):
    if not rows:
        st.caption(empty)
        return
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch", height=fit_height(len(rows)))


# ---------------------------------------------------------------- page
def render_users_view(tz: ZoneInfo, *, live: bool, list_every_s: float, user_every_s: float) -> None:
    """The list on top, the picked user under it, both the full width of the page so their tables fit without
    scrolling sideways. With `live`, each part re-runs on its own."""
    st.title("Users")
    list_part, user_part = _fragments(list_every_s if live else None, user_every_s if live else None)
    list_part(tz)
    st.divider()
    user_part(tz)


@functools.lru_cache(maxsize=None)
def _fragments(list_every_s: float | None, user_every_s: float | None):
    return st.fragment(run_every=list_every_s)(_user_list), st.fragment(run_every=user_every_s)(_user_panel)


def _user_list(tz: ZoneInfo) -> None:
    data.require_server()   # server gone: the whole app shows the 'connection lost' banner
    users, err = data.users()
    if err:
        st.error(f"Users unavailable: {err}")
        return
    users = users or []
    riding = [u for u in users if u.get("open_ride_status")]
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Users", len(users))
    c2.metric("Accounts", sum(u.get("tier") == "account" for u in users))
    c3.metric("Riding now", len(riding))
    c4.metric("Inspectors", sum("inspector" in (u.get("roles") or []) for u in users))

    f1, f2, f3 = st.columns([3, 2, 2], vertical_alignment="bottom")
    query = f1.text_input("Search", key=K_SEARCH, placeholder="name, email or id").strip().lower()
    tier = f2.selectbox("Tier", TIERS, key=K_TIER)
    only_riding = f3.toggle("Riding now", key=K_RIDING)

    shown = [u for u in users
             if (tier == "all" or u.get("tier") == tier)
             and (not only_riding or u.get("open_ride_status"))
             and (not query or any(query in str(u.get(k) or "").lower() for k in ("name", "email", "id")))]
    if not shown:
        st.caption("No user matches.")
        return

    ss = st.session_state
    ids = [u["id"] for u in shown]
    event = st.dataframe(
        pd.DataFrame([{
            "Name": _name(u),
            "Email": u.get("email") or "—",
            "Tier": u.get("tier") or "—",
            "Role": ", ".join(u.get("roles") or []) or "—",
            "Riding": u.get("open_ride_status") or "",
            "Cards": u.get("cards_count"),
            "Passes": u.get("passes_count"),
            "Last seen": _ago(u.get("last_seen_at")),
            "Joined": _when(u.get("created_at"), tz),
        } for u in shown]),
        # the key follows the rows shown, so a refresh or a filter that moves rows can't select another user
        key=f"user_table_{hash(tuple(ids))}",
        height=fit_height(len(shown)),
        on_select="rerun",
        selection_mode=["single-row", "single-cell"],
        hide_index=True,
        width="stretch",
    )
    st.caption(f"{len(shown)} of {len(users)} users. Click one to see it.")
    row = picked_row(event.selection if event else None)
    picked = ids[row] if row is not None and 0 <= row < len(ids) else None
    if picked is not None and picked != ss.get(K_LAST_USER_PICK):
        ss[K_LAST_USER_PICK] = picked
        ss[K_SELECTED_USER] = picked
        ss[K_SELECTED_RIDE] = ss[K_LAST_RIDE_PICK] = None
        st.rerun()   # the user panel is another part: redraw both at once


def _user_panel(tz: ZoneInfo) -> None:
    data.require_server()   # server gone: the whole app shows the 'connection lost' banner
    ss = st.session_state
    uid = ss.get(K_SELECTED_USER)
    if not uid:
        st.caption("Click a user in the list to see their rides, charges, trips and passes.")
        return
    user, err = data.user(uid)
    if err or user is None:
        st.error(f"User unavailable: {err or 'not found'}")
        return

    head, close = st.columns([5, 1], vertical_alignment="center")
    head.subheader(_name(user))
    if close.button("✕", key="close_user", help="Close"):
        ss[K_SELECTED_USER] = None
        st.rerun()
    st.caption(" · ".join([
        user.get("email") or "no email",
        user.get("tier") or "—",
        ", ".join(user.get("roles") or []) or "—",
        f"joined {_when(user.get('created_at'), tz)}",
        f"last seen {_ago(user.get('last_seen_at'))}",
    ]))
    if user.get("open_ride_status"):
        st.info(f"Riding now: ride is **{user['open_ride_status']}**")

    activity = data.user_activity(uid)
    rec = {name: (activity[name][0] or []) for name in data.USER_ACTIVITY}
    for name in data.USER_ACTIVITY:
        if activity[name][1]:
            st.caption(f"{name}: {activity[name][1]}")

    names = ["Rides", "Charges", "Trips", "Passes", "Sightings", "Account"]
    labels = [f"{n} ({len(rec[n.lower()])})" if n.lower() in rec else n for n in names]
    # the tab a user had open stays open for the next user: remembered by name (the counts differ)
    chosen = ss.get(K_USER_TAB, names[0])
    tab_key = f"user_tabs_{uid}"
    tabs = st.tabs(labels, key=tab_key, default=labels[names.index(chosen)] if chosen in names else None,
                   on_change=_remember_tab, args=(tab_key,))
    with tabs[0]:
        _rides(rec["rides"], tz)
    with tabs[1]:
        _table([{
            "Started": _when(c.get("startedAt"), tz),
            "Kind": c.get("kind"),
            "Line": c.get("line") or "—",
            "To": c.get("destination") or "—",
            "Amount": f"{c.get('amount')} {c.get('currency') or ''}".strip(),
            "Status": c.get("status"),
            "Paid with": c.get("paidWith") or "—",
            "Passengers": c.get("passengers"),
            "Ended": _when(c.get("endedAt"), tz),
        } for c in rec["charges"]], "No charges.")
    with tabs[2]:
        _table([{
            "Date": t.get("date"), "Time": t.get("time"), "Line": t.get("line"),
            "From": t.get("from"), "To": t.get("to"), "Fare (RON)": t.get("fareRon"),
            "km": t.get("distanceKm"), "min": t.get("durationMin"), "Bus": t.get("bus_id"),
        } for t in rec["trips"]], "No trips.")
    with tabs[3]:
        _table([{
            "Pass": p.get("name"), "Holder": p.get("holder"), "Card": _mask(p.get("cardNumber")),
            "Valid from": _when(p.get("validFrom"), tz), "Valid until": _when(p.get("validUntil"), tz),
            "Lines": _lines(p.get("lines")), "Verified": _yes(p.get("verified")),
            "Renewed": _when(p.get("last_renewed_at"), tz),
        } for p in rec["passes"]], "No passes.")
    with tabs[4]:
        _table([{
            "Received": _when(s.get("received_at"), tz), "Bus": s.get("bus_id"), "Slot": s.get("slot"),
            "Offset": s.get("slot_offset"), "RSSI": s.get("rssi"), "Clock synced": _yes(s.get("clock_synced")),
            "Accepted": _yes(s.get("accepted")), "Refused because": s.get("reject_reason") or "",
        } for s in rec["sightings"]], "No sightings.")
    with tabs[5]:
        _account(user, tz)


def _remember_tab(tab_key: str) -> None:
    label = st.session_state.get(tab_key)
    if label:
        st.session_state[K_USER_TAB] = str(label).split(" (")[0]


def _rides(rides: list[dict], tz: ZoneInfo) -> None:
    if not rides:
        st.caption("No rides.")
        return
    ss = st.session_state
    ids = [r["session_id"] for r in rides]
    event = st.dataframe(
        pd.DataFrame([{
            "Started": _when(r.get("created_at"), tz),
            "Line": r.get("line") or "—",
            "To": r.get("destination") or "—",
            "Status": r.get("status"),
            "Bus": r.get("bus_id"),
            "Passengers": r.get("passengers"),
            "Ended because": r.get("end_reason") or "",
            "Manual": _yes(r.get("manual")),
        } for r in rides]),
        key=f"ride_table_{hash(tuple(ids))}",
        height=fit_height(len(rides)),
        on_select="rerun",
        selection_mode=["single-row", "single-cell"],
        hide_index=True,
        width="stretch",
    )
    row = picked_row(event.selection if event else None)
    picked = ids[row] if row is not None and 0 <= row < len(ids) else None
    if picked is not None and picked != ss.get(K_LAST_RIDE_PICK):
        ss[K_LAST_RIDE_PICK] = picked
        ss[K_SELECTED_RIDE] = picked
    if ss.get(K_SELECTED_RIDE) in ids:
        _ride_detail(ss[K_SELECTED_RIDE], tz)
    else:
        st.caption("Click a ride to see how it went.")


def _ride_detail(session_id: str, tz: ZoneInfo) -> None:
    ride, err = data.ride(session_id)
    if err or ride is None:
        st.error(f"Ride unavailable: {err or 'not found'}")
        return
    st.markdown(f"**Ride** `{session_id[:8]}` · {ride.get('status')} · line {ride.get('line') or '—'}"
                f" → {ride.get('destination') or '—'}")
    history = ride.get("status_history") or []
    if history:
        st.markdown(" → ".join(f"**{h.get('status')}** ({_when(h.get('at'), tz)[-5:]})" for h in history))
    q = ride.get("qualification")
    facts = {
        "Created": _when(ride.get("created_at"), tz),
        "Confirmed": _when(ride.get("confirmed_at"), tz),
        "Ended": _when(ride.get("ended_at"), tz),
        "Ended because": ride.get("end_reason") or "—",
        "Payment note": ride.get("payment_reason") or "—",
        "Manual (\"I am on the bus\")": _yes(ride.get("manual")),
        "Offered because": (f"{q.get('slots_in_row')} slots in a row, bus moved {q.get('distance_m')} m"
                            if q else "—"),
        "Candidates": ", ".join(f"bus {c.get('busId')} ({c.get('line')})" for c in ride.get("candidates") or []) or "—",
    }
    charge = ride.get("charge")
    if charge:
        facts["Charge"] = (f"{charge.get('amount')} {charge.get('currency') or ''} · {charge.get('status')}"
                           f" · {charge.get('paidWith') or '—'}")
    kv_table(facts)
    sightings = ride.get("sightings") or []
    with st.expander(f"Sightings during the ride ({len(sightings)})"):
        _table([{
            "Received": _when(s.get("received_at"), tz), "Bus": s.get("bus_id"),
            "Slot": s.get("slot"), "RSSI": s.get("rssi"),
        } for s in sightings], "None.")


def _account(user: dict, tz: ZoneInfo) -> None:
    settings = user.get("settings") or {}
    kv_table({
        "User id": user.get("id"),
        "Cards": user.get("cards_count"),
        "Passes": user.get("passes_count"),
        "Default payment": user.get("default_source_id") or "—",
        **{SETTING_LABELS.get(k, k): _yes(v) for k, v in settings.items()},
    })
    st.markdown("**Sign-in methods**")
    _table([{"Provider": i.get("provider"), "Subject": _mask(i.get("subject"), 6)}
            for i in user.get("identities") or []], "None (anonymous).")
    st.markdown("**Phones (sessions)**")
    _table([{
        "Device": s.get("device_id"), "First login": _when(s.get("created_at"), tz),
        "Last refresh": _when(s.get("last_refresh_at"), tz), "Expires": _when(s.get("expires_at"), tz),
        "Revoked": _yes(s.get("revoked")), "Why": s.get("revoked_reason") or "",
    } for s in user.get("sessions") or []], "No sessions.")
    st.markdown("**Push notifications**")
    _table([{"Platform": p.get("platform"), "Token": _mask(p.get("push_token"), 6)}
            for p in user.get("push_devices") or []], "None registered.")
