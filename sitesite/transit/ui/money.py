"""Money page: revenue and charges over a period, from GET /charges (and the pass catalog for product names)."""
from __future__ import annotations

import functools
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import altair as alt
import pandas as pd
import streamlit as st

from . import data

# Rides and pass sales: categorical slots 1 and 2 of the validated palette (blue, orange; checked light and dark)
KIND_LABEL = {"ride": "Rides", "pass_renewal": "Pass sales"}
KIND_COLOR = {"Rides": "#2a78d6", "Pass sales": "#eb6834"}
BAR_COLOR = "#2a78d6"
COUNTED = ("completed", "in_progress")   # money that is (or will be) taken; covered_by_pass and cancelled aren't
STATUS_LABEL = {"completed": "completed", "in_progress": "in progress (not settled yet)",
                "covered_by_pass": "covered by a pass", "cancelled": "cancelled"}
PERIODS = ("Today", "7 days", "30 days", "All time", "Custom")
K_PERIOD, K_RANGE = "money_period", "money_range"


# ---------------------------------------------------------------- period
def period_bounds(choice: str, custom: tuple[date, date] | None, tz: ZoneInfo, now: datetime
                  ) -> tuple[datetime | None, datetime | None]:
    """[start, end) in UTC for a period choice; None = open. Rounded so the cache can be reused. Pure."""
    local = now.astimezone(tz)
    if choice == "Today":
        start = datetime.combine(local.date(), time(), tz)
    elif choice == "7 days":
        start = datetime.combine(local.date() - timedelta(days=6), time(), tz)
    elif choice == "30 days":
        start = datetime.combine(local.date() - timedelta(days=29), time(), tz)
    elif choice == "Custom" and custom:
        a, b = custom
        return (datetime.combine(a, time(), tz).astimezone(timezone.utc),
                datetime.combine(b + timedelta(days=1), time(), tz).astimezone(timezone.utc))
    else:
        return None, None
    return start.astimezone(timezone.utc), None


def _iso(d: datetime | None) -> str | None:
    return d.strftime("%Y-%m-%dT%H:%M:%SZ") if d else None


def _frame(records: list[dict], tz: ZoneInfo, start: datetime | None, end: datetime | None) -> pd.DataFrame:
    """Charges as a table, kept to [start, end) (the server may not filter yet)."""
    df = pd.DataFrame(records)
    if df.empty:
        return df
    df["started"] = pd.to_datetime(df.get("startedAt"), utc=True, errors="coerce")
    if start is not None:
        df = df[df["started"] >= start]
    if end is not None:
        df = df[df["started"] < end]
    df = df.copy()
    df["amount"] = pd.to_numeric(df.get("amount"), errors="coerce").fillna(0.0)
    df["passengers"] = pd.to_numeric(df.get("passengers"), errors="coerce").fillna(0).astype(int)
    df["kind_label"] = df["kind"].map(KIND_LABEL).fillna(df["kind"])
    df["counted"] = df["status"].isin(COUNTED)
    df["local"] = df["started"].dt.tz_convert(str(tz))
    return df


# ---------------------------------------------------------------- page
def render_money_view(tz: ZoneInfo, *, live: bool, every_s: float) -> None:
    st.title("Money")
    c1, c2 = st.columns([3, 2], vertical_alignment="bottom")
    choice = c1.segmented_control("Period", PERIODS, key=K_PERIOD, default="7 days") or "7 days"
    custom = None
    if choice == "Custom":
        today = datetime.now(tz).date()
        picked = c2.date_input("From – to", value=(today - timedelta(days=6), today), key=K_RANGE)
        custom = tuple(picked) if isinstance(picked, (list, tuple)) and len(picked) == 2 else None
    _fragment(every_s if live else None)(tz, choice, custom)


@functools.lru_cache(maxsize=None)
def _fragment(every_s: float | None):
    return st.fragment(run_every=every_s)(_money)


