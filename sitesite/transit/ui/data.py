from __future__ import annotations

from typing import Callable, TypeVar

import streamlit as st

from ..api import (
    ApiClient, build_client, get_bus_info, get_latest_frame, get_route_positions,
    get_telemetry_history, list_buses, list_reports,
)

T = TypeVar("T")


@st.cache_resource
def api_client() -> ApiClient:
    return build_client()


def _safe(fn: Callable[[], T]) -> tuple[T | None, str | None]:
    try:
        return fn(), None
    except Exception as exc:  # UI boundary: show the error instead of breaking the page
        return None, f"{type(exc).__name__}: {exc}"


@st.cache_data(ttl=5, show_spinner=False)
def route_positions(api_route_id: int):
    return _safe(lambda: get_route_positions(api_client(), api_route_id))


@st.cache_data(ttl=5, show_spinner=False)
def bus_info(bus_id: int):
    return _safe(lambda: get_bus_info(api_client(), bus_id, with_frame=False))


@st.cache_data(ttl=10, show_spinner=False)
def latest_frame(bus_id: int):
    return _safe(lambda: get_latest_frame(api_client(), bus_id))


@st.cache_data(ttl=10, show_spinner=False)
def telemetry_history(bus_id: int, limit: int):
    return _safe(lambda: get_telemetry_history(api_client(), bus_id, limit=limit))


@st.cache_data(ttl=30, show_spinner=False)
def reports(line: str):
    return _safe(lambda: list_reports(api_client(), line=line))


@st.cache_data(ttl=60, show_spinner=False)
def buses():
    return _safe(lambda: list_buses(api_client()))


def clear() -> None:
    for fn in (route_positions, bus_info, latest_frame, telemetry_history, reports, buses):
        fn.clear()