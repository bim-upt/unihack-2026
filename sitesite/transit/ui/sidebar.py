from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import streamlit as st

from ..config import Settings
from ..gtfs import Feed
from . import state


@dataclass(frozen=True)
class Controls:
    now: datetime
    simulated: bool


def render_sidebar(feed: Feed, settings: Settings) -> Controls:
    with st.sidebar:
        st.header("Controls")
        st.selectbox(
            "Route",
            [""] + feed.sorted_route_ids(),
            key=state.K_ROUTE,
            format_func=lambda r: "— none —" if not r else feed.routes[r].label,
            on_change=state.on_route_change,
        )
        st.button("Show all routes", on_click=state.show_all_routes)
        simulated = st.checkbox("Override time (simulate)", value=False)
        now = datetime.now(settings.tz)
        if simulated:
            tt = st.time_input("Time", value=now.time().replace(microsecond=0))
            now = now.replace(hour=tt.hour, minute=tt.minute, second=tt.second)
        st.caption(f"Reference time: {now:%Y-%m-%d %H:%M:%S}")
        st.button("Refresh")
    return Controls(now=now, simulated=simulated)