"""Navigation with the browser's own Back / Forward buttons.

A "place" is the page and what's picked on it (line, bus, device, user, ...). It lives in the URL's query.
- When the place changes in the app, the new place is written to the URL. Streamlit then adds a browser history
  entry (it uses history.pushState for a query change), so the browser's Back has somewhere to go.
- When the browser's Back / Forward changes the URL, Streamlit doesn't rerun a single-page app by itself. An
  invisible helper (nav_listener/index.html) hears the browser's "popstate" and sends the new URL to the app,
  which reruns; the app then opens the place in that URL.
- On the first load (a refresh, or a shared link), the place in the URL is opened too.
"""
from __future__ import annotations

from pathlib import Path
from urllib.parse import parse_qs, urlencode

import streamlit as st
import streamlit.components.v1 as components

from . import state

_listener = components.declare_component("transit_nav_listener", path=str(Path(__file__).parent / "nav_listener"))

# session key -> URL parameter. Devices are kept as (device_id, bus_id): "id@bus" in the URL.
PLACE = {
    state.K_VIEW: "view",
    state.K_ROUTE: "line",
    state.K_VEHICLE: "bus",
    "selected_device": "device",
    "selected_user": "user",
    "health_line": "hline",
    "insp_bus": "ibus",
}
K_PLACE = "nav_place"       # the current place, as last written to (or opened from) the URL
K_MEMORY, K_LAST_VIEW = "nav_page_memory", "nav_last_view"

# Each page's own settings (windows, filters, search text, toggles): kept when you leave the page and put back
# when you return to it (browser Back / Forward or the View dropdown). They live in this browser tab's session,
# so a page opened fresh (typed URL, refresh, a link) starts with the defaults.
PAGE_SETTINGS = {
    "map": [state.K_ROUTE, state.K_SOURCE, state.K_SIM_ON, state.K_SIM_HOUR, state.K_SIM_MIN],
    "fleet": [],
    "health": ["health_window", "health_all_lines"],
    "devices": ["device_search", "device_role", "device_status"],
    "sightings": ["sight_window", "sight_show", "sight_line", "sight_only_line"],
    "inspector": ["insp_seen"],
    "users": ["user_search", "user_tier", "user_riding_only", "users_loaded", "users_tier_loaded"],
    "money": ["money_period", "money_range"],
}
K_POP_SEEN = "nav_pop_seen"  # the last Back / Forward already handled
K_LISTENER = "nav_listener"  # the helper (its value: the last Back / Forward)
K_OPS, K_NONCE, K_SENT = "nav_ops", "nav_nonce", "nav_sent"   # history steps waiting for / sent to the helper


def _encode(key: str, value) -> str | None:
    if value in (None, "", ()):
        return None
    if key == "selected_device":
        device_id, bus_id = value
        return f"{device_id}@{bus_id}"
    return str(value)


def _decode(key: str, text: str):
    if key == "selected_device":
        device_id, _, bus = text.partition("@")
        return (device_id, int(bus)) if bus.isdigit() else (device_id, None)
    if key == "insp_bus":
        return int(text) if text.isdigit() else None
    return text


def _current() -> dict[str, str]:
    """The place in the session, as URL parameters (empty ones left out)."""
    ss = st.session_state
    out = {}
    for key, param in PLACE.items():
        text = _encode(key, ss.get(key))
        if text is not None:
            out[param] = text
    return out


def _url() -> dict[str, str]:
    return {k: v for k, v in st.query_params.to_dict().items() if k in PLACE.values()}


def _open(place: dict[str, str], valid_views, valid_routes) -> None:
    """Make `place` the current one. Only before the widgets of this run exist (start of a full run)."""
    ss = st.session_state
    if place.get("view") not in valid_views:
        place = {**place, "view": ss.get(state.K_VIEW) or "map"}
    if place.get("line") and place["line"] not in valid_routes:
        place = {k: v for k, v in place.items() if k != "line"}
    for key, param in PLACE.items():
        if param in place:
            ss[key] = _decode(key, place[param])
        elif key == state.K_ROUTE:
            ss[key] = ""
        elif key != state.K_VIEW:
            ss[key] = None


