"""Inspector page: pick a bus, see who is on it and whether each person paid or holds a pass valid on its line.

"On the bus" = an open ride on this bus, or the bus's beacon heard by the person's phone in the last few
minutes (accepted sightings) without a ride on it: those are the people to check first.
"""
from __future__ import annotations

import functools
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Callable
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st

from ..config import Settings
from . import data
from .info_panel import fmt_age

OPEN_RIDE = ("pending", "awaiting_confirmation", "payment_failed", "confirmed")   # rides still going on
PAID_CHARGE = ("in_progress", "completed")
SEEN_WINDOWS = {"2 min": 2, "5 min": 5, "15 min": 15}
K_BUS, K_SEEN = "insp_bus", "insp_seen"

# verdict -> (level, label); level picks the colour: ok (green), check (amber), fail (red)
VERDICTS = {
    "paid": ("ok", "Paid ticket"),
    "pass_ride": ("ok", "Riding on a pass"),
    "valid_pass": ("ok", "No ticket, valid pass for this line"),
    "pending": ("check", "Ride not confirmed yet"),
    "unverified_pass": ("check", "Pass for this line, not verified"),
    "payment_failed": ("fail", "Payment failed"),
    "wrong_line": ("fail", "Pass not valid on this line"),
    "expired": ("fail", "Pass expired"),
    "nothing": ("fail", "No ticket, no pass"),
}
LEVEL = {"ok": ("✅", "#0ca30c", "#e3f3e3"), "check": ("⚠️", "#b07800", "#fff3d1"), "fail": ("❌", "#d03b3b", "#fbe1e1")}


@dataclass(frozen=True)
class PassCheck:
    name: str
    valid_now: bool
    covers_line: bool
    verified: bool
    until: str


def check_pass(p: dict, line: str, today: date) -> PassCheck:
    """Is this pass valid today, and does it cover `line`? Pure."""
    lines = p.get("lines")
    if isinstance(lines, str):
        covers = lines.strip().lower() == "all" or line.casefold() in (x.strip().casefold() for x in lines.split(","))
    else:
        covers = line.casefold() in (str(x).casefold() for x in lines or [])
    try:
        start = date.fromisoformat(str(p.get("validFrom"))[:10])
        end = date.fromisoformat(str(p.get("validUntil"))[:10])
        valid = start <= today <= end
    except ValueError:
        valid = False
    return PassCheck(p.get("name") or "pass", valid, covers, bool(p.get("verified")), str(p.get("validUntil") or "—"))


def verdict(ride: dict | None, charge: dict | None, passes: list[PassCheck]) -> str:
    """What an inspector should think of this person. Pure."""
    if charge and charge.get("status") == "covered_by_pass":
        return "pass_ride"
    if ride and ride.get("status") == "confirmed" and charge and charge.get("status") in PAID_CHARGE:
        return "pass_ride" if charge.get("source_kind") == "pass" else "paid"
    good = [p for p in passes if p.valid_now and p.covers_line]
    if any(p.verified for p in good):
        return "valid_pass"
    if good:
        return "unverified_pass"
    if ride and ride.get("status") in ("pending", "awaiting_confirmation"):
        return "pending"
    if ride and ride.get("status") == "payment_failed":
        return "payment_failed"
    if any(p.valid_now for p in passes):
        return "wrong_line"
    if passes:
        return "expired"
    return "nothing"


def _pass_text(p: PassCheck, line: str) -> str:
    where = f"valid on {line}" if p.covers_line else f"not valid on {line}"
    when = f"until {p.until}" if p.valid_now else f"expired {p.until}"
    return f"{p.name} · {where} · {when}" + ("" if p.verified else " · not verified")


# ---------------------------------------------------------------- page
def render_inspector_view(settings: Settings, line_of: Callable[[int | None], str], *, live: bool) -> None:
    st.title("Inspector")
    buses, err = data.buses()
    if err:
        st.error(f"Buses unavailable: {err}")
        return
    route_of = {b.bus_id: b.route_id for b in buses or []}
    c1, c2 = st.columns([3, 2], vertical_alignment="bottom")
    bus_id = c1.selectbox("Bus", sorted(route_of), key=K_BUS, index=None, placeholder="Pick the bus you're on",
                          format_func=lambda b: f"Bus {b} · line {line_of(route_of.get(b))}")
    seen = c2.segmented_control("Phones that heard the bus in the last", list(SEEN_WINDOWS), key=K_SEEN,
                                default="5 min") or "5 min"
    if bus_id is None:
        st.caption("Pick a bus to see the people on it and whether each one has paid.")
        return
    _fragment(settings.inspector_refresh_s if live else None)(settings, bus_id, line_of(route_of.get(bus_id)),
                                                               SEEN_WINDOWS[seen])


