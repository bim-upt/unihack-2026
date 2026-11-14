from __future__ import annotations

from datetime import datetime
from typing import Callable, TypeVar

import requests

from .client import ApiClient
from .errors import ApiError, AuthNotConfigured, TransportError
from .models import (
    Bus, BusInfo, CaptureInterval, Device, Frame, FrameImage, Occupancy, PassProduct, Position, Report, Telemetry,
)

T = TypeVar("T")
TimeArg = datetime | int | float | None


def _get(api: ApiClient, name: str, parse: Callable[[requests.Response], T], *,
         path: dict | None = None, query: dict | None = None) -> T | None:
    if not api.has(name):
        return None
    try:
        return parse(api.call(name, path=path, query=query))
    except ApiError as exc:
        if exc.status == 404:
            return None
        raise


def _items(payload) -> list:
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("items", "data", "results"):
            if isinstance(payload.get(key), list):
                return payload[key]
    return []


def _list(api: ApiClient, name: str, model, **kw) -> list | None:
    return _get(api, name, lambda r: [model.from_json(x) for x in _items(r.json())], **kw)


# ---------------------------------------------------------------- devices
def list_bus_devices(api: ApiClient, bus_id: int) -> list[Device] | None:
    return _list(api, "bus_devices", Device, path={"bus_id": int(bus_id)})


def get_device(api: ApiClient, device_id: str) -> Device | None:
    return _get(api, "device", lambda r: Device.from_json(r.json()), path={"device_id": device_id})


# ---------------------------------------------------------------- buses and positions
def list_buses(api: ApiClient) -> list[Bus] | None:
    return _list(api, "buses", Bus)


def get_telemetry_history(api: ApiClient, bus_id: int, start: TimeArg = None, end: TimeArg = None,
                          limit: int | None = None) -> list[Telemetry] | None:
    query = {"start": api.time_value(start), "end": api.time_value(end), "limit": limit}
    return _list(api, "bus_telemetry", Telemetry, path={"bus_id": int(bus_id)}, query=query)


def get_bus_position(api: ApiClient, bus_id: int) -> Telemetry | None:
    return _get(api, "bus_position", lambda r: Telemetry.from_json(r.json()), path={"bus_id": int(bus_id)})


def get_route_positions(api: ApiClient, route_id: int) -> list[Position] | None:
    return _list(api, "route_positions", Position, query={"route_id": int(route_id)})


# ---------------------------------------------------------------- camera
def list_frames(api: ApiClient, bus_id: int, limit: int | None = None, before=None) -> list[Frame] | None:
    return _list(api, "bus_frames", Frame, path={"bus_id": int(bus_id)}, query={"limit": limit, "before": before})


def get_latest_frame(api: ApiClient, bus_id: int) -> FrameImage | None:
    return _get(api, "bus_frame_latest", lambda r: FrameImage.from_response(int(bus_id), r),
                path={"bus_id": int(bus_id)})


def get_capture_interval(api: ApiClient, bus_id: int) -> CaptureInterval | None:
    return _get(api, "capture_interval", lambda r: CaptureInterval.from_json(r.json()), path={"bus_id": int(bus_id)})


# ---------------------------------------------------------------- passengers
def get_occupancy(api: ApiClient, bus_id: int) -> Occupancy | None:
    return _get(api, "occupancy", lambda r: Occupancy.from_json(r.json()), path={"bus_id": int(bus_id)})


# ---------------------------------------------------------------- reports and passes
def list_reports(api: ApiClient, line: str | None = None, kind: str | None = None) -> list[Report] | None:
    reports = _list(api, "reports", Report, query={"line": line, "kind": kind})
    if reports is None:
        return None
    if line:
        reports = [r for r in reports if r.line.casefold() == line.casefold()]
    if kind:
        reports = [r for r in reports if r.kind == kind]
    return reports


def get_pass_product(api: ApiClient, product_id: str) -> PassProduct | None:
    return _get(api, "pass_product", lambda r: PassProduct.from_json(r.json()), path={"product_id": product_id})


# ---------------------------------------------------------------- aggregate for the bus panel
def get_bus_info(api: ApiClient, bus_id: int, *, line: str | None = None, with_frame: bool = False) -> BusInfo:
   
    errors: dict[str, str] = {}

    def safe(part: str, fn: Callable[[], T]) -> T | None:
        try:
            return fn()
        except (ApiError, TransportError, AuthNotConfigured, ValueError, KeyError, TypeError) as exc:
            errors[part] = str(exc)
            return None

    position = safe("position", lambda: get_bus_position(api, bus_id))
    occupancy = safe("occupancy", lambda: get_occupancy(api, bus_id))
    devices = safe("devices", lambda: list_bus_devices(api, bus_id))
    interval = safe("capture_interval", lambda: get_capture_interval(api, bus_id))
    reports = safe("reports", lambda: list_reports(api, line=line)) if line else None
    frame = safe("frame", lambda: get_latest_frame(api, bus_id)) if with_frame else None

    return BusInfo(
        bus_id=int(bus_id),
        position=position,
        occupancy=occupancy.passenger_count if occupancy else None,
        devices=tuple(devices or ()),
        capture_interval_ms=interval.next_capture_ms if interval else None,
        reports=tuple(reports or ()),
        frame=frame,
        errors=errors,
    )