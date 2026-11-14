"""Sightings page: diagnostics of the detection system. A sighting is a phone hearing a bus's beacon and telling
the server; the server accepts it or refuses it (with a reason). Sightings carry no location, so the map places
each one where its bus was at that moment (the bus position closest in time, within MATCH_S)."""
from __future__ import annotations

import bisect
import functools
from datetime import datetime, timedelta, timezone
from typing import Callable
from zoneinfo import ZoneInfo

import altair as alt
import folium
import pandas as pd
import streamlit as st
from folium.plugins import HeatMap
from streamlit_folium import st_folium

from ..config import Bounds, Settings
from ..gtfs import Feed
from ..geometry import view_for
from ..mapview.layers import add_background_lines, add_hit_lines, add_route_lines, build_base_map
from . import data

WINDOWS = {"1 h": 60, "6 h": 360, "24 h": 1440, "All kept": None}
LINES_OPACITY = 0.35            # the network under the sightings: pale, but hovering it still works
MATCH_S = 60                     # a bus position further from the sighting in time isn't used for its place
# accepted / rejected are states: status palette (good, critical), always shown with their label
OUTCOME_COLOR = {"accepted": "#0ca30c", "rejected": "#d03b3b"}
BAR_COLOR = "#2a78d6"
REASONS = {
    "bad_token": "bad token", "slot_too_old": "slot too old", "slot_ahead": "slot ahead (phone clock fast)",
    "clock_not_synced": "phone clock not synced", "unknown_bus": "unknown bus", "no_beacon": "bus has no beacon",
}
SHOW = {"All": None, "Accepted": True, "Rejected": False}
K_WINDOW, K_SHOW, K_LINE, K_ONLY_LINE = "sight_window", "sight_show", "sight_line", "sight_only_line"
MAP_WIDTH_PX = 700   # rough width of the map column, only to pick a zoom that fits one line


def bucket_for(minutes: int | None) -> tuple[str, str]:
    """(pandas floor unit, label) for the time chart of a window. Pure."""
    if minutes is not None and minutes <= 60:
        return "5min", "5 min"
    if minutes is not None and minutes <= 360:
        return "15min", "15 min"
    return "h", "hour"


def place_sightings(df: pd.DataFrame, history: dict[int, list]) -> pd.DataFrame:
    """Adds lat/lon to each sighting: its bus's position nearest in time (server clock), within MATCH_S."""
    tracks = {}
    for bid, rows in history.items():
        pts = sorted((t.received_at.timestamp(), t.lat, t.lon) for t in rows or [] if t.received_at and t.latlon)
        if pts:
            tracks[bid] = ([p[0] for p in pts], pts)
    lats, lons = [], []
    for bid, when in zip(df["bus_id"], df["received"]):
        lat = lon = None
        tr = tracks.get(bid) if pd.notna(bid) else None
        if tr is not None and pd.notna(when):
            ts, pts = tr
            x = when.timestamp()
            i = bisect.bisect_left(ts, x)
            near = [j for j in (i - 1, i) if 0 <= j < len(ts)]
            j = min(near, key=lambda k: abs(ts[k] - x))
            if abs(ts[j] - x) <= MATCH_S:
                lat, lon = pts[j][1], pts[j][2]
        lats.append(lat)
        lons.append(lon)
    return df.assign(lat=lats, lon=lons)


# ---------------------------------------------------------------- page
def render_sightings_view(settings: Settings, area: Bounds, line_of: Callable[[int | None], str], feed: Feed,
                          hover_payload: dict, *, live: bool) -> None:
    st.title("Sightings")
    _fragment(settings.sightings_refresh_s if live else None)(settings, area, line_of, feed, hover_payload)


@functools.lru_cache(maxsize=None)
def _fragment(every_s: float | None):
    return st.fragment(run_every=every_s)(_sightings)


