"""Line health page: how each line runs against its timetable, and a traffic map of how fast the buses move.

The API gives no trip ids, so "behind schedule" can't be measured per trip. What is compared is speed: the
average speed the line's buses reported in the chosen window against the speed the timetable expects for the
line's vehicles right now. A line slower than its timetable is losing time; "≈ min lost per 10 km" says how much.
"""
from __future__ import annotations

import functools
import statistics
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable
from zoneinfo import ZoneInfo

import folium
import pandas as pd
import streamlit as st
from streamlit_folium import st_folium

from ..api.models import Telemetry
from ..config import Bounds, Settings
from ..geometry import view_for
from ..gtfs import Feed
from ..mapview.layers import build_base_map
from ..providers import ScheduleProvider
from . import data, state
from .devices import picked_row

WINDOWS = {"10 min": 10, "30 min": 30, "1 h": 60, "3 h": 180}
MAX_GAP_S = 300          # two positions further apart in time aren't joined into a traffic segment
MAX_JUMP_M = 2000        # ... nor ones further apart in space (a jump, not driving)
# traffic bins: (upper km/h, colour, label), status palette: critical, serious, warning, good
SPEED_BINS = ((10, "#d03b3b", "< 10 km/h"), (20, "#ec835a", "10–20"), (30, "#fab219", "20–30"),
              (float("inf"), "#0ca30c", "30+ km/h"))
PACE_SLOW, PACE_VERY_SLOW = 0.8, 0.6
SEVERITY = {"very slow": 0, "slow": 1, "short of buses": 2, "on pace": 3, "no timetable now": 4, "no live data": 5}
K_WINDOW, K_ALL_LINES, K_LINE, K_LAST_LINE_PICK = "health_window", "health_all_lines", "health_line", "health_line_pick"


@dataclass(frozen=True)
class LineHealth:
    gtfs_route_id: str
    line: str
    live_buses: int
    scheduled_now: int
    live_kmh: float | None
    scheduled_kmh: float | None
    reports: int

    @property
    def pace(self) -> float | None:
        if self.live_kmh is None or not self.scheduled_kmh:
            return None
        return self.live_kmh / self.scheduled_kmh

    @property
    def minutes_lost_per_10km(self) -> float | None:
        if not self.live_kmh or not self.scheduled_kmh:
            return None
        return (60 / self.live_kmh - 60 / self.scheduled_kmh) * 10

    @property
    def status(self) -> str:
        if not self.live_buses:
            return "no live data"
        if not self.scheduled_now:
            return "no timetable now"
        pace = self.pace
        if pace is not None and pace < PACE_VERY_SLOW:
            return "very slow"
        if pace is not None and pace < PACE_SLOW:
            return "slow"
        if self.live_buses < self.scheduled_now:
            return "short of buses"
        return "on pace"


_TRIP_KMH: dict[str, float | None] = {}


def trip_kmh(feed: Feed, trip_id: str | None) -> float | None:
    """A trip's whole-trip average speed in the timetable: path length / scheduled duration. Used instead of the
    timetable's speed at one moment, which jumps (two stops with the same minute look like a very fast bus)."""
    if not trip_id or trip_id not in feed.trips:
        return None
    if trip_id not in _TRIP_KMH:
        trip, dist = feed.trips[trip_id], None
        dist = feed.stop_distances(trip)
        duration = float(trip.times[-1] - trip.times[0]) if len(trip.times) > 1 else 0.0
        _TRIP_KMH[trip_id] = (float(dist[-1] - dist[0]) / duration * 3.6
                              if dist is not None and duration > 0 and dist[-1] > dist[0] else None)
    return _TRIP_KMH[trip_id]


def speed_colour(kmh: float) -> str:
    return next(c for upper, c, _ in SPEED_BINS if kmh < upper)