def _money(tz: ZoneInfo, choice: str, custom) -> None:
    data.require_server()   # server gone: the whole app shows the 'connection lost' banner
    now = datetime.now(timezone.utc)
    start, end = period_bounds(choice, custom, tz, now)
    records, err = data.charges(_iso(start), _iso(end))
    if err:
        st.error(f"Charges unavailable: {err}")
        return
    df = _frame(records or [], tz, start, end)
    if df.empty:
        st.info("No charges in this period.")
        return
    currencies = sorted(df["currency"].dropna().unique())
    cur = currencies[0] if len(currencies) == 1 else ""
    if len(currencies) > 1:
        st.warning(f"Several currencies ({', '.join(currencies)}): totals add them as they are.")

    counted = df[df["counted"]]
    rides = counted[counted["kind"] == "ride"]
    passes = counted[counted["kind"] == "pass_renewal"]
    money = lambda v: f"{v:,.2f} {cur}".strip()   # noqa: E731

    t = st.columns(4)
    t[0].metric("Revenue", money(counted["amount"].sum()), help="Completed and in-progress charges.")
    t[1].metric("Rides paid", f"{len(rides)}", help=f"{money(rides['amount'].sum())} from rides")
    t[2].metric("Pass sales", money(passes["amount"].sum()), help=f"{len(passes)} passes sold")
    t[3].metric("Average ride fare", money(rides["amount"].mean()) if len(rides) else "—")
    t = st.columns(4)
    t[0].metric("Passengers", int(counted["passengers"].sum()))
    open_ = df[df["status"] == "in_progress"]
    t[1].metric("Not settled yet", money(open_["amount"].sum()), help=f"{len(open_)} rides still in progress")
    t[2].metric("Covered by a pass", int((df["status"] == "covered_by_pass").sum()), help="Rides taken on a pass")
    t[3].metric("Cancelled", int((df["status"] == "cancelled").sum()))

    span = (df["started"].max() - df["started"].min()) if start is None else ((end or now) - start)
    hourly = span <= pd.Timedelta(days=2)
    st.caption(f"{len(df)} charges · {_label(start, end, tz)} · updated {now.astimezone(tz):%H:%M:%S}")

    left, right = st.columns([3, 2], gap="medium")
    with left:
        st.subheader("Revenue over time")
        _over_time(counted, hourly, cur)
    with right:
        st.subheader("Revenue by line")
        _by_line(rides, cur)

    b1, b2, b3 = st.columns(3, gap="medium")
    with b1:
        st.markdown("**By status**")
        _breakdown(df, "status", lambda s: STATUS_LABEL.get(s, s), cur)
    with b2:
        st.markdown("**By payment method**")
        _breakdown(counted, "source_kind", lambda s: {"card": "card", "pass": "pass"}.get(s, "unknown"), cur)
    with b3:
        st.markdown("**Passes sold**")
        _pass_products(passes, cur)


def _label(start: datetime | None, end: datetime | None, tz: ZoneInfo) -> str:
    if start is None:
        return "all time"
    a = start.astimezone(tz).strftime("%Y-%m-%d")
    b = (end - timedelta(seconds=1)).astimezone(tz).strftime("%Y-%m-%d") if end else "now"
    return f"{a} – {b}"


def _over_time(counted: pd.DataFrame, hourly: bool, cur: str) -> None:
    if counted.empty:
        st.caption("No revenue in this period.")
        return
    unit = "h" if hourly else "D"
    g = (counted.assign(bucket=counted["local"].dt.floor(unit).dt.tz_localize(None))
         .groupby(["bucket", "kind_label"], as_index=False).agg(amount=("amount", "sum"), charges=("amount", "size")))
    kinds = [k for k in KIND_COLOR if k in set(g["kind_label"])]
    chart = (
        alt.Chart(g)
        .mark_bar(cornerRadiusTopLeft=4, cornerRadiusTopRight=4)
        .encode(
            x=alt.X("bucket:T", title=None, timeUnit="yearmonthdatehours" if hourly else "yearmonthdate",
                    axis=alt.Axis(grid=False, format="%H:%M" if hourly else "%b %d")),
            y=alt.Y("sum(amount):Q", title=cur or "amount", axis=alt.Axis(gridOpacity=0.3)),
            color=alt.Color("kind_label:N", title=None, scale=alt.Scale(domain=kinds, range=[KIND_COLOR[k] for k in kinds]),
                            legend=alt.Legend(orient="top")),
            order=alt.Order("kind_label:N", sort="descending"),
            tooltip=[alt.Tooltip("bucket:T", title="Hour" if hourly else "Day", format="%Y-%m-%d %H:%M" if hourly else "%Y-%m-%d"),
                     alt.Tooltip("kind_label:N", title="Kind"),
                     alt.Tooltip("amount:Q", title=f"Revenue ({cur})" if cur else "Revenue", format=",.2f"),
                     alt.Tooltip("charges:Q", title="Charges")],
        )
        .properties(height=280)
    )
    st.altair_chart(chart, width="stretch")
    with st.expander("Data"):
        table = g.pivot_table(index="bucket", columns="kind_label", values="amount", aggfunc="sum", fill_value=0)
        st.dataframe(table.sort_index(ascending=False), width="stretch")


