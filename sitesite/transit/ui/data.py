from __future__ import annotations

import time
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from collections import defaultdict
from typing import Callable, TypeVar

import numpy as np
import streamlit as st

from ..api import (
    ApiClient, assemble_bus_snapshot, build_client, fetch_many, get_bus_photo, get_bus_setup, get_occupancy,
    get_capture_interval, get_route_positions, get_telemetry_history, list_bus_devices, list_buses, list_frames,
    list_all_records, list_latest_telemetry, list_records, list_reports, get_record, server_up,
    get_headcount, Occupancy,
)
from ..api.errors import CONNECTION_LOST, transport_failed_within, user_message
from ..config import Settings
from ..gtfs import Feed
from ..linking import RouteLink, match_routes
from .history import HistoryStore

T = TypeVar("T")

# How long cached data is kept. A cache read by a page that updates every N s keeps its data N/2 s: each
# update gets fresh data, and viewers (and parts of one page) at the same moment share one call. The intervals
# come from config.Settings (the same values the sidebar shows), so they're set in one place.
_S = Settings.from_env()
CAMERA_S = 5          # the camera sends (and the server counts) a photo about every 5 s
BUS_SETUP_S = 30      # a bus's devices and capture interval, users' names, passes: rarely change
BUS_LIST_S = 60       # the list of buses
REPORTS_S = 30        # a line's reports (the Map page's Reports tab)
DEVICE_LIST_S = 300   # the devices of every bus, the pass catalog: change almost never
LINK_SAMPLES_PER_BUS = 30


# Every cache here is st.cache_resource: results are kept as they are, not pickled. Nothing changes them, and
# pickling breaks when Streamlit reloads a changed module while the app runs (the cached objects' class is
# then "not the same object" as the reloaded one). It also saves pickling the photo every second.
@st.cache_resource
def api_client() -> ApiClient:
    return build_client()


def _safe(fn: Callable[[], T]) -> tuple[T | None, str | None]:
    try:
        return fn(), None
    except Exception as exc:  # UI boundary: a plain message instead of breaking the page (detail in the log)
        return None, user_message(exc)


# ---------------------------------------------------------------- connection
@st.cache_resource(ttl=1, show_spinner=False)
def _server_up() -> bool:
    return server_up(api_client())   # at most one /health a second, shared by every page and viewer


RECHECK_AFTER_FAILURE_S = 10   # /health is only asked while a real call failed this recently


def server_up_now() -> bool:
    """Normally True without any call: /health is only asked when a real call got no answer in the last
    RECHECK_AFTER_FAILURE_S (then once a second at most, shared by everyone)."""
    return _server_up() if transport_failed_within(RECHECK_AFTER_FAILURE_S) else True


def require_server() -> None:
    """At the top of every self-updating part: if the server stopped answering, rerun the whole app, which
    then shows the 'connection lost' banner instead of making calls that would all fail."""
    if not server_up_now():
        st.rerun(scope="app")
    from . import nav   # every self-updating part calls this: a pick made inside one goes to the URL too
    nav.write()


def render_connection_lost() -> None:
    """The page while the server is down: one banner, /health checked every second. When it answers, the
    cached failures are dropped and the whole app reruns with its normal calls."""
    @st.fragment(run_every=1)
    def wait() -> None:
        if _server_up():
            clear()
            st.rerun(scope="app")
        st.warning(f"{CONNECTION_LOST} Checking every second (last check "
                   f"{datetime.now().strftime('%H:%M:%S')}). The page comes back by itself.")
    wait()


# ---------------------------------------------------------------- GTFS line <-> API route
def _route_samples() -> dict[int, np.ndarray]:
    """Recent positions of every bus, grouped by API route."""
    api = api_client()
    buses = list_buses(api) or []
    route_of = {b.bus_id: b.route_id for b in buses}
    history = fetch_many(lambda bid: get_telemetry_history(api, bid, limit=LINK_SAMPLES_PER_BUS), route_of)
    points: dict[int, list[tuple[float, float]]] = defaultdict(list)
    for bid, (rows, _err) in history.items():
        for t in rows or []:
            rid = t.route_id if t.route_id is not None else route_of[bid]
            if rid is not None and t.latlon:
                points[rid].append(t.latlon)
    return {rid: np.array(p) for rid, p in points.items()}


@st.cache_resource(ttl=600, show_spinner="Matching the server's routes to the GTFS lines...")
def _route_link(gtfs_key: str, _feed: Feed) -> RouteLink:
    return match_routes(_feed, _route_samples())   # an exception is not cached: the next run tries again


def route_link(gtfs_key: str, feed: Feed) -> tuple[RouteLink, str | None]:
    try:
        return _route_link(gtfs_key, feed), None
    except Exception as exc:
        return RouteLink(), user_message(exc)


# ---------------------------------------------------------------- map and bus panel
# ttl under the 1 s update period: every update gets fresh positions
@st.cache_resource(ttl=_S.live_refresh_s / 2, show_spinner=False)
def route_positions(api_route_id: int):
    return _safe(lambda: get_route_positions(api_client(), api_route_id))


