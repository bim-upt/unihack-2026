from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Iterable, TypeVar

import requests

from .client import ApiClient
from .errors import ApiError, AuthNotConfigured, TransportError
from .models import (
    Bus, BusInfo, CaptureInterval, Device, DeviceStatus, Frame, FrameImage, Occupancy, PassProduct, Position, Report, Telemetry,
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


# ---------------------------------------------------------------- what users do (display views)
def list_records(api: ApiClient, name: str, **query) -> list[dict] | None:
    """A list call of the user views (users, rides, charges, ...) as plain JSON records: they are only shown
    in tables, so no model class for each. None when the endpoint is turned off."""
    return _get(api, name, lambda r: _items(r.json()), query=query)


def get_record(api: ApiClient, name: str, **path) -> dict | None:
    """One record (a user, a ride) as plain JSON. None when unknown (404) or turned off."""
    return _get(api, name, lambda r: r.json(), path=path)


# ---------------------------------------------------------------- bulk helpers
def fetch_many(fn: Callable[[K], T], keys: Iterable[K], workers: int = 16) -> dict[K, tuple[T | None, str | None]]:
    """Runs `fn(key)` for every key in parallel. The API has no bulk calls for these, so the per-bus
    calls run side by side instead of one after another."""
    keys = list(dict.fromkeys(keys))

    def one(k: K) -> tuple[T | None, str | None]:
        try:
            return fn(k), None
        except (ApiError, TransportError, AuthNotConfigured, ValueError, KeyError, TypeError) as exc:
            return None, str(exc)

    if len(keys) <= 1:
        return {k: one(k) for k in keys}
    with ThreadPoolExecutor(max_workers=min(workers, len(keys))) as pool:
        return dict(zip(keys, pool.map(one, keys)))


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


def get_device_statuses(api: ApiClient, bus_id: int) -> tuple[list[DeviceStatus], dict[str, str]]:
    """Every device of one bus with its newest message: 4 small calls, made in parallel. Raises if the
    device list itself fails; the other parts are best effort and their errors come back in the dict."""
    parts: dict[str, Callable[[], object]] = {
        "devices": lambda: list_bus_devices(api, bus_id),
        "position": lambda: get_bus_position(api, bus_id),
        "frames": lambda: list_frames(api, bus_id, limit=1),
        "capture_interval": lambda: get_capture_interval(api, bus_id),
    }
    got = fetch_many(lambda name: parts[name](), parts)
    devices, err = got["devices"]
    if err:
        raise TransportError(f"devices: {err}")
    errors = {name: e for name, (_, e) in got.items() if e}
    frames = got["frames"][0] or []
    interval = got["capture_interval"][0]
    return combine_device_statuses(
        devices or [], got["position"][0], frames[0] if frames else None,
        interval.next_capture_ms if interval else None,
    ), errors


# ---------------------------------------------------------------- aggregate for the bus panel
def get_bus_info(api: ApiClient, bus_id: int, *, line: str | None = None, with_frame: bool = False) -> BusInfo:
    """Everything the bus panel shows. The parts are separate calls, made in parallel; a part that fails
    is left empty and its error comes back in `errors`."""
    parts: dict[str, Callable[[], object]] = {
        "position": lambda: get_bus_position(api, bus_id),
        "occupancy": lambda: get_occupancy(api, bus_id),
        "devices": lambda: list_bus_devices(api, bus_id),
        "capture_interval": lambda: get_capture_interval(api, bus_id),
    }
    if line:
        parts["reports"] = lambda: list_reports(api, line=line)
    if with_frame:
        parts["frame"] = lambda: get_latest_frame(api, bus_id)
    got = fetch_many(lambda name: parts[name](), parts)
    value = {name: v for name, (v, _) in got.items()}
    occupancy, interval = value["occupancy"], value["capture_interval"]

    return BusInfo(
        bus_id=int(bus_id),
        position=value["position"],
        occupancy=occupancy.passenger_count if occupancy else None,
        devices=tuple(value["devices"] or ()),
        capture_interval_ms=interval.next_capture_ms if interval else None,
        reports=tuple(value.get("reports") or ()),
        frame=value.get("frame"),
        errors={name: e for name, (_, e) in got.items() if e},
    )


# ---------------------------------------------------------------- everything for the selected bus, at once
@dataclass(frozen=True)
class BusSnapshot:
    info: BusInfo
    frame: FrameImage | None
    history: list[Telemetry] | None
    devices: list[DeviceStatus]
    errors: dict[str, str]
    photo_meta: Frame | None = None   # metadata of the newest photo (capture time, sequence)


def _parts(parts: dict[str, Callable[[], object]]) -> tuple[dict[str, object], dict[str, str]]:
    got = fetch_many(lambda name: parts[name](), parts)
    return {n: v for n, (v, _) in got.items()}, {n: e for n, (_, e) in got.items() if e}


def get_bus_live(api: ApiClient, bus_id: int, *, history_limit: int) -> tuple[dict, dict]:
    """What changes every second: position, passengers, recent positions (the trail)."""
    return _parts({
        "position": lambda: get_bus_position(api, bus_id),
        "occupancy": lambda: get_occupancy(api, bus_id),
        "history": lambda: get_telemetry_history(api, bus_id, limit=history_limit),
    })


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
        reports=(),
        frame=None,
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
    )


def get_bus_snapshot(api: ApiClient, bus_id: int, *, history_limit: int) -> BusSnapshot:
    """All of it at once (no caching schedule)."""
    return assemble_bus_snapshot(bus_id, get_bus_live(api, bus_id, history_limit=history_limit),
                                 get_bus_setup(api, bus_id), get_bus_photo(api, bus_id))
