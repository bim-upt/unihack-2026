from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Iterable, TypeVar

import requests

from .client import ApiClient
from .errors import ApiError, AuthNotConfigured, TransportError, user_message
from .models import (
    Bus, BusInfo, CaptureInterval, Device, DeviceStatus, Frame, Headcount, FrameImage, Occupancy, PassProduct, Position, Report, Telemetry,
)

T = TypeVar("T")
K = TypeVar("K")
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


def get_headcount(api: ApiClient, bus_id: int) -> Headcount | None:
    """None when the server doesn't have this call (an older server) or the bus is unknown."""
    return _get(api, "headcount", lambda r: Headcount.from_json(r.json()), path={"bus_id": int(bus_id)})


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


def list_pass_products(api: ApiClient) -> list[PassProduct] | None:
    return _list(api, "pass_catalog", PassProduct)


# ---------------------------------------------------------------- server reachable?
def server_up(api: ApiClient, timeout_s: float = 3.0) -> bool:
    """GET /health once, without the automatic retries of the normal calls (needs no token). 3 s: the server
    sometimes takes 1.5 s to answer, and a shorter limit took a slow moment for an outage."""
    try:
        r = requests.get(api.transport.base_url + "/health", timeout=timeout_s, verify=api.transport.verify_tls)
        return r.status_code == 200
    except requests.RequestException:
        return False


# ---------------------------------------------------------------- what users do (display views)
def list_records(api: ApiClient, name: str, **query) -> list[dict] | None:
    """A list call of the user views (users, rides, charges, ...) as plain JSON records: they are only shown
    in tables, so no model class for each. None when the endpoint is turned off."""
    return _get(api, name, lambda r: _items(r.json()), query=query)


def list_all_records(api: ApiClient, name: str, page: int = 1000, max_pages: int = 50, **query) -> list[dict] | None:
    """Every record of a list call: pages through it with limit/offset (at most 1000 per call)."""
    out: list[dict] = []
    for i in range(max_pages):
        batch = list_records(api, name, limit=page, offset=i * page, **query)
        if batch is None:
            return None if i == 0 else out
        out.extend(batch)
        if len(batch) < page:
            break
    return out


def get_record(api: ApiClient, name: str, **path) -> dict | None:
    """One record (a user, a ride) as plain JSON. None when unknown (404) or turned off."""
    return _get(api, name, lambda r: r.json(), path=path)


# ---------------------------------------------------------------- bulk helpers
_POOL = ThreadPoolExecutor(max_workers=16, thread_name_prefix="transit-api")   # one pool for every page


def fetch_many(fn: Callable[[K], T], keys: Iterable[K], workers: int = 16) -> dict[K, tuple[T | None, str | None]]:
    """Runs `fn(key)` for every key in parallel, on one shared pool of 16 threads. The API has no bulk calls
    for these, so the per-bus calls run side by side instead of one after another."""
    keys = list(dict.fromkeys(keys))

    def one(k: K) -> tuple[T | None, str | None]:
        try:
            return fn(k), None
        except (ApiError, TransportError, AuthNotConfigured, ValueError, KeyError, TypeError) as exc:
            return None, user_message(exc)

    # one key, or already on a pool thread (a fetch_many inside a fetch_many): run here, so the pool can't
    # fill up with tasks that wait for each other
    if len(keys) <= 1 or threading.current_thread().name.startswith("transit-api"):
        return {k: one(k) for k in keys}
    return dict(zip(keys, _POOL.map(one, keys)))


def list_latest_telemetry(api: ApiClient) -> list[Telemetry] | None:
    """Newest message of every bus, in one call (GET /buses/positions without a route)."""
    return _list(api, "route_positions", Telemetry)


# ---------------------------------------------------------------- aggregate for the device views
def combine_device_statuses(devices: Iterable[Device], telemetry: Telemetry | None, frame: Frame | None,
                            capture_interval_ms: int | None = None) -> list[DeviceStatus]:
    """Pairs a bus's devices with its newest beacon message and newest frame. Pure."""
    out = []
    for d in devices:
        t = telemetry if telemetry and d.is_beacon and telemetry.device_id in (None, d.device_id) else None
        out.append(DeviceStatus(
            device=d,
            telemetry=t,
            frame=frame if d.is_camera else None,
            capture_interval_ms=capture_interval_ms if d.is_camera else None,
        ))
    return out


# ---------------------------------------------------------------- aggregate for the bus panel
# ---------------------------------------------------------------- everything for the selected bus, at once
@dataclass(frozen=True)
class BusSnapshot:
    info: BusInfo
    frame: FrameImage | None
    history: list[Telemetry] | None
    devices: list[DeviceStatus]
    errors: dict[str, str]
    photo_meta: Frame | None = None   # metadata of the newest photo (capture time, sequence)
    headcount: Headcount | None = None   # camera count vs paid (None: no such call on the server)


def _parts(parts: dict[str, Callable[[], object]]) -> tuple[dict[str, object], dict[str, str]]:
    got = fetch_many(lambda name: parts[name](), parts)
    return {n: v for n, (v, _) in got.items()}, {n: e for n, (_, e) in got.items() if e}


def get_bus_setup(api: ApiClient, bus_id: int) -> tuple[dict, dict]:
    """What rarely changes: the devices and the camera's capture interval."""
    return _parts({
        "devices": lambda: list_bus_devices(api, bus_id),
        "capture_interval": lambda: get_capture_interval(api, bus_id),
    })


def get_bus_photo(api: ApiClient, bus_id: int) -> tuple[dict, dict]:
    """The newest photo and its metadata: worth fetching once per capture interval."""
    return _parts({
        "frame": lambda: get_latest_frame(api, bus_id),
        "frames": lambda: list_frames(api, bus_id, limit=1),
    })


def assemble_bus_snapshot(bus_id: int, live: tuple[dict, dict], setup: tuple[dict, dict],
                          photo: tuple[dict, dict]) -> BusSnapshot:
    """Puts the three parts (fetched on their own schedules) together. Pure."""
    v = {**live[0], **setup[0], **photo[0]}
    errors = {**live[1], **setup[1], **photo[1]}
    occupancy, interval, frames = v.get("occupancy"), v.get("capture_interval"), v.get("frames") or []
    interval_ms = interval.next_capture_ms if interval else None
    info = BusInfo(
        bus_id=int(bus_id),
        position=v.get("position"),
        occupancy=occupancy.passenger_count if occupancy else None,
        devices=tuple(v.get("devices") or ()),
        capture_interval_ms=interval_ms,
        errors={k: e for k, e in errors.items() if k in ("position", "occupancy", "devices", "capture_interval")},
    )
    return BusSnapshot(
        info=info,
        frame=v.get("frame"),
        history=v.get("history"),
        devices=combine_device_statuses(v.get("devices") or [], v.get("position"), frames[0] if frames else None,
                                        interval_ms),
        errors=errors,
        photo_meta=frames[0] if frames else None,
        headcount=v.get("headcount"),
    )


