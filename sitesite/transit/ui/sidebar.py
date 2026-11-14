from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import streamlit as st

from ..config import Settings
from ..gtfs import Feed
from . import data, state

SOURCE_LABELS = {"live": "Live (API)", "schedule": "Schedule (GTFS)"}


@dataclass(frozen=True)
class Controls:
    now: datetime
    simulated: bool
    source: str          # "live" | "schedule"


def render_sidebar(feed: Feed, settings: Settings) -> Controls:
    with st.sidebar:
        st.header("Controls")
        st.radio(
            "Vehicles",
            list(SOURCE_LABELS),
            key=state.K_SOURCE,
            format_func=SOURCE_LABELS.get,
            on_change=state.on_source_change,
            horizontal=True,
        )
        st.selectbox(
            "Route",
            [""] + feed.sorted_route_ids(),
            key=state.K_ROUTE,
            format_func=lambda r: "— none —" if not r else feed.routes[r].label,
            on_change=state.on_route_change,
        )
        st.button("Show all routes", on_click=state.show_all_routes)

        source = state.vehicle_source()
        now = datetime.now(settings.tz)
        simulated = False
        if source == "schedule":
            simulated = st.checkbox("Override time (simulate)", value=False)
            if simulated:
                tt = st.time_input("Time", value=now.time().replace(microsecond=0))
                now = now.replace(hour=tt.hour, minute=tt.minute, second=tt.second)
        st.caption(f"Reference time: {now:%Y-%m-%d %H:%M:%S}")
        st.button("Refresh", on_click=data.clear)
    return Controls(now=now, simulated=simulated, source=source)