def _sightings(settings: Settings, area: Bounds, line_of: Callable[[int | None], str], feed: Feed,
               hover_payload: dict) -> None:
    data.require_server()   # server gone: the whole app shows the 'connection lost' banner
    tz = settings.tz
    col_ctl, col_map, col_side = st.columns([1, 4, 3], gap="medium")
    with col_ctl:
        window = st.radio("Window", list(WINDOWS), key=K_WINDOW, index=1)
        show = st.radio("On the map", list(SHOW), key=K_SHOW, index=0)
        one_line = st.selectbox("Line on the map", [""] + feed.sorted_route_ids(), key=K_LINE,
                                format_func=lambda r: "All lines (pale)" if not r else feed.routes[r].label)
        only_line = st.checkbox("Show only this line's sightings", key=K_ONLY_LINE, disabled=not one_line,
                                help="Counts, charts and the map then cover this line alone.") and bool(one_line)

    records, err = data.sightings_all()
    if err:
        st.error(f"Sightings unavailable: {err}")
        return
    df = pd.DataFrame(records or [])
    now = datetime.now(timezone.utc)
    minutes = WINDOWS[window]
    if not df.empty:
        df["received"] = pd.to_datetime(df["received_at"], utc=True, errors="coerce")
        if minutes is not None:
            df = df[df["received"] >= now - timedelta(minutes=minutes)]
    if df.empty:
        with col_map:
            st.info("No sightings in this window.")
        return
    df = df.copy()
    df["outcome"] = df["accepted"].map({True: "accepted", False: "rejected"}).fillna("rejected")
    df["reason"] = df["reject_reason"].map(lambda r: REASONS.get(r, r) if r else None)
    df["rssi"] = pd.to_numeric(df["rssi"], errors="coerce")
    buses, _ = data.buses()
    route_of = {b.bus_id: b.route_id for b in buses or []}
    df["line"] = df["bus_id"].map(lambda b: line_of(route_of.get(int(b))) if pd.notna(b) and int(b) in route_of
                                  else "unknown bus")

    if only_line:
        df = df[df["line"] == feed.routes[one_line].short_name]
        if df.empty:
            with col_map:
                st.info(f"No sightings on line {feed.routes[one_line].short_name} in this window.")
            return
    accepted = int((df["outcome"] == "accepted").sum())
    with col_ctl:
        st.divider()
        st.metric("Sightings", len(df))
        st.metric("Accepted", f"{accepted / len(df):.0%}")
        st.metric("Rejected", len(df) - accepted)
        st.metric("Users", df["user_id"].nunique())
        st.metric("Buses", df["bus_id"].nunique())
        st.caption(f"Updated {now.astimezone(tz):%H:%M:%S}")

    with col_map:
        _hot_spots(settings, area, df, route_of, SHOW[show], now, minutes, feed, hover_payload, one_line or None)
    with col_side:
        st.subheader("Accepted vs rejected")
        _over_time(df, minutes)
        st.subheader("Why rejected")
        _reasons(df)

    st.subheader("By line")
    _by_group(df, "line", "Line")
    with st.expander("By bus"):
        _by_group(df, "bus_id", "Bus")


