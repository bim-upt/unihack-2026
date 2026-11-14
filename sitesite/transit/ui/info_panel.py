from __future__ import annotations

import pandas as pd
import streamlit as st

from ..domain import Route, Vehicle
from ..providers import DetailsProvider


def render_network(n_routes: int, n_services: int) -> None:
    st.subheader("Network")
    st.metric("Routes", n_routes)
    st.metric("Active services today", n_services)
    st.info("Click a route on the map (or use the sidebar).")


def render_route(route: Route, vehicles: list[Vehicle], selected_id: str | None,
                 details: DetailsProvider, notice: str | None = None) -> None:
    st.subheader(f"{route.mode_name} {route.short_name}")
    st.caption(route.long_name)
    if notice:
        st.warning(notice)
    st.metric("Vehicles active now", len(vehicles))
    if not vehicles:
        st.warning("No active trips at this time (try the time override).")
        return

    v = next((x for x in vehicles if x.vehicle_id == selected_id), None)
    if v is None:
        st.info("Click a vehicle on the map.")
        return

    st.divider()
    st.subheader(f"Vehicle {v.label or v.vehicle_id}")
    speed = f"{v.speed_kmh:.1f} km/h ({v.source})" if v.speed_kmh is not None else "n/a"
    for k, val in {
        "Source": v.source,
        "Trip": v.trip_id or "—",
        "Heading to": v.headsign or "—",
        "Previous stop": v.prev_stop,
        "Next stop": v.next_stop,
        "Speed": speed,
    }.items():
        st.write(f"**{k}:** {val}")
    st.table(pd.Series(details.details(v), name="Value").astype(str))