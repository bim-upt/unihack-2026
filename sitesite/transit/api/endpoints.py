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
    # reports and passes
    reports: Endpoint | None = Endpoint("GET", "/reports", params=(("line", "line"), ("kind", "kind")))
    pass_catalog: Endpoint | None = Endpoint("GET", "/passes/catalog")   # every pass product

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