def _popped() -> dict[str, str] | None:
    """The URL the browser's Back / Forward went to, if that happened since the last run; else None. Read from
    the helper's last value in the session (the helper itself is drawn at the end of sync)."""
    ev = st.session_state.get(K_LISTENER)
    ss = st.session_state
    if not ev or ev.get("at") == ss.get(K_POP_SEEN):
        return None
    ss[K_POP_SEEN] = ev.get("at")
    query = parse_qs(str(ev.get("search") or "").lstrip("?"))
    return {k: v[-1] for k, v in query.items() if k in PLACE.values()}


def sync(valid_views, valid_routes) -> None:
    """At the start of every full run, before the widgets.
    - the browser's Back / Forward (reported by the helper): open the place in that URL;
    - the first run of a browser session (a refresh, or a link): open the place in the URL, if it names one;
    - otherwise: if the app moved, put the new place in the URL (see write).
    Ends by drawing the invisible helper, which carries any in-place URL update to the browser."""
    ss = st.session_state
    _remember_page()   # the page being shown (or just left) keeps its settings, before anything changes them
    popped = _popped()
    if popped is not None:
        _open(popped, valid_views, valid_routes)
        _bring_back_page(from_url=True)
        ss[K_PLACE] = _current()   # already in the browser's URL: nothing to write
    elif K_PLACE not in ss:
        url = _url()
        if url:
            _open(url, valid_views, valid_routes)
        ss[K_LAST_VIEW] = ss.get(state.K_VIEW)   # a fresh page: nothing to bring back
        ss[K_PLACE] = _current()
        if url != ss[K_PLACE]:
            _queue("replace", ss[K_PLACE])      # tidy the URL in place: the page just opened adds no entry
    else:
        _bring_back_page()
        write()
    _send_ops()


def _remember_page() -> None:
    """Keep the settings of the page shown last (they're still in the session at the start of this run)."""
    ss = st.session_state
    view = ss.get(K_LAST_VIEW)
    if view in PAGE_SETTINGS:
        saved = ss.setdefault(K_MEMORY, {}).setdefault(view, {})
        for key in PAGE_SETTINGS[view]:
            if key in ss:
                saved[key] = ss[key]


def _bring_back_page(from_url: bool = False) -> None:
    """Entering another page: put back the settings it had when it was left. Settings always come back; a place
    field (the Map's line) only when nothing names one, and never on Back / Forward (the URL decides then)."""
    ss = st.session_state
    view = ss.get(state.K_VIEW)
    if view != ss.get(K_LAST_VIEW):
        for key, value in ss.get(K_MEMORY, {}).get(view, {}).items():
            if key in PLACE:
                if not from_url and not ss.get(key) and value:
                    ss[key] = value
            else:
                ss[key] = value
    ss[K_LAST_VIEW] = view


def write() -> None:
    """If the place changed in the app, queue its URL for the helper. The same page with another line, bus,
    device...: replace the URL in place (no history entry). Another page: bring the old page's entry up to date
    (a pick made in a self-updating part may not have reached it yet), then add an entry for the new page: that's
    what the browser's Back / Forward step through. Also called by the self-updating parts; their steps reach the
    browser with the next full run, in order."""
    ss = st.session_state
    now = _current()
    before = ss.get(K_PLACE)
    if now == before:
        return
    ss[K_PLACE] = now
    if before is not None and now.get("view") != before.get("view"):
        _queue("replace", before)
        _queue("push", now)
    else:
        _queue("replace", now)


def _queue(op: str, place: dict[str, str]) -> None:
    st.session_state.setdefault(K_OPS, []).append([op, urlencode(place)])


def _send_ops() -> None:
    """Draw the helper with the queued history steps (each batch carries a new number, so it's done once)."""
    ss = st.session_state
    ops = ss.pop(K_OPS, [])
    if ops:
        ss[K_NONCE] = ss.get(K_NONCE, 0) + 1
        ss[K_SENT] = {"ops": ops, "nonce": ss[K_NONCE]}
    _listener(key=K_LISTENER, default=None, **ss.get(K_SENT, {"ops": None, "nonce": None}))
