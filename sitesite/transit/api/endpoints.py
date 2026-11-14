from __future__ import annotations

from dataclasses import dataclass, fields, replace
from urllib.parse import quote


@dataclass(frozen=True)
class Endpoint:
    method: str
    path: str
    params: tuple[tuple[str, str], ...] = ()
    auth: str = "default"

    def url_path(self, **path_vars) -> str:
        return self.path.format(**{k: quote(str(v), safe="") for k, v in path_vars.items()})

    def query(self, **values) -> dict:
        wire = dict(self.params)
        return {wire[k]: v for k, v in values.items() if v is not None and k in wire}


@dataclass(frozen=True)
class Endpoints:
    # devices
    bus_devices: Endpoint | None = Endpoint("GET", "/buses/{bus_id}/devices")
    device: Endpoint | None = Endpoint("GET", "/bus-devices/{device_id}")
    # buses and positions
    buses: Endpoint | None = Endpoint("GET", "/buses")
    bus_telemetry: Endpoint | None = Endpoint(
        "GET", "/buses/{bus_id}/telemetry", params=(("start", "from"), ("end", "to"), ("limit", "limit")))
    bus_position: Endpoint | None = Endpoint("GET", "/buses/{bus_id}/position")
    route_positions: Endpoint | None = Endpoint("GET", "/buses/positions", params=(("route_id", "route_id"),))
    # camera
    bus_frames: Endpoint | None = Endpoint(
        "GET", "/buses/{bus_id}/frames", params=(("limit", "limit"), ("before", "before")))
    bus_frame_latest: Endpoint | None = Endpoint("GET", "/buses/{bus_id}/frames/latest")
    capture_interval: Endpoint | None = Endpoint("GET", "/buses/{bus_id}/capture-interval")
    # passengers
    occupancy: Endpoint | None = Endpoint("GET", "/buses/{bus_id}/occupancy")
    # people the camera counts vs app users who paid (an estimate; counted about every 5 s)
    headcount: Endpoint | None = Endpoint("GET", "/buses/{bus_id}/headcount")
    # reports and passes
    reports: Endpoint | None = Endpoint("GET", "/reports", params=(("line", "line"), ("kind", "kind")))
    pass_catalog: Endpoint | None = Endpoint("GET", "/passes/catalog")   # every pass product
    # what users do (display views; lists are newest first)
    users: Endpoint | None = Endpoint("GET", "/users", params=(("tier", "tier"), ("limit", "limit"), ("offset", "offset")))
    user: Endpoint | None = Endpoint("GET", "/users/{user_id}")
    sightings: Endpoint | None = Endpoint(
        "GET", "/sightings", params=(("user_id", "user_id"), ("bus_id", "bus_id"), ("accepted", "accepted"), ("limit", "limit"), ("offset", "offset")))
    rides: Endpoint | None = Endpoint(
        "GET", "/rides", params=(("user_id", "user_id"), ("bus_id", "bus_id"), ("status", "status"), ("limit", "limit"), ("offset", "offset")))
    ride: Endpoint | None = Endpoint("GET", "/rides/{session_id}")
    # from/to (on startedAt) and kind are being added on the server; until then it ignores them (the site filters too)
    charges: Endpoint | None = Endpoint(
        "GET", "/charges", params=(("user_id", "user_id"), ("status", "status"), ("start", "from"), ("end", "to"),
                                   ("kind", "kind"), ("limit", "limit"), ("offset", "offset")))
    trips: Endpoint | None = Endpoint("GET", "/trips", params=(("user_id", "user_id"), ("limit", "limit"), ("offset", "offset")))
    passes: Endpoint | None = Endpoint("GET", "/passes", params=(("user_id", "user_id"), ("limit", "limit"), ("offset", "offset")))
    ticket_verifications: Endpoint | None = Endpoint(
        "GET", "/ticket-verifications", params=(("inspector_id", "inspector_id"), ("limit", "limit"), ("offset", "offset")))

    def names(self) -> list[str]:
        return [f.name for f in fields(self)]

    def override(self, **changes: Endpoint | str | None) -> "Endpoints":
        out = {}
        for name, value in changes.items():
            if name not in self.names():
                raise KeyError(f"Unknown endpoint {name!r}. Known: {self.names()}")
            if isinstance(value, str):
                current = getattr(self, name)
                if current is None:
                    raise ValueError(f"Endpoint {name!r} is disabled; pass a full Endpoint to enable it.")
                value = replace(current, path=value)
            out[name] = value
        return replace(self, **out)