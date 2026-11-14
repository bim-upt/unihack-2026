from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


@dataclass(frozen=True, eq=False)
class RouteLink:
    gtfs_to_api: dict[str, int]
    api_to_gtfs: dict[int, str]

    def api_id(self, gtfs_route_id: str) -> int | None:
        return self.gtfs_to_api.get(gtfs_route_id)

    def gtfs_id(self, api_route_id: int) -> str | None:
        return self.api_to_gtfs.get(api_route_id)

    @classmethod
    def build(cls, gtfs_route_ids: Iterable[str], map_file: Path | None) -> "RouteLink":
        mapping: dict[str, int] = {}
        for rid in gtfs_route_ids:
            if rid.strip().isdigit():
                mapping[rid] = int(rid)
        if map_file is not None and Path(map_file).exists():
            with open(map_file, newline="", encoding="utf-8-sig") as fh:
                for row in csv.DictReader(fh):
                    g = (row.get("gtfs_route_id") or "").strip()
                    a = (row.get("api_route_id") or "").strip()
                    if g and a.isdigit():
                        mapping[g] = int(a)
        reverse: dict[int, str] = {}
        for g, a in mapping.items():
            reverse.setdefault(a, g)
        return cls(mapping, reverse)