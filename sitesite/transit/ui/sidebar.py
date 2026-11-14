from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import streamlit as st

from ..config import Settings
from ..gtfs import Feed
from . import data, state

SOURCE_LABELS = {"live": "Live (API)", "schedule": "Schedule (GTFS)"}
VIEW_LABELS = {"map": "🗺️ Map", "fleet": "🚍 Fleet", "health": "🚦 Line health", "devices": "📡 Devices",
               "users": "👤 Users", "money": "💰 Money"}


@dataclass(frozen=True)
class Controls:
    now: datetime
    simulated: bool
    source: str          # "live" | "schedule"
    view: str            # "map" | "devices"
    live_updates: bool = True


def render_sidebar(feed: Feed, settings: Settings) -> Controls:
    with st.sidebar:
        st.radio("View", list(VIEW_LABELS), key=state.K_VIEW, format_func=VIEW_LABELS.get, horizontal=True)
        view = state.view()
        if view != "map":
            label = {
                "fleet": f"every {settings.fleet_refresh_s:g} s",
                "money": f"every {settings.money_refresh_s:g} s",
                "health": f"every {settings.health_refresh_s:g} s",
                "devices": f"list every {settings.devices_list_refresh_s:g} s, "
                           f"selected device every {settings.live_refresh_s:g} s",
                "users": f"list every {settings.devices_list_refresh_s:g} s, "
                         f"selected user every {settings.users_refresh_s:g} s",
            }[view]
            live_updates = st.toggle(f"Live updates ({label})", key=state.K_LIVE_UPDATES)
            st.button("Refresh", on_click=data.clear)
            return Controls(now=datetime.now(settings.tz), simulated=False, source=state.vehicle_source(), view=view,
                            live_updates=live_updates)

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
        live_updates = st.toggle(f"Live updates (every {settings.live_refresh_s:g} s)", key=state.K_LIVE_UPDATES)
        if not simulated:
            st.caption("Time: now")
        else:
            st.caption(f"Reference time: {now:%Y-%m-%d %H:%M:%S}")
        st.button("Refresh", on_click=data.clear)
    return Controls(now=now, simulated=simulated, source=source, view=view, live_updates=live_updates)