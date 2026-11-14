from __future__ import annotations

import colorsys
from typing import Iterable

import pandas as pd


def clean_hex(series: pd.Series) -> pd.Series:
    s = series.fillna("").astype(str).str.strip().str.lstrip("#")
    return s.where(s.str.fullmatch(r"[0-9A-Fa-f]{6}"), "")


def hex_to_rgb(h: str) -> tuple[float, float, float]:
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))


def rgb_to_hex(r: float, g: float, b: float) -> str:
    return "#%02x%02x%02x" % (round(r * 255), round(g * 255), round(b * 255))


def luminance(h: str) -> float:
    def lin(c: float) -> float:
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (lin(c) for c in hex_to_rgb(h))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def readable_text_color(bg_hex: str) -> str:
    return "#111111" if luminance(bg_hex) >= 0.5 else "#ffffff"


def contrast_path_colors(route_hex: str) -> tuple[str, str]:
    h, _l, _s = colorsys.rgb_to_hls(*hex_to_rgb(route_hex))
    light_route = luminance(route_hex) >= 0.4
    path = rgb_to_hex(*colorsys.hls_to_rgb((h + 0.5) % 1.0, 0.27 if light_route else 0.68, 0.95))
    casing = "#ffffff" if luminance(path) < 0.4 else "#222222"
    return path, casing


def distinct_colors(ids: Iterable[str]) -> dict[str, str]:
    out = {}
    for i, rid in enumerate(sorted(ids)):
        h = (i * 0.61803398875) % 1.0
        out[rid] = rgb_to_hex(*colorsys.hls_to_rgb(h, (0.34, 0.46)[i % 2], 0.92))
    return out