def traffic_segments(history: list[Telemetry]) -> list[tuple[list[float], list[float], float, datetime | None]]:
    """(start, end, km/h, time) for each stretch driven between two consecutive positions. Pure."""
    pts = sorted((t for t in history if t.latlon and t.received_at), key=lambda t: t.received_at)
    out = []
    for a, b in zip(pts, pts[1:]):
        if (b.received_at - a.received_at).total_seconds() > MAX_GAP_S:
            continue
        dlat, dlon = (b.lat - a.lat) * 111_320, (b.lon - a.lon) * 111_320 * 0.70   # ~cos(45.7°)
        if (dlat * dlat + dlon * dlon) ** 0.5 > MAX_JUMP_M:
            continue
        speeds = [s for s in (a.speed_kmh, b.speed_kmh) if s is not None]
        if speeds:
            out.append(([a.lat, a.lon], [b.lat, b.lon], sum(speeds) / len(speeds), b.received_at))
    return out


# ---------------------------------------------------------------- page
def render_health_view(feed: Feed, settings: Settings, area: Bounds, gtfs_of: Callable[[int | None], str | None],
                       map_width_px: int, *, live: bool) -> None:
    st.title("Line health")
    _fragment(settings.health_refresh_s if live else None)(feed, settings, area, gtfs_of, map_width_px)


@functools.lru_cache(maxsize=None)
def _fragment(every_s: float | None):
    return st.fragment(run_every=every_s)(_health)


def _health(feed: Feed, settings: Settings, area: Bounds, gtfs_of: Callable[[int | None], str | None],
            map_width_px: int) -> None:
    data.require_server()   # server gone: the whole app shows the 'connection lost' banner
    ss = st.session_state
    # controls and counts in a narrow column left of the map, so the map starts right under the title
    col_ctl, col_map, col_side = st.columns([1, 4, 3], gap="medium")
    with col_ctl:
        window = st.radio("Window", list(WINDOWS), key=K_WINDOW, index=0)
        all_lines = st.toggle("Lines without live buses", key=K_ALL_LINES)

    now = datetime.now(timezone.utc)
    # rounded to the minute, so the calls are reused between updates and between viewers
    start = (now - timedelta(minutes=WINDOWS[window])).replace(second=0, microsecond=0)
    buses, err = data.buses()
    if err:
        st.error(f"Buses unavailable: {err}")
        return
    route_of = {b.bus_id: b.route_id for b in buses or []}
    history = data.recent_history(tuple(sorted(route_of)), start.strftime("%Y-%m-%dT%H:%M:%SZ"))
    reports, rerr = data.all_reports()

    by_line: dict[str, dict[int, list[Telemetry]]] = {}
    for bid, (rows, _e) in history.items():
        rid = gtfs_of(route_of.get(bid))
        rows = [t for t in rows or [] if t.received_at and t.received_at >= start]
        if rid and rows:
            by_line.setdefault(rid, {})[bid] = rows
    report_count: dict[str, int] = {}
    for r in reports or []:
        report_count[r.line.casefold()] = report_count.get(r.line.casefold(), 0) + 1

    schedule = ScheduleProvider(feed)
    local_now = now.astimezone(settings.tz)
    lines: list[LineHealth] = []
    for rid in feed.sorted_route_ids():
        sched = schedule.vehicles(rid, local_now)
        if rid not in by_line and not (all_lines and sched):
            continue
        speeds = [t.speed_kmh for rows in by_line.get(rid, {}).values() for t in rows if t.speed_kmh is not None]
        sched_speeds = [k for k in (trip_kmh(feed, v.trip_id) for v in sched) if k]
        route = feed.routes[rid]
        lines.append(LineHealth(
            gtfs_route_id=rid, line=route.short_name, live_buses=len(by_line.get(rid, {})),
            scheduled_now=len(sched),
            live_kmh=statistics.fmean(speeds) if speeds else None,
            scheduled_kmh=statistics.median(sched_speeds) if sched_speeds else None,
            reports=report_count.get(route.short_name.casefold(), 0),
        ))
    lines.sort(key=lambda h: (SEVERITY[h.status], -(h.minutes_lost_per_10km or 0)))

    count = lambda s: sum(h.status == s for h in lines)   # noqa: E731
    with col_ctl:
        st.divider()
        st.metric("Lines live", sum(h.live_buses > 0 for h in lines))
        st.metric("On pace", count("on pace"))
        st.metric("Slow", count("slow") + count("very slow"))
        st.metric("Short of buses", count("short of buses"))
        st.metric("Open reports", len(reports or []))
        if rerr:
            st.caption(f"Reports unavailable: {rerr}")

    selected = ss.get(K_LINE)
    with col_side:
        selected = _line_table(lines, selected)
    with col_map:
        _traffic_map(feed, settings, area, by_line, selected, map_width_px)
        st.markdown(" &nbsp; ".join(f"<span style='color:{c}'>━━</span> {label}" for _, c, label in SPEED_BINS),
                    unsafe_allow_html=True)
        st.caption(f"Where the buses drove in the last {window}, coloured by their speed · updated "
                   f"{local_now:%H:%M:%S}. Pace = observed speed ÷ the timetable's average speed of the line's trips running now.")


