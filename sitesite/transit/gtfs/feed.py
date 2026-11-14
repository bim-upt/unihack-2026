from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from ..colors import clean_hex, distinct_colors, readable_text_color
from ..domain import Route, Stop, Trip
from ..geometry import Shape
from .calendar import active_services
from ..config import Bounds


@dataclass(eq=False)
class Feed:
    routes: dict[str, Route]
    stops: dict[str, Stop]
    trips: dict[str, Trip]
    shapes: dict[str, Shape]
    calendar: pd.DataFrame
    calendar_dates: pd.DataFrame
    trips_by_route: dict[str, list[str]]
    shapes_by_route: dict[str, list[str]]
    stops_by_route: dict[str, list[str]]
    _stop_dist: dict[str, np.ndarray | None] = field(default_factory=dict, repr=False)

    def sorted_route_ids(self) -> list[str]:
        return sorted(self.routes, key=lambda r: (len(self.routes[r].short_name), self.routes[r].short_name))

    def drawable_route_ids(self) -> list[str]:
        return [r for r in self.sorted_route_ids() if r in self.shapes_by_route]

    def stop_name(self, stop_id: str) -> str:
        s = self.stops.get(stop_id)
        return s.name if s else stop_id

    def services_on(self, day: date) -> frozenset[str]:
        return active_services(self.calendar, self.calendar_dates, day)

    def shape_for(self, trip: Trip) -> Shape | None:
        return self.shapes.get(trip.shape_id) if trip.shape_id else None

    def stop_distances(self, trip: Trip) -> np.ndarray | None:
        if trip.trip_id not in self._stop_dist:
            shape = self.shape_for(trip)
            self._stop_dist[trip.trip_id] = None if shape is None else shape.project(trip.coords)
        return self._stop_dist[trip.trip_id]

    def route_bounds(self, route_id: str) -> Bounds | None:
        """Box around one line's shapes (or its stops when it has no shape)."""
        coords = [self.shapes[s].coords for s in self.shapes_by_route.get(route_id, []) if s in self.shapes]
        if coords:
            pts = np.vstack(coords)
        else:
            pts = np.array([[self.stops[s].lat, self.stops[s].lon] for s in self.stops_by_route.get(route_id, [])
                            if s in self.stops])
        if not len(pts):
            return None
        return (float(pts[:, 0].min()), float(pts[:, 1].min())), (float(pts[:, 0].max()), float(pts[:, 1].max()))

    def bounds(self) -> Bounds:
        if self.shapes:
            pts = np.vstack([s.coords for s in self.shapes.values()])
        else:
            pts = np.array([[s.lat, s.lon] for s in self.stops.values()])
        pts = pts[~((np.abs(pts[:, 0]) < 1e-6) & (np.abs(pts[:, 1]) < 1e-6))]
        return (
            (float(pts[:, 0].min()), float(pts[:, 1].min())),
            (float(pts[:, 0].max()), float(pts[:, 1].max())),
        )


# ------- readers
def _read(base: Path, name: str, required: bool = True, **kw) -> pd.DataFrame:
    path = base / name
    if not path.exists():
        if required:
            raise FileNotFoundError(path)
        return pd.DataFrame()
    return pd.read_csv(path, dtype=str, keep_default_na=False, skipinitialspace=True,
                       encoding="utf-8-sig", **kw)


def _ensure(df: pd.DataFrame, *cols: str) -> pd.DataFrame:
    for c in cols:
        if c not in df.columns:
            df[c] = ""
    return df


def _load_routes(base: Path) -> dict[str, Route]:
    df = _ensure(_read(base, "routes.txt"), "route_short_name", "route_long_name",
                 "route_type", "route_color", "route_text_color")
    color = clean_hex(df["route_color"])
    tcolor = clean_hex(df["route_text_color"])
    fallback = distinct_colors(df.loc[color == "", "route_id"])
    out = {}
    for row, c, tc in zip(df.itertuples(index=False), color, tcolor):
        col = f"#{c}" if c else fallback[row.route_id]
        out[row.route_id] = Route(
            route_id=row.route_id,
            short_name=row.route_short_name or row.route_id,
            long_name=row.route_long_name,
            route_type=row.route_type,
            color=col,
            text_color=f"#{tc}" if tc else readable_text_color(col),
        )
    return out


