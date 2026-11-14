from __future__ import annotations

import html

from ..domain import Route

ICON_PX = 44
_BODY = {"0": (30, 30, 8), "11": (40, 26, 13)}   # route_type -> (w, h, radius)
_DEFAULT_BODY = (30, 30, 15)


def vehicle_html(route: Route, heading: float | None, big: bool) -> str:
    w, h, rad = _BODY.get(route.route_type, _DEFAULT_BODY)
    glow = "0 0 0 3px rgba(255,0,0,.6)," if big else ""
    scale = 1.3 if big else 1
    color, tcolor, label = route.color, route.text_color, html.escape(route.short_name)
    arrow = "" if heading is None else (
        f"<div style='position:absolute;inset:0;transform:rotate({heading:.0f}deg);'>"
        f"<div style='position:absolute;left:50%;top:0;margin-left:-8px;"
        f"border-left:8px solid transparent;border-right:8px solid transparent;"
        f"border-bottom:12px solid {color};filter:drop-shadow(0 0 1px #fff);'></div></div>"
    )
    return (
        f"<div style='position:relative;width:{ICON_PX}px;height:{ICON_PX}px;transform:scale({scale});'>"
        + arrow +
        f"<div style='position:absolute;left:50%;top:50%;width:{w}px;height:{h}px;"
        f"margin:-{h // 2}px 0 0 -{w // 2}px;border-radius:{rad}px;background:{color};"
        f"border:3px solid #fff;box-shadow:{glow}0 1px 5px rgba(0,0,0,.65);box-sizing:border-box;"
        f"display:flex;align-items:center;justify-content:center;color:{tcolor};"
        f"font:700 12px sans-serif;'>{label}</div></div>"
    )