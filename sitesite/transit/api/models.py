from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

import requests


# --------------------------------- helpers
def _pick(j: dict, *keys, default=None):
    for k in keys:
        if k in j and j[k] is not None:
            return j[k]
    return default


def _f(v) -> float | None:
    try:
        return None if v is None or v == "" else float(v)
    except (TypeError, ValueError):
        return None


def _i(v) -> int | None:
    try:
        return None if v is None or v == "" else int(v)
    except (TypeError, ValueError):
        return None


def _b(v) -> bool | None:
    if v is None:
        return None
    if isinstance(v, str):
        return v.strip().lower() in ("1", "true", "yes")
    return bool(v)


def _dt(v) -> datetime | None:
    if not v:
        return None
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except ValueError:
        return None


def _unix(ts: int | None) -> datetime | None:
    return datetime.fromtimestamp(ts, timezone.utc) if ts else None


def _size(v) -> tuple[int | None, int | None]:
    """'800x600' -> (800, 600)."""
    if not v or "x" not in str(v).lower():
        return None, None
    w, h = str(v).lower().split("x", 1)
    return _i(w), _i(h)


# --------------------------- devices and buses
@dataclass(frozen=True)
class Device:
    device_id: str
    bus_id: int | None
    role: str | None               # "beacon" | "camera"

    @property
    def is_beacon(self) -> bool:
        return self.role == "beacon"

    @property
    def is_camera(self) -> bool:
        return self.role == "camera"

    @classmethod
    def from_json(cls, j: dict) -> "Device":
        return cls(
            device_id=str(_pick(j, "device_id", "deviceId", "id")),
            bus_id=_i(_pick(j, "bus_id", "busId")),
            role=_pick(j, "role"),
        )


@dataclass(frozen=True)
class Bus:
    bus_id: int
    route_id: int | None

    @classmethod
    def from_json(cls, j: dict) -> "Bus":
        return cls(bus_id=int(_pick(j, "bus_id", "busId")), route_id=_i(_pick(j, "route_id", "routeId")))


# ---------------------------- positions
@dataclass(frozen=True)
class Position:
    """GET /buses/positions item: one per bus."""
    bus_id: int
    route_id: int | None = None
    ts: int | None = None         
    sim: bool | None = None       
    fix_ok: bool | None = None
    lat: float | None = None
    lon: float | None = None
    speed_kmh: float | None = None

    @property
    def latlon(self) -> tuple[float, float] | None:
        if self.fix_ok is not False and self.lat is not None and self.lon is not None:
            return self.lat, self.lon
        return None

    @property
    def time(self) -> datetime | None:
        return _unix(self.ts)

    def age_s(self, now: datetime | None = None) -> float | None:
        t = self.time
        return None if t is None else ((now or datetime.now(timezone.utc)) - t).total_seconds()

    @staticmethod
    def _base(j: dict) -> dict:
        return dict(
            bus_id=int(_pick(j, "bus_id", "busId")),
            route_id=_i(_pick(j, "route_id", "routeId")),
            ts=_i(_pick(j, "ts")),
            sim=_b(_pick(j, "sim")),
            fix_ok=_b(_pick(j, "fix_ok", "fixOk")),
            lat=_f(_pick(j, "lat")),
            lon=_f(_pick(j, "lon", "lng")),
            speed_kmh=_f(_pick(j, "speed_kmh", "speedKmh")),
        )

    @classmethod
    def from_json(cls, j: dict) -> "Position":
        return cls(**cls._base(j))


@dataclass(frozen=True)
class Telemetry(Position):
    """GET /buses/{bus_id}/position and /telemetry items: Position plus device fields."""
    device_id: str | None = None
    boot_id: str | None = None     
    seq: int | None = None         
    uptime_ms: int | None = None
    received_at: datetime | None = None   # server clock

    @classmethod
    def from_json(cls, j: dict) -> "Telemetry":
        return cls(
            **cls._base(j),
            device_id=_pick(j, "device_id", "deviceId"),
            boot_id=_pick(j, "boot_id", "bootId"),
            seq=_i(_pick(j, "seq")),
            uptime_ms=_i(_pick(j, "uptime_ms", "uptimeMs")),
            received_at=_dt(_pick(j, "received_at", "receivedAt")),
        )


# ------------------------------------- camera
@dataclass(frozen=True)
class Frame:
    """Photo metadata (GET /buses/{bus_id}/frames item)."""
    bus_id: int
    route_id: int | None
    frame_seq: int | None          
    capture_ts: int | None        
    width: int | None
    height: int | None
    received_at: datetime | None = None   # server clock

    @property
    def captured_at(self) -> datetime | None:
        return _unix(self.capture_ts)

    @classmethod
    def from_json(cls, j: dict) -> "Frame":
        w, h = _size(_pick(j, "frame_size", "frameSize", "size"))
        return cls(
            bus_id=int(_pick(j, "bus_id", "busId")),
            route_id=_i(_pick(j, "route_id", "routeId")),
            frame_seq=_i(_pick(j, "frame_seq", "frameSeq", "seq")),
            capture_ts=_i(_pick(j, "capture_ts", "captureTs")),
            width=_i(_pick(j, "width")) or w,
            height=_i(_pick(j, "height")) or h,
            received_at=_dt(_pick(j, "received_at", "receivedAt")),
        )


