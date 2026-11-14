from __future__ import annotations

import time
from datetime import datetime
from collections import defaultdict
from typing import Callable, TypeVar

import numpy as np
import streamlit as st

from ..api import (
    ApiClient, assemble_bus_snapshot, build_client, fetch_many, get_bus_live, get_bus_photo, get_bus_setup,
    get_capture_interval, get_route_positions, get_telemetry_history, list_bus_devices, list_buses, list_frames,
    list_all_records, list_latest_telemetry, list_records, list_reports, get_record, server_up,
)
from ..api.errors import CONNECTION_LOST, user_message
from ..gtfs import Feed
from ..linking import RouteLink, match_routes

T = TypeVar("T")
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


def server_up_now() -> bool:
    return _server_up()


def require_server() -> None:
    """At the top of every self-updating part: if the server stopped answering, rerun the whole app, which
    then shows the 'connection lost' banner instead of making calls that would all fail."""
    if not _server_up():
        st.rerun(scope="app")


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
@st.cache_resource(ttl=0.5, show_spinner=False)
def route_positions(api_route_id: int):
    return _safe(lambda: get_route_positions(api_client(), api_route_id))


@st.cache_resource(ttl=30, show_spinner=False)
def reports(line: str):
    return _safe(lambda: list_reports(api_client(), line=line))


@st.cache_resource(ttl=60, show_spinner=False)
def buses():
    return _safe(lambda: list_buses(api_client()))


@st.cache_resource(ttl=0.5, show_spinner=False)
def _bus_live(bus_id: int, history_limit: int):
    return get_bus_live(api_client(), bus_id, history_limit=history_limit)


@st.cache_resource(ttl=30, show_spinner=False)
def _bus_setup(bus_id: int):
    return get_bus_setup(api_client(), bus_id)


@st.cache_resource(ttl=60, max_entries=50, show_spinner=False)
def _bus_photo(bus_id: int, slot: int):
    """`slot` changes once per capture interval, so the photo is fetched at the camera's pace."""
    return get_bus_photo(api_client(), bus_id)


def bus_snapshot(bus_id: int, history_limit: int, *, photo_every_s: float = 5.0):
    """Everything about the selected bus. Position and passengers are fetched on every update; the device
    list every 30 s; the photo at the camera's own pace: the capture interval the server tells the camera
    after each photo (GET /buses/{id}/capture-interval), or `photo_every_s` when the server gives none."""
    def build():
        setup = _bus_setup(bus_id)
        interval = setup[0].get("capture_interval")
        every_s = interval.next_capture_ms / 1000 if interval and interval.next_capture_ms else photo_every_s
        slot = int(time.time() // max(every_s, 0.5))
        return assemble_bus_snapshot(bus_id, _bus_live(bus_id, history_limit), setup, _bus_photo(bus_id, slot))
    return _safe(build)


# ---------------------------------------------------------------- devices page
@st.cache_resource(ttl=300, show_spinner="Loading the device list...")
def fleet_devices(bus_ids: tuple[int, ...]):
    """bus id -> (devices, error). The API has no list of all devices, so this is one call per bus
    (in parallel); devices rarely change, so it is kept for 5 minutes."""
    return fetch_many(lambda bid: list_bus_devices(api_client(), bid), bus_ids)


@st.cache_resource(ttl=5, show_spinner=False)
def latest_telemetry():
    """Newest message of every bus: one call."""
    return _safe(lambda: list_latest_telemetry(api_client()))


@st.cache_resource(ttl=5, show_spinner=False)
def latest_frames(bus_ids: tuple[int, ...]):
    """bus id -> (newest frame list, error), one call per bus with a camera (in parallel)."""
    return fetch_many(lambda bid: list_frames(api_client(), bid, limit=1), bus_ids)


@st.cache_resource(ttl=60, show_spinner=False)
def capture_interval(bus_id: int):
    return _safe(lambda: get_capture_interval(api_client(), bus_id))


# ---------------------------------------------------------------- users page
USER_ACTIVITY = ("rides", "charges", "trips", "passes", "sightings")
LIST_LIMIT = 1000   # the most a list call gives


@st.cache_resource(ttl=5, show_spinner=False)
def users():
    return _safe(lambda: list_records(api_client(), "users", limit=LIST_LIMIT))


@st.cache_resource(ttl=3, show_spinner=False)
def user(user_id: str):
    return _safe(lambda: get_record(api_client(), "user", user_id=user_id))


@st.cache_resource(ttl=3, show_spinner=False)
def user_activity(user_id: str):
    """name -> (records, error) for the user's rides, charges, trips, passes and sightings, in parallel."""
    return fetch_many(lambda name: list_records(api_client(), name, user_id=user_id, limit=LIST_LIMIT), USER_ACTIVITY)


@st.cache_resource(ttl=3, show_spinner=False)
def ride(session_id: str):
    return _safe(lambda: get_record(api_client(), "ride", session_id=session_id))


# ---------------------------------------------------------------- money page
@st.cache_resource(ttl=5, max_entries=20, show_spinner=False)   # under the 10 s update; no spinner flashing
def charges(start_iso: str | None, end_iso: str | None):
    """Every charge started in [start, end). from/to go to the server (it filters once that is built); the
    money page filters the dates again, so the numbers are right either way."""
    return _safe(lambda: list_all_records(api_client(), "charges", start=start_iso, end=end_iso))


@st.cache_resource(ttl=300, show_spinner=False)
def pass_catalog():
    return _safe(lambda: list_records(api_client(), "pass_catalog"))


# ---------------------------------------------------------------- line health page
@st.cache_resource(ttl=20, max_entries=10, show_spinner=False)
def recent_history(bus_ids: tuple[int, ...], start_iso: str):
    """bus id -> (positions received since `start_iso`, error); one call per bus, in parallel."""
    start = datetime.fromisoformat(start_iso.replace("Z", "+00:00"))   # text as the cache key, datetime for the call
    return fetch_many(lambda bid: get_telemetry_history(api_client(), bid, start=start, limit=1000), bus_ids)


@st.cache_resource(ttl=20, show_spinner=False)
def all_reports():
    return _safe(lambda: list_reports(api_client()))


def clear() -> None:
    for fn in (route_positions, reports, buses, _bus_live, _bus_setup, _bus_photo,
               fleet_devices, latest_telemetry, latest_frames, capture_interval, users, user, user_activity, ride,
               charges, pass_catalog, recent_history, all_reports):
        fn.clear()
    # the route link is left alone: it is rebuilt every 10 minutes on its own