def _by_line(rides: pd.DataFrame, cur: str) -> None:
    if rides.empty:
        st.caption("No ride revenue in this period.")
        return
    g = (rides.assign(line=rides["line"].fillna("—"))
         .groupby("line", as_index=False).agg(amount=("amount", "sum"), rides=("amount", "size"),
                                               passengers=("passengers", "sum"))
         .sort_values("amount", ascending=False))
    top = g.head(15)
    chart = (
        alt.Chart(top)
        .mark_bar(color=BAR_COLOR, cornerRadiusTopRight=4, cornerRadiusBottomRight=4, height={"band": 0.7})
        .encode(
            y=alt.Y("line:N", sort="-x", title=None),
            x=alt.X("amount:Q", title=cur or "amount", axis=alt.Axis(gridOpacity=0.3)),
            tooltip=[alt.Tooltip("line:N", title="Line"),
                     alt.Tooltip("amount:Q", title=f"Revenue ({cur})" if cur else "Revenue", format=",.2f"),
                     alt.Tooltip("rides:Q", title="Rides"), alt.Tooltip("passengers:Q", title="Passengers")],
        )
        .properties(height=max(120, 26 * len(top)))
    )
    st.altair_chart(chart, width="stretch")
    if len(g) > len(top):
        st.caption(f"Top {len(top)} of {len(g)} lines.")
    with st.expander("Data"):
        st.dataframe(g.rename(columns={"line": "Line", "amount": f"Revenue {cur}".strip(), "rides": "Rides",
                                       "passengers": "Passengers"}), hide_index=True, width="stretch")


def _breakdown(df: pd.DataFrame, column: str, label, cur: str) -> None:
    if df.empty or column not in df:
        st.caption("Nothing to show.")
        return
    g = (df.assign(group=df[column].map(label))
         .groupby("group", as_index=False).agg(charges=("amount", "size"), amount=("amount", "sum"))
         .sort_values("amount", ascending=False))
    g["amount"] = g["amount"].map(lambda v: f"{v:,.2f} {cur}".strip())
    st.dataframe(g.rename(columns={"group": column.replace("_", " ").capitalize(), "charges": "Charges",
                                   "amount": "Amount"}),
                 hide_index=True, width="stretch", height=(len(g) + 1) * 35 + 3)


def _pass_products(passes: pd.DataFrame, cur: str) -> None:
    if passes.empty:
        st.caption("No passes sold in this period.")
        return
    catalog, _ = data.pass_catalog()
    names = {p.get("id"): p.get("name") for p in catalog or []}
    g = (passes.assign(product=passes.get("pass_product_id").map(lambda i: names.get(i) or "unknown product"))
         .groupby("product", as_index=False).agg(sold=("amount", "size"), amount=("amount", "sum"))
         .sort_values("amount", ascending=False))
    g["amount"] = g["amount"].map(lambda v: f"{v:,.2f} {cur}".strip())
    st.dataframe(g.rename(columns={"product": "Pass", "sold": "Sold", "amount": "Revenue"}),
                 hide_index=True, width="stretch", height=(min(len(g), 10) + 1) * 35 + 3)