@dataclass(frozen=True)
class FrameImage:
    bus_id: int
    jpeg: bytes
    content_type: str
    meta: Frame | None

    @classmethod
    def from_response(cls, bus_id: int, resp: requests.Response) -> "FrameImage":
        h = resp.headers
        meta = None
        if any(k in h for k in ("X-Frame-Seq", "X-Capture-Ts", "X-Frame-Size", "X-Route-Id")):
            w, hh = _size(h.get("X-Frame-Size"))
            meta = Frame(
                bus_id=bus_id,
                route_id=_i(h.get("X-Route-Id")),
                frame_seq=_i(h.get("X-Frame-Seq")),
                capture_ts=_i(h.get("X-Capture-Ts")),
                width=w,
                height=hh,
            )
        return cls(bus_id, resp.content, h.get("Content-Type", ""), meta)


@dataclass(frozen=True)
class CaptureInterval:
    bus_id: int
    next_capture_ms: int | None

    @classmethod
    def from_json(cls, j: dict) -> "CaptureInterval":
        return cls(
            bus_id=int(_pick(j, "bus_id", "busId")),
            next_capture_ms=_i(_pick(j, "next_capture_ms", "nextCaptureMs")),
        )


# ----------------------------- passengers
@dataclass(frozen=True)
class Occupancy:
    bus_id: int
    passenger_count: int | None

    @classmethod
    def from_json(cls, j: dict) -> "Occupancy":
        return cls(
            bus_id=int(_pick(j, "bus_id", "busId")),
            passenger_count=_i(_pick(j, "passenger_count", "passengerCount", "passengers", "count")),
        )


@dataclass(frozen=True)
class Headcount:
    """GET /buses/{bus_id}/headcount: the camera's count of people against the app users who paid. An estimate
    (the driver counts, someone can be missed or counted twice)."""
    bus_id: int
    people_counted: int | None        # None: nothing counted yet, or the count is older than 60 s
    counted_at: datetime | None       # when the photo was counted (also given when too old to show)
    frame_id: str | None
    devices_heard: int | None         # app users heard on the bus now
    devices_paid: int | None          # ... of them with a valid ticket
    devices_unpaid: int | None
    paid_passengers: int | None       # people the valid tickets cover
    without_ticket: int | None        # max(0, people_counted - paid_passengers); None with no count

    @classmethod
    def from_json(cls, j: dict) -> "Headcount":
        return cls(
            bus_id=int(_pick(j, "bus_id", "busId")),
            people_counted=_i(_pick(j, "people_counted")),
            counted_at=_dt(_pick(j, "counted_at")),
            frame_id=_pick(j, "frame_id"),
            devices_heard=_i(_pick(j, "devices_heard")),
            devices_paid=_i(_pick(j, "devices_paid")),
            devices_unpaid=_i(_pick(j, "devices_unpaid")),
            paid_passengers=_i(_pick(j, "paid_passengers")),
            without_ticket=_i(_pick(j, "without_ticket")),
        )


# ----------------------------- reports and passes
@dataclass(frozen=True)
class Report:
    id: str
    kind: str                      # traffic | accident | breakdown | works | crowding | detour
    line: str
    stop_name: str | None
    confirmations: int
    expires_at: datetime | None
    created_at: datetime | None

    @classmethod
    def from_json(cls, j: dict) -> "Report":
        return cls(
            id=str(_pick(j, "id")),
            kind=str(_pick(j, "kind", default="")),
            line=str(_pick(j, "line", default="")),
            stop_name=_pick(j, "stopName", "stop_name"),
            confirmations=_i(_pick(j, "confirmations")) or 0,
            expires_at=_dt(_pick(j, "expiresAt", "expires_at")),
            created_at=_dt(_pick(j, "createdAt", "created_at")),
        )


@dataclass(frozen=True)
class PassProduct:
    id: str
    name: str
    days: int | None
    price_ron: float | None
    lines: str | tuple[str, ...]   # "all" or line names

    @classmethod
    def from_json(cls, j: dict) -> "PassProduct":
        lines = _pick(j, "lines", default="all")
        return cls(
            id=str(_pick(j, "id")),
            name=str(_pick(j, "name", default="")),
            days=_i(_pick(j, "days")),
            price_ron=_f(_pick(j, "priceRon", "price_ron")),
            lines=lines if isinstance(lines, str) else tuple(str(x) for x in lines),
        )


# ------------------- aggregate for the device views
@dataclass(frozen=True)
class DeviceStatus:
    """One device and the newest thing the server got from it."""
    device: Device
    telemetry: Telemetry | None = None       # beacon: its newest message
    frame: Frame | None = None               # camera: the bus's newest frame (frames carry no device id)
    capture_interval_ms: int | None = None   # camera

    @property
    def last_seen(self) -> datetime | None:
        """Server receive time of the newest message, else the device's own clock."""
        if self.telemetry:
            return self.telemetry.received_at or self.telemetry.time
        if self.frame:
            return self.frame.received_at or self.frame.captured_at
        return None


# ------------------- aggregate for the bus panel
@dataclass(frozen=True)
class BusInfo:
    bus_id: int
    position: Telemetry | None
    occupancy: int | None
    devices: tuple[Device, ...]
    capture_interval_ms: int | None
    errors: dict[str, str] = field(default_factory=dict) 