def _line_table(lines: list[LineHealth], selected: str | None) -> str | None:
    ss = st.session_state
    if not lines:
        st.info("No line has live positions in this window. Try a longer window.")
        return None
    ids = [h.gtfs_route_id for h in lines]
    fmt = lambda v, f: "—" if v is None else f.format(v)   # noqa: E731
    event = st.dataframe(
        pd.DataFrame([{
            "Line": h.line,
            "Status": h.status,
            "Buses live / timetable": f"{h.live_buses} / {h.scheduled_now}",
            "km/h live": fmt(h.live_kmh, "{:.0f}"),
            "km/h timetable": fmt(h.scheduled_kmh, "{:.0f}"),
            "Pace": fmt(h.pace, "{:.0%}"),
            "≈ min lost / 10 km": fmt(h.minutes_lost_per_10km, "{:+.0f}"),
            "Reports": h.reports,
        } for h in lines]),
        key=f"health_table_{hash(tuple(ids))}", on_select="rerun", selection_mode=["single-row", "single-cell"],
        hide_index=True, width="stretch", height=min(len(lines) + 1, 14) * 35 + 18,
    )
    row = picked_row(event.selection if event else None)
    picked = ids[row] if row is not None and 0 <= row < len(ids) else None
    if picked is not None and picked != ss.get(K_LAST_LINE_PICK):
        ss[K_LAST_LINE_PICK] = ss[K_LINE] = selected = picked
    if selected in ids:
        h = lines[ids.index(selected)]
        a, b = st.columns([3, 2], vertical_alignment="center")
        a.markdown(f"**Line {h.line}** · {h.status}")
        if b.button("Open on map", key="health_open", width="stretch"):
            state.open_bus_on_map(h.gtfs_route_id, None)
            st.rerun()
        if st.button("Show all lines", key="health_all"):
            ss[K_LINE] = None
            st.rerun(scope="fragment")
        return selected
    st.caption("Click a line to zoom the map to it.")
    return None


def _traffic_map(feed: Feed, settings: Settings, area: Bounds, by_line: dict[str, dict[int, list[Telemetry]]],
                 selected: str | None, map_width_px: int) -> None:
    base = build_base_map(settings, area, {})
    fg = folium.FeatureGroup(name="traffic")
    if selected:
        for sid in feed.shapes_by_route.get(selected, ()):   # the line's path, faint, under the traffic
            folium.PolyLine([[lat, lon] for lon, lat in feed.shapes[sid].lonlat], color="#9e9e9e", weight=7,
                            opacity=0.35).add_to(fg)
    for rid, buses in by_line.items():
        if selected and rid != selected:
            continue
        line = feed.routes[rid].short_name
        for bid, rows in buses.items():
            for a, b, kmh, when in traffic_segments(rows):
                folium.PolyLine([a, b], color=speed_colour(kmh), weight=5, opacity=0.9,
                                tooltip=f"Line {line} · bus {bid} · {kmh:.0f} km/h"
                                        + (f" · {when.astimezone(settings.tz):%H:%M}" if when else "")).add_to(fg)
    box = feed.route_bounds(selected) if selected else None
    if box:
        center, zoom = view_for(box, map_width_px, settings.map_height, settings.max_zoom)
    else:
        (s, w), (n, e) = area
        center, zoom = ((s + n) / 2, (w + e) / 2), 0
    st_folium(base, feature_group_to_add=fg, key="health_map", height=settings.map_height,
              use_container_width=True, center=center, zoom=zoom, returned_objects=[])