@st.cache_resource(ttl=REPORTS_S, show_spinner=False)
def reports(line: str):
    return _safe(lambda: list_reports(api_client(), line=line))


@st.cache_resource(ttl=BUS_LIST_S, show_spinner=False)
def buses():
    return _safe(lambda: list_buses(api_client()))


@st.cache_resource
def _history_store() -> HistoryStore:
    return HistoryStore()


BUS_HISTORY_WINDOW = timedelta(minutes=30)   # what the selected bus's trail, next stop and ETA are drawn from


def history(bus_ids, start: datetime, max_age_s: float):
    """bus id -> (positions since `start`, newest first; error), from the shared incremental store."""
    return _history_store().get(api_client(), tuple(bus_ids), start, max_age_s)


@st.cache_resource(ttl=CAMERA_S, show_spinner=False)
def _occupancy(bus_id: int):
    """Passengers: changes only when a ride is confirmed or ends, so every 5 s is enough."""
    return _safe(lambda: get_occupancy(api_client(), bus_id))


@st.cache_resource(ttl=CAMERA_S, show_spinner=False)
def _headcount(bus_id: int):
    return _safe(lambda: get_headcount(api_client(), bus_id))


def headcount(bus_id: int):
    """Camera count vs paid passengers (GET /buses/{id}/headcount, the photo is counted about every 5 s).
    A server that leaves out paid_passengers / without_ticket (an older build does): filled in here from the
    occupancy call (the paid passengers) and the camera count."""
    hc, err = _headcount(bus_id)
    if hc is None or hc.paid_passengers is not None:
        return hc, err
    occ, oerr = _occupancy(bus_id)
    paid = occ.passenger_count if occ else None
    missing = None if paid is None or hc.people_counted is None else max(0, hc.people_counted - paid)
    return replace(hc, paid_passengers=paid, without_ticket=missing), err or oerr


@st.cache_resource(ttl=BUS_SETUP_S, show_spinner=False)
def _bus_setup(bus_id: int):
    return get_bus_setup(api_client(), bus_id)


@st.cache_resource(ttl=60, max_entries=50, show_spinner=False)
def _bus_photo(bus_id: int, slot: int):
    """`slot` changes once per capture interval, so the photo is fetched at the camera's pace."""
    return get_bus_photo(api_client(), bus_id)


