from __future__ import annotations

from datetime import date

import pandas as pd

_WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")


def active_services(calendar: pd.DataFrame, calendar_dates: pd.DataFrame, day: date) -> frozenset[str]:
    ymd = day.strftime("%Y%m%d")
    out: set[str] = set()
    if not calendar.empty:
        col = _WEEKDAYS[day.weekday()]
        mask = (calendar[col] == "1") & (calendar["start_date"] <= ymd) & (calendar["end_date"] >= ymd)
        out = set(calendar.loc[mask, "service_id"])
    if not calendar_dates.empty:
        ex = calendar_dates[calendar_dates["date"] == ymd]
        out |= set(ex.loc[ex["exception_type"] == "1", "service_id"])
        out -= set(ex.loc[ex["exception_type"] == "2", "service_id"])
    return frozenset(out)