@functools.lru_cache(maxsize=None)
def _fragment(every_s: float | None):
    return st.fragment(run_every=every_s)(_people)


def _people(settings: Settings, bus_id: int, line: str, seen_min: int) -> None:
    data.require_server()   # server gone: the whole app shows the 'connection lost' banner
    tz: ZoneInfo = settings.tz
    rides, rerr = data.bus_rides(bus_id)
    sightings, serr = data.bus_sightings(bus_id)
    passes, perr = data.all_passes()
    charges, cerr = data.charges(None, None)
    users, uerr = data.users()
    for e in dict.fromkeys(x for x in (rerr, serr, perr, cerr, uerr) if x):
        st.caption(e)

    now = datetime.now(timezone.utc)
    open_rides = {}
    for r in rides or []:
        if r.get("status") in OPEN_RIDE and r.get("user_id") not in open_rides:   # newest first: keep the newest
            open_rides[r["user_id"]] = r
    last_seen: dict[str, datetime] = {}
    for s in sightings or []:
        when = pd.to_datetime(s.get("received_at"), utc=True, errors="coerce")
        if s.get("accepted") and s.get("user_id") and pd.notna(when) and now - when <= timedelta(minutes=seen_min):
            last_seen[s["user_id"]] = max(when, last_seen.get(s["user_id"], when))
    charge_by_id = {c.get("id"): c for c in charges or []}
    passes_by_user: dict[str, list[dict]] = {}
    for p in passes or []:
        passes_by_user.setdefault(p.get("user_id"), []).append(p)
    who = {u.get("id"): u for u in users or []}
    today = now.astimezone(tz).date()

    rows = []
    for uid in list(open_rides) + [u for u in last_seen if u not in open_rides]:
        ride = open_rides.get(uid)
        charge = charge_by_id.get(ride.get("charge_id")) if ride else None
        checks = [check_pass(p, line, today) for p in passes_by_user.get(uid, [])]
        v = verdict(ride, charge, checks)
        level, label = VERDICTS[v]
        best = next((p for p in checks if p.valid_now and p.covers_line), None) or next(iter(checks), None)
        u = who.get(uid, {})
        rows.append({
            "_level": level,
            "Status": f"{LEVEL[level][0]} {label}",
            "Name": u.get("name") or ("(anonymous)" if u.get("tier") == "anonymous" else "—"),
            "Email": u.get("email") or "—",
            "Ride": (ride.get("status") or "—").replace("_", " ") if ride else "no ride (phone heard the bus)",
            "Passengers": ride.get("passengers") if ride else 1,
            "Paid": (f"{charge.get('amount')} {charge.get('currency') or ''} · "
                     f"{charge.get('source_kind') or 'card'}").strip() if charge and charge.get("status") in PAID_CHARGE
                    else ("on a pass" if charge and charge.get("status") == "covered_by_pass" else "—"),
            "Pass": _pass_text(best, line) if best else "none",
            "Heard": fmt_age((now - last_seen[uid]).total_seconds()) if uid in last_seen else "—",
        })

    order = {"fail": 0, "check": 1, "ok": 2}
    rows.sort(key=lambda r: order[r["_level"]])
    n = {lvl: sum(r["_level"] == lvl for r in rows) for lvl in order}
    m = st.columns(4)
    m[0].metric("People on board", sum(int(r["Passengers"] or 1) for r in rows))
    m[1].metric("❌ To check", n["fail"])
    m[2].metric("⚠️ Unclear", n["check"])
    m[3].metric("✅ OK", n["ok"])
    if not rows:
        st.info(f"Nobody on bus {bus_id}: no open ride and no phone heard its beacon in the last {seen_min} min.")
        return

    df = pd.DataFrame(rows)
    levels = df.pop("_level")
    styled = df.style.apply(lambda r: [f"background-color: {LEVEL[levels[r.name]][2]}; color: #1a1a19"] * len(r),
                            axis=1)
    st.dataframe(styled, hide_index=True, width="stretch", height=(min(len(df), 12) + 1) * 35 + 18)
    st.caption(f"Line {line}. Red first. Updated {now.astimezone(tz):%H:%M:%S}. People without a ride were found "
               f"by their phone hearing this bus in the last {seen_min} min. Passengers counts a group ride once "
               "per person paid for.")
