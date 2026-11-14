from __future__ import annotations

from collections import defaultdict
from typing import Callable, TypeVar

import numpy as np
import streamlit as st

from ..api import (
    ApiClient, build_client, fetch_many, get_bus_snapshot, get_capture_interval, get_route_positions,
    get_telemetry_history, list_bus_devices, list_buses, list_frames, list_latest_telemetry, list_reports,
)
from ..gtfs import Feed
from ..linking import RouteLink, match_routes

T = TypeVar("T")
LINK_SAMPLES_PER_BUS = 30


@st.cache_resource
def api_client() -> ApiClient:
    return build_client()


def _safe(fn: Callable[[], T]) -> tuple[T | None, str | None]:
    try:
        return fn(), None
    except Exception as exc:  # UI boundary: show the error instead of breaking the page
        return None, f"{type(exc).__name__}: {exc}"


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
        return RouteLink(), f"{type(exc).__name__}: {exc}"


# ---------------------------------------------------------------- map and bus panel
@st.cache_data(ttl=5, show_spinner=False)
def route_positions(api_route_id: int):
    return _safe(lambda: get_route_positions(api_client(), api_route_id))


@st.cache_data(ttl=30, show_spinner=False)
def reports(line: str):
    return _safe(lambda: list_reports(api_client(), line=line))


@st.cache_data(ttl=60, show_spinner=False)
def buses():
    return _safe(lambda: list_buses(api_client()))


@st.cache_data(ttl=5, show_spinner=False)
def bus_snapshot(bus_id: int, history_limit: int):
    """Everything about the selected bus, fetched in parallel."""
    return _safe(lambda: get_bus_snapshot(api_client(), bus_id, history_limit=history_limit))


# ---------------------------------------------------------------- devices page
@st.cache_data(ttl=300, show_spinner="Loading the device list...")
def fleet_devices(bus_ids: tuple[int, ...]):
    """bus id -> (devices, error). The API has no list of all devices, so this is one call per bus
    (in parallel); devices rarely change, so it is kept for 5 minutes."""
    return fetch_many(lambda bid: list_bus_devices(api_client(), bid), bus_ids)


@st.cache_data(ttl=10, show_spinner=False)
def latest_telemetry():
    """Newest message of every bus: one call."""
    return _safe(lambda: list_latest_telemetry(api_client()))


@st.cache_data(ttl=15, show_spinner=False)
def latest_frames(bus_ids: tuple[int, ...]):
    """bus id -> (newest frame list, error), one call per bus with a camera (in parallel)."""
    return fetch_many(lambda bid: list_frames(api_client(), bid, limit=1), bus_ids)


@st.cache_data(ttl=60, show_spinner=False)
def capture_interval(bus_id: int):
    return _safe(lambda: get_capture_interval(api_client(), bus_id))


def clear() -> None:
    for fn in (route_positions, reports, buses, bus_snapshot,
               fleet_devices, latest_telemetry, latest_frames, capture_interval):
        fn.clear()
    # the route link is left alone: it is rebuilt every 10 minutes on its own