def _load_stops(base: Path) -> dict[str, Stop]:
    df = _ensure(_read(base, "stops.txt"), "stop_name").drop_duplicates("stop_id")
    df = df[(df["stop_lat"] != "") & (df["stop_lon"] != "")]
    return {
        r.stop_id: Stop(r.stop_id, r.stop_name, float(r.stop_lat), float(r.stop_lon))
        for r in df.itertuples(index=False)
    }


def _load_shapes(base: Path, simplify_m: float) -> dict[str, Shape]:
    path = base / "shapes.txt"
    if not path.exists():
        return {}
    df = pd.read_csv(path, dtype={"shape_id": str}, encoding="utf-8-sig", skipinitialspace=True,
                     usecols=["shape_id", "shape_pt_lat", "shape_pt_lon", "shape_pt_sequence"])
    df = df.sort_values(["shape_id", "shape_pt_sequence"])
    return {
        sid: Shape.from_coords(g[["shape_pt_lat", "shape_pt_lon"]].to_numpy(float), simplify_m)
        for sid, g in df.groupby("shape_id", sort=False)
        if len(g) > 1
    }


def _load_trips(base: Path, trips_df: pd.DataFrame, stops: dict[str, Stop]) -> dict[str, Trip]:
    st_ = _read(base, "stop_times.txt",
                usecols=["trip_id", "arrival_time", "departure_time", "stop_id", "stop_sequence"])
    tstr = st_["arrival_time"].where(st_["arrival_time"] != "", st_["departure_time"]).str.strip()
    st_ = st_.assign(
        t=pd.to_timedelta(tstr, errors="coerce").dt.total_seconds(),
        seq=pd.to_numeric(st_["stop_sequence"], errors="coerce"),
    )
    st_ = st_[st_["stop_id"].isin(list(stops))].dropna(subset=["t", "seq"])
    st_ = st_.sort_values(["trip_id", "seq"]).assign(
        lat=lambda d: d["stop_id"].map({k: s.lat for k, s in stops.items()}),
        lon=lambda d: d["stop_id"].map({k: s.lon for k, s in stops.items()}),
    )

    meta = trips_df.set_index("trip_id").to_dict("index")
    out = {}
    for tid, g in st_.groupby("trip_id", sort=False):
        m = meta.get(tid)
        if m is None or len(g) < 2:
            continue
        out[tid] = Trip(
            trip_id=tid,
            route_id=m["route_id"],
            service_id=m["service_id"],
            shape_id=m["shape_id"] or None,
            headsign=m["trip_headsign"],
            times=g["t"].to_numpy(float),
            stop_ids=tuple(g["stop_id"]),
            coords=g[["lat", "lon"]].to_numpy(float),
        )
    return out


def load_feed(gtfs_dir: Path, simplify_m: float = 0.0) -> Feed:
    base = Path(gtfs_dir)
    routes = _load_routes(base)
    stops = _load_stops(base)
    shapes = _load_shapes(base, simplify_m)
    trips_df = _ensure(_read(base, "trips.txt"), "shape_id", "trip_headsign").drop_duplicates("trip_id")
    trips = _load_trips(base, trips_df, stops)

    trips_by_route: dict[str, list[str]] = {}
    for t in trips.values():
        trips_by_route.setdefault(t.route_id, []).append(t.trip_id)

    shaped = trips_df[trips_df["shape_id"] != ""]
    shapes_by_route = {
        rid: [s for s in ids if s in shapes]
        for rid, ids in shaped.groupby("route_id")["shape_id"].unique().items()
    }
    shapes_by_route = {k: v for k, v in shapes_by_route.items() if v}

    stops_by_route = {
        rid: list(dict.fromkeys(s for tid in ids for s in trips[tid].stop_ids))
        for rid, ids in trips_by_route.items()
    }

    return Feed(
        routes=routes,
        stops=stops,
        trips=trips,
        shapes=shapes,
        calendar=_read(base, "calendar.txt", required=False),
        calendar_dates=_read(base, "calendar_dates.txt", required=False),
        trips_by_route=trips_by_route,
        shapes_by_route=shapes_by_route,
        stops_by_route=stops_by_route,
    )