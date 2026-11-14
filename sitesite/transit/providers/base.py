from __future__ import annotations

from datetime import datetime
from typing import Protocol

from ..domain import Vehicle


class VehicleProvider(Protocol):
    source: str

    def vehicles(self, route_id: str, now: datetime) -> list[Vehicle]: ...


class DetailsProvider(Protocol):
    def details(self, vehicle: Vehicle) -> dict[str, object]: ...