def _hot_spots(settings: Settings, area: Bounds, df: pd.DataFrame, route_of: dict, accepted: bool | None,
               now: datetime, minutes: int | None, feed: Feed, hover_payload: dict, one_line: str | None) -> None:
    ids = tuple(sorted(int(b) for b in df["bus_id"].dropna().unique() if int(b) in route_of))
    oldest = df["received"].min()
    start = (oldest - timedelta(seconds=MATCH_S)) if minutes is None else now - timedelta(minutes=minutes, seconds=MATCH_S)
    history = data.recent_history(ids, start.replace(second=0, microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")) if ids else {}
    placed = place_sightings(df, {b: rows for b, (rows, _e) in history.items()})
    if accepted is not None:
        placed = placed[placed["accepted"] == accepted]
    pts = placed.dropna(subset=["lat", "lon"])

    # the network, pale, under the heat map; hovering a line shows its name and stops (same as the Map page)
    # with one line picked: only that line, drawn clearly, and the map zooms to it (easier to read)
    # hover names the lines in the corner box, but doesn't thicken them or pop up their stops here
    base = build_base_map(settings, area, {}, highlight_px=0)
    lines = folium.FeatureGroup(name="lines")
    ids_drawn = [one_line] if one_line else feed.drawable_route_ids()
    if one_line:
        add_route_lines(lines, feed, ids_drawn, settings.line_weight_selected, opacity=0.9)
        add_hit_lines(lines, feed, ids_drawn, settings.hit_px)
    else:   # the whole network: one pale layer that is also the hover target
        add_background_lines(lines, feed, ids_drawn, settings.line_weight, LINES_OPACITY, settings.hit_px)
    lines.add_to(base)
    box = feed.route_bounds(one_line) if one_line else None
    if box:
        center, zoom = view_for(box, MAP_WIDTH_PX, settings.map_height, settings.max_zoom)
    else:
        (s_, w_), (n_, e_) = area
        center, zoom = ((s_ + n_) / 2, (w_ + e_) / 2), 0
    fg = folium.FeatureGroup(name="sightings")
    if len(pts):
        HeatMap(pts[["lat", "lon"]].values.tolist(), radius=18, blur=22, min_opacity=0.35).add_to(fg)
        for _, r in pts[pts["outcome"] == "rejected"].iterrows():   # rejected ones also as dots: worth a look
            folium.CircleMarker((r["lat"], r["lon"]), radius=4, color="#ffffff", weight=1, fill=True,
                                fill_color=OUTCOME_COLOR["rejected"], fill_opacity=0.9,
                                tooltip=f"Rejected · {r['reason'] or '—'} · bus {r['bus_id']} · line {r['line']}").add_to(fg)
    st_folium(base, feature_group_to_add=fg, key="sightings_map", height=settings.map_height,
              use_container_width=True, center=center, zoom=zoom, returned_objects=[])
    st.caption(f"Where sightings happened: {len(pts)} of {len(placed)} placed at their bus's position "
               f"(within {MATCH_S} s); red dots are rejected ones.")


def _over_time(df: pd.DataFrame, minutes: int | None) -> None:
    unit, label = bucket_for(minutes)
    g = (df.assign(bucket=df["received"].dt.floor(unit).dt.tz_localize(None))
         .groupby(["bucket", "outcome"], as_index=False).size())
    outcomes = [o for o in OUTCOME_COLOR if o in set(g["outcome"])]
    st.altair_chart(
        alt.Chart(g).mark_bar(cornerRadiusTopLeft=3, cornerRadiusTopRight=3).encode(
            x=alt.X("bucket:T", title=None, axis=alt.Axis(grid=False)),
            y=alt.Y("size:Q", title=f"sightings per {label}", axis=alt.Axis(gridOpacity=0.3)),
            color=alt.Color("outcome:N", title=None, legend=alt.Legend(orient="top"),
                            scale=alt.Scale(domain=outcomes, range=[OUTCOME_COLOR[o] for o in outcomes])),
            order=alt.Order("outcome:N"),
            tooltip=[alt.Tooltip("bucket:T", title="From", format="%H:%M"), alt.Tooltip("outcome:N", title=""),
                     alt.Tooltip("size:Q", title="Sightings")],
        ).properties(height=220),
        width="stretch",
    )


def _reasons(df: pd.DataFrame) -> None:
    rej = df[df["outcome"] == "rejected"]
    if rej.empty:
        st.caption("None rejected in this window.")
        return
    g = rej.assign(reason=rej["reason"].fillna("no reason given")).groupby("reason", as_index=False).size()
    st.altair_chart(
        alt.Chart(g).mark_bar(color=OUTCOME_COLOR["rejected"], cornerRadiusTopRight=3, cornerRadiusBottomRight=3).encode(
            # 40 px a reason and space between bars, so the labels never overlap; long labels aren't cut
            y=alt.Y("reason:N", sort="-x", title=None, scale=alt.Scale(paddingInner=0.35),
                    axis=alt.Axis(labelLimit=260, labelPadding=6)),
            x=alt.X("size:Q", title="rejected sightings", axis=alt.Axis(gridOpacity=0.3, tickMinStep=1)),
            tooltip=[alt.Tooltip("reason:N", title="Reason"), alt.Tooltip("size:Q", title="Sightings")],
        ).properties(height=max(140, 40 * len(g))),
        width="stretch",
    )


def _by_group(df: pd.DataFrame, column: str, title: str) -> None:
    g = (df.groupby(column)
         .agg(sightings=("outcome", "size"), accepted=("accepted", "sum"), users=("user_id", "nunique"),
              rssi=("rssi", "mean"), not_synced=("clock_synced", lambda s: int((s == False).sum())))  # noqa: E712
         .reset_index().sort_values("sightings", ascending=False))
    g["accepted %"] = (g["accepted"] / g["sightings"]).map("{:.0%}".format)
    g["avg RSSI (dBm)"] = g["rssi"].map(lambda v: "—" if pd.isna(v) else f"{v:.0f}")
    left, right = st.columns([3, 2], gap="medium")
    with left:
        top = g.head(15)
        st.altair_chart(
            alt.Chart(top).mark_bar(color=BAR_COLOR, cornerRadiusTopRight=3, cornerRadiusBottomRight=3).encode(
                y=alt.Y(f"{column}:N", sort="-x", title=None, scale=alt.Scale(paddingInner=0.3),
                        axis=alt.Axis(labelLimit=200, labelPadding=6)),
                x=alt.X("sightings:Q", title="sightings", axis=alt.Axis(gridOpacity=0.3, tickMinStep=1)),
                tooltip=[alt.Tooltip(f"{column}:N", title=title), alt.Tooltip("sightings:Q", title="Sightings"),
                         alt.Tooltip("accepted %:N", title="Accepted"), alt.Tooltip("users:Q", title="Users")],
            ).properties(height=max(120, 32 * len(top))),
            width="stretch",
        )
    with right:
        st.dataframe(g[[column, "sightings", "accepted %", "users", "avg RSSI (dBm)", "not_synced"]].rename(
            columns={column: title, "sightings": "Sightings", "accepted %": "Accepted", "users": "Users",
                     "not_synced": "Clock not synced"}),
            hide_index=True, width="stretch", height=(min(len(g), 10) + 1) * 35 + 18)