def bus_snapshot(bus_id: int, history_limit: int, *, photo_every_s: float = 5.0):
    """Everything about the selected bus. Position and trail come from the shared history (new rows about once
    a second); passengers every 5 s; the device list every 30 s; the photo at the camera's own pace: the capture interval the server tells the camera
    after each photo (GET /buses/{id}/capture-interval), or `photo_every_s` when the server gives none."""
    def build():
        setup = _bus_setup(bus_id)
        interval = setup[0].get("capture_interval")
        every_s = interval.next_capture_ms / 1000 if interval and interval.next_capture_ms else photo_every_s
        slot = int(time.time() // max(every_s, 0.5))
        # position and trail from the shared history (only new rows are fetched, about once a second); the
        # position is the newest one with GPS, so no separate /position call is needed
        rows, herr = history([bus_id], datetime.now(timezone.utc) - BUS_HISTORY_WINDOW, max_age_s=_S.live_refresh_s * 0.8)[bus_id]
        position = next((t for t in rows if t.latlon), rows[0] if rows else None)
        # passengers from the headcount call (it also gives the camera's count); an older server without it:
        # the occupancy call instead
        hc, oerr = headcount(bus_id)
        if hc is not None and hc.paid_passengers is not None:
            occupancy = Occupancy(bus_id, hc.paid_passengers)
        else:
            occupancy, oerr = _occupancy(bus_id)
        live = ({"position": position, "occupancy": occupancy, "history": rows[:history_limit], "headcount": hc},
                {k: e for k, e in (("history", herr), ("position", herr), ("occupancy", oerr)) if e})
        return assemble_bus_snapshot(bus_id, live, setup, _bus_photo(bus_id, slot))
    return _safe(build)


# ---------------------------------------------------------------- devices page
@st.cache_resource(ttl=DEVICE_LIST_S, show_spinner="Loading the device list...")
def fleet_devices(bus_ids: tuple[int, ...]):
    """bus id -> (devices, error). The API has no list of all devices, so this is one call per bus
    (in parallel); devices rarely change, so it is kept for 5 minutes."""
    return fetch_many(lambda bid: list_bus_devices(api_client(), bid), bus_ids)


@st.cache_resource(ttl=min(_S.devices_list_refresh_s, _S.fleet_refresh_s) / 2, show_spinner=False)
def latest_telemetry():
    """Newest message of every bus: one call."""
    return _safe(lambda: list_latest_telemetry(api_client()))


@st.cache_resource(ttl=_S.fleet_refresh_s / 2, show_spinner=False)
def latest_frames(bus_ids: tuple[int, ...]):
    """bus id -> (its newest photo's metadata (time, sequence; no image), error), one call per bus, in parallel.
    Used by the Fleet page to tell cameras that went quiet."""
    return fetch_many(lambda bid: list_frames(api_client(), bid, limit=1), bus_ids)


@st.cache_resource(ttl=BUS_SETUP_S, show_spinner=False)
def capture_interval(bus_id: int):
    return _safe(lambda: get_capture_interval(api_client(), bus_id))


# ---------------------------------------------------------------- users page
USER_ACTIVITY = ("rides", "charges", "trips", "passes", "sightings")
LIST_LIMIT = 1000   # the most a list call gives


USERS_PAGE = 50   # the Users page loads this many, and this many more on "Show more"


@st.cache_resource(ttl=_S.devices_list_refresh_s / 2, max_entries=20, show_spinner=False)
def users(limit: int = USERS_PAGE, tier: str | None = None):
    """The newest `limit` users (of a tier), one call: the page asks for more as you click "Show more"."""
    return _safe(lambda: list_records(api_client(), "users", limit=min(limit, LIST_LIMIT), tier=tier))


@st.cache_resource(ttl=BUS_SETUP_S, show_spinner=False)
def users_by_id(user_ids: tuple[str, ...]):
    """user id -> (user, error) for just these people, in parallel."""
    return fetch_many(lambda uid: get_record(api_client(), "user", user_id=uid), user_ids)


@st.cache_resource(ttl=_S.users_refresh_s / 2, show_spinner=False)
def user(user_id: str):
    return _safe(lambda: get_record(api_client(), "user", user_id=user_id))


@st.cache_resource(ttl=_S.users_refresh_s / 2, show_spinner=False)
def user_activity(user_id: str):
    """name -> (records, error) for the user's rides, charges, trips, passes and sightings, in parallel."""
    return fetch_many(lambda name: list_records(api_client(), name, user_id=user_id, limit=LIST_LIMIT), USER_ACTIVITY)


@st.cache_resource(ttl=_S.users_refresh_s / 2, show_spinner=False)
def ride(session_id: str):
    return _safe(lambda: get_record(api_client(), "ride", session_id=session_id))


# ---------------------------------------------------------------- money page
@st.cache_resource(ttl=_S.money_refresh_s / 2, max_entries=20, show_spinner=False)   # no spinner flashing
def charges(start_iso: str | None, end_iso: str | None):
    """Every charge started in [start, end). from/to go to the server (it filters once that is built); the
    money page filters the dates again, so the numbers are right either way."""
    return _safe(lambda: list_all_records(api_client(), "charges", start=start_iso, end=end_iso))


@st.cache_resource(ttl=DEVICE_LIST_S, show_spinner=False)
def pass_catalog():
    return _safe(lambda: list_records(api_client(), "pass_catalog"))


# ---------------------------------------------------------------- line health page
def recent_history(bus_ids: tuple[int, ...], start_iso: str):
    """bus id -> (positions received since `start_iso`, newest first; error), from the shared store: only what's
    new since the last update is downloaded."""
    start = datetime.fromisoformat(start_iso.replace("Z", "+00:00"))
    return history(bus_ids, start, max_age_s=min(_S.health_refresh_s, _S.sightings_refresh_s) / 2)


@st.cache_resource(ttl=_S.health_refresh_s / 2, show_spinner=False)
def all_reports():
    return _safe(lambda: list_reports(api_client()))


# ---------------------------------------------------------------- sightings page
@st.cache_resource(ttl=_S.sightings_refresh_s / 2, show_spinner=False)
def sightings_all():
    """Every sighting the server kept (it keeps the newest 20000): newest first, read 1000 at a time. There is
    no date filter on /sightings, so the page cuts the window itself."""
    return _safe(lambda: list_all_records(api_client(), "sightings", max_pages=20))


# ---------------------------------------------------------------- inspector page
@st.cache_resource(ttl=_S.inspector_refresh_s / 2, show_spinner=False)
def bus_rides(bus_id: int):
    return _safe(lambda: list_all_records(api_client(), "rides", bus_id=bus_id, max_pages=2))


@st.cache_resource(ttl=_S.inspector_refresh_s / 2, show_spinner=False)
def bus_sightings(bus_id: int):
    """The newest sightings of this bus's beacon (newest first)."""
    return _safe(lambda: list_records(api_client(), "sightings", bus_id=bus_id, limit=1000))


@st.cache_resource(ttl=_S.inspector_refresh_s / 2, show_spinner=False)
def charges_of(user_ids: tuple[str, ...]):
    """user id -> (their charges, error): only the people being looked at, in parallel."""
    return fetch_many(lambda uid: list_records(api_client(), "charges", user_id=uid, limit=LIST_LIMIT), user_ids)


@st.cache_resource(ttl=BUS_SETUP_S, show_spinner=False)
def all_passes():
    return _safe(lambda: list_all_records(api_client(), "passes"))


def clear() -> None:
    for fn in (route_positions, reports, buses, _occupancy, _headcount, _bus_setup, _bus_photo,
               fleet_devices, latest_telemetry, latest_frames, capture_interval, users, users_by_id, user, user_activity, ride,
               charges, charges_of, pass_catalog, all_reports, sightings_all, bus_rides, bus_sightings,
               all_passes):
        fn.clear()
    # the route link is left alone: it is rebuilt every 10 minutes on its own
