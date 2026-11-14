from __future__ import annotations

import hashlib

from ..domain import Vehicle


class DummyDetailsProvider:
    def details(self, vehicle: Vehicle) -> dict[str, object]:
        h = int(hashlib.md5(vehicle.vehicle_id.encode()).hexdigest(), 16)
        return {
            "Plate": f"TM-{h % 90 + 10:02d}-STP",
            "Occupancy (dummy)": f"{h % 100}%",
            "Passengers (dummy)": h % 80,
            "Delay (dummy)": f"{(h >> 8) % 6} min",
            "Doors (dummy)": ["closed", "open"][(h >> 12) % 2],
            "Last update (dummy)": "just now",
        }