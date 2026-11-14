import colorsys
import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import folium
import numpy as np
import pandas as pd
import streamlit as st
from branca.element import MacroElement
from jinja2 import Template
from streamlit_folium import st_folium


#-------------------- config
GTFS_DIR = Path("gtfs")
TZ = ZoneInfo("Europe/Bucharest")
MODE = {"0": "Tram", "3": "Bus", "11": "Trolleybus"}
MAP_CENTER = (45.7489, 21.2087)
TILES = "OpenStreetMap"
TILE_FILTER = "grayscale(1) contrast(0.8) brightness(1.12)"
STOP_RADIUS = 8
HIT_PX = 22  

st.set_page_config(page_title="Transit Admin", layout="wide")


#------------------ hover inject - js
class HoverInfo(MacroElement):
    """Runs in the browser (no Streamlit rerun):
    - grays out the basemap tiles
    - lists lines under the cursor, highlights the nearest and shows its stops
    - shows a vehicle's path to its next stop while hovering it"""

    _template = Template(
        """
{% macro script(this, kwargs) %}
(function () {
  try {
    var map = {{ this._parent.get_name() }};
    var STOPS = {{ this.stops_json }};
    var TILE_FILTER = "{{ this.tile_filter }}";
    var HIGHLIGHT = 3;

    if (TILE_FILTER) { map.getPanes().tilePane.style.filter = TILE_FILTER; }

    var st = document.createElement("style");
    st.textContent =
      ".vpath{opacity:0;pointer-events:none !important}" +
      ".vpath.show,.vpath.sel{opacity:1}";
    document.head.appendChild(st);

    function vid(el) {
      var c = Array.prototype.find.call(el.classList, function (x) { return x.indexOf("vid-") === 0; });
      return c ? c.slice(4) : null;
    }
    function setPath(id, on) {
      document.querySelectorAll(".vp-" + id).forEach(function (p) {
        if (on) p.classList.add("show"); else p.classList.remove("show");
      });
    }
    var cont = map.getContainer();
    cont.addEventListener("mouseover", function (e) {
      var el = e.target.closest && e.target.closest(".veh");
      if (!el) return;
      var id = vid(el);
      if (id) setPath(id, true);
    });
    cont.addEventListener("mouseout", function (e) {
      var el = e.target.closest && e.target.closest(".veh");
      if (!el) return;
      if (e.relatedTarget && el.contains(e.relatedTarget)) return;
      var id = vid(el);
      if (id) setPath(id, false);
    });

    var box = L.control({position: "topright"});
    box.onAdd = function () {
      var d = L.DomUtil.create("div");
      d.style.cssText = "display:none;background:rgba(255,255,255,0.95);padding:6px 8px;" +
        "border-radius:6px;box-shadow:0 1px 5px rgba(0,0,0,0.45);font:13px sans-serif;" +
        "max-width:240px;pointer-events:none";
      box._d = d;
      return d;
    };
    box.addTo(map);

    var stopLayer = L.layerGroup().addTo(map);
    var current = null;
    var lastRun = 0;

    function pts(l) {
      var o = map.getPixelOrigin();
      var key = map.getZoom() + "," + o.x + "," + o.y;
      if (!l._pc || l._pck !== key) {
        var arr = l.getLatLngs().map(function (ll) { return map.latLngToLayerPoint(ll); });
        var minx = Infinity, miny = Infinity, maxx = -Infinity, maxy = -Infinity;
        arr.forEach(function (p) {
          if (p.x < minx) minx = p.x;
          if (p.y < miny) miny = p.y;
          if (p.x > maxx) maxx = p.x;
          if (p.y > maxy) maxy = p.y;
        });
        l._pc = arr;
        l._bb = [minx, miny, maxx, maxy];
        l._pck = key;
      }
      return l._pc;
    }

    function hits(p) {
      var res = {};
      map.eachLayer(function (l) {
        var f = l.feature;
        if (!f || !f.properties || !f.properties.hit || !l.getLatLngs) return;
        var arr = pts(l);
        var pad = (l.options.weight || 20) / 2;
        var bb = l._bb;
        if (p.x < bb[0] - pad || p.x > bb[2] + pad || p.y < bb[1] - pad || p.y > bb[3] + pad) return;
        var d = Infinity;
        for (var i = 1; i < arr.length; i++) {
          var dd = L.LineUtil.pointToSegmentDistance(p, arr[i - 1], arr[i]);
          if (dd < d) d = dd;
          if (d <= pad) break;
        }
        if (d <= pad) {
          var rid = f.properties.route_id;
          if (!(rid in res) || d < res[rid].d) {
            res[rid] = {d: d, short: f.properties.short, color: f.properties.color,
                        tcolor: f.properties.tcolor || "#ffffff"};
          }
        }
      });
      return res;
    }

    function styleRoute(rid, extra) {
      map.eachLayer(function (l) {
        var f = l.feature;
        if (!f || !f.properties || f.properties.hit || f.properties.route_id !== rid || !l.setStyle) return;
        if (l._bw === undefined) l._bw = l.options.weight;
        l.setStyle({weight: l._bw + extra});
      });
    }

    function showStops(rid, color) {
      stopLayer.clearLayers();
      (STOPS[rid] || []).forEach(function (s) {
        L.circleMarker([s[0], s[1]], {
          radius: 7, color: "#ffffff", weight: 2,
          fillColor: color, fillOpacity: 1, interactive: false
        }).addTo(stopLayer);
      });
    }

    function clearAll() {
      box._d.style.display = "none";
      if (current) { styleRoute(current, 0); current = null; }
      stopLayer.clearLayers();
    }

    function update(e) {
      var res = hits(e.layerPoint);
      var ids = Object.keys(res).sort(function (a, b) { return res[a].d - res[b].d; });
      if (!ids.length) { clearAll(); return; }
      var html = "<div style='font-weight:600;margin-bottom:3px'>Lines here</div>";
      ids.forEach(function (id) {
        html += "<span style='background:" + res[id].color + ";color:" + res[id].tcolor +
          ";padding:1px 8px;border-radius:10px;margin:2px 2px 0 0;display:inline-block;" +
          "font-weight:700;border:1px solid rgba(0,0,0,0.25)'>" + res[id].short + "</span>";
      });
      box._d.innerHTML = html;
      box._d.style.display = "block";
      if (current !== ids[0]) {
        if (current) styleRoute(current, 0);
        current = ids[0];
        styleRoute(current, HIGHLIGHT);
        showStops(current, res[current].color);
      }
    }

    map.on("mousemove", function (e) {
      var t = Date.now();
      if (t - lastRun < 50) return;
      lastRun = t;
      update(e);
    });
    map.on("mouseout", clearAll);
  } catch (err) {
    console.error("HoverInfo failed", err);
  }
})();
{% endmacro %}
"""
    )

    def __init__(self, stops_json: str, tile_filter: str):
        super().__init__()
        self._name = "HoverInfo"
        self.stops_json = stops_json
        self.tile_filter = tile_filter


# -------  colors 
def _clean_hex(series: pd.Series) -> pd.Series:
    s = series.fillna("").astype(str).str.strip().str.lstrip("#")
    return s.where(s.str.fullmatch(r"[0-9A-Fa-f]{6}"), "")


def hex_to_rgb(h: str):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))


def rgb_to_hex(r, g, b) -> str:
    return "#%02x%02x%02x" % (round(r * 255), round(g * 255), round(b * 255))


def luminance(h: str) -> float:
    """WCAG relative luminance (0 = black, 1 = white)."""
    def lin(c):
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (lin(c) for c in hex_to_rgb(h))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast_path_colors(route_hex: str):
    """(path color, casing color) that stand out against the route color:
    complementary hue, with lightness flipped away from the route's."""
    r, g, b = hex_to_rgb(route_hex)
    h, _l, _s = colorsys.rgb_to_hls(r, g, b)
    light_route = luminance(route_hex) >= 0.4
    path = rgb_to_hex(*colorsys.hls_to_rgb((h + 0.5) % 1.0, 0.27 if light_route else 0.68, 0.95))
    casing = "#ffffff" if luminance(path) < 0.4 else "#222222"
    return path, casing


def distinct_colors(route_ids) -> dict:
    """Fallback only: vivid, well-separated colors for routes without a GTFS color."""
    out = {}
    for i, rid in enumerate(sorted(route_ids)):
        h = (i * 0.61803398875) % 1.0
        lightness = (0.34, 0.46)[i % 2]
        r, g, b = colorsys.hls_to_rgb(h, lightness, 0.92)
        out[rid] = rgb_to_hex(r, g, b)
    return out



def route_color(rid: str) -> str:
    c = rinfo.at[rid, "route_color"]
    return ("#" + c) if c else fallback[rid]


def text_color(rid: str) -> str:
    t = rinfo.at[rid, "route_text_color"]
    if t:
        return "#" + t
    return "#111111" if luminance(route_color(rid)) >= 0.5 else "#ffffff"


# ----------------- data
@st.cache_data(show_spinner="Loading GTFS...")
def load_static(gtfs_dir: str):
    base = Path(gtfs_dir)

    def read_txt(name: str, **kw) -> pd.DataFrame:
        return pd.read_csv(base / name, dtype=str, **kw)

    routes = read_txt("routes.txt")

    routes["route_color"] = _clean_hex(routes["route_color"])
    if "route_text_color" in routes.columns:
        routes["route_text_color"] = _clean_hex(routes["route_text_color"])
    else:
        routes["route_text_color"] = ""
    trips = read_txt("trips.txt")

    stops = read_txt("stops.txt").drop_duplicates("stop_id").set_index("stop_id")
    stops["stop_lat"] = stops["stop_lat"].astype(float)
    stops["stop_lon"] = stops["stop_lon"].astype(float)

    shp_df = pd.read_csv(base / "shapes.txt", dtype={"shape_id": str})
    shp_df = shp_df.sort_values(["shape_id", "shape_pt_sequence"])
    shapes = {
        sid: grp[["shape_pt_lat", "shape_pt_lon"]].to_numpy()
        for sid, grp in shp_df.groupby("shape_id")
    }

    times = pd.read_csv(
        base / "stop_times.txt",
        dtype=str,
        usecols=["trip_id", "arrival_time", "departure_time", "stop_id", "stop_sequence"],
    )
    tstr = times["arrival_time"].fillna(times["departure_time"])
    times["t"] = pd.to_timedelta(tstr.str.strip()).dt.total_seconds() 
    times["seq"] = times["stop_sequence"].astype(int)
    times["lat"] = times["stop_id"].map(stops["stop_lat"])
    times["lon"] = times["stop_id"].map(stops["stop_lon"])
    times = times.drop(columns=["arrival_time", "departure_time", "stop_sequence"])
    times = times.dropna(subset=["t", "lat", "lon"]).sort_values(["trip_id", "seq"])
    bounds = times.groupby("trip_id")["t"].agg(["min", "max"])

    #-------------- stops per route
    trip_route = trips.drop_duplicates("trip_id").set_index("trip_id")["route_id"]
    rs = times[["trip_id", "stop_id", "lat", "lon"]].copy()
    rs["route_id"] = rs["trip_id"].map(trip_route)
    rs = rs.dropna(subset=["route_id"]).drop_duplicates(["route_id", "stop_id"])
    route_stops = {
        rid: g[["lat", "lon"]].round(5).to_numpy().tolist() for rid, g in rs.groupby("route_id")
    }
    stops_json = json.dumps(route_stops)

    times = times.set_index("trip_id")

    cal = read_txt("calendar.txt") if (base / "calendar.txt").exists() else pd.DataFrame()
    cal_dates = (
        read_txt("calendar_dates.txt") if (base / "calendar_dates.txt").exists() else pd.DataFrame()
    )
    return routes, trips, stops, shapes, times, bounds, cal, cal_dates, stops_json


def active_services(cal: pd.DataFrame, cal_dates: pd.DataFrame, day: datetime) -> set:
    ymd = day.strftime("%Y%m%d")
    weekday = day.strftime("%A").lower()
    out = set()
    if not cal.empty:
        mask = (cal[weekday] == "1") & (cal["start_date"] <= ymd) & (cal["end_date"] >= ymd)
        out = set(cal.loc[mask, "service_id"])
    if not cal_dates.empty:
        today_ex = cal_dates[cal_dates["date"] == ymd]
        out |= set(today_ex.loc[today_ex["exception_type"] == "1", "service_id"])
        out -= set(today_ex.loc[today_ex["exception_type"] == "2", "service_id"])
    return out


#-------------- geometry
def project_on_shape(shape: np.ndarray, pts: np.ndarray):
    lat0 = shape[:, 0].mean()
    scale = np.array([111320.0, 111320.0 * np.cos(np.radians(lat0))])
    s = shape * scale
    seg = np.linalg.norm(np.diff(s, axis=0), axis=1)
    cum = np.concatenate([[0], np.cumsum(seg)])
    dist = np.linalg.norm((pts * scale)[:, None, :] - s[None, :, :], axis=2)
    idx = np.maximum.accumulate(dist.argmin(axis=1))  # enforce monotonic order
    return cum, cum[idx]


def bearing(lat1, lon1, lat2, lon2):
    """Compass bearing in degrees (0 = north), or None if the points coincide."""
    dy = (lat2 - lat1) * 111320.0
    dx = (lon2 - lon1) * 111320.0 * np.cos(np.radians(lat1))
    if abs(dx) < 1e-6 and abs(dy) < 1e-6:
        return None
    return float(np.degrees(np.arctan2(dx, dy)) % 360)


#------------- vehicles
def vehicles_for_route(route_id, now_s, services, trips, stops, shapes, times, bounds):
    rt = trips[(trips["route_id"] == route_id) & (trips["service_id"].isin(services))]
    rt = rt[rt["trip_id"].isin(bounds.index)]
    if rt.empty:
        return []
    bb = bounds.loc[rt["trip_id"]]
    rt = rt[((bb["min"] <= now_s) & (bb["max"] >= now_s)).to_numpy()]

    out = []
    for r in rt.itertuples():
        g = times.loc[r.trip_id]
        if isinstance(g, pd.Series) or len(g) < 2:
            continue
        t = g["t"].to_numpy()
        pts = g[["lat", "lon"]].to_numpy()
        j = int(np.clip(np.searchsorted(t, now_s, side="right") - 1, 0, len(t) - 2))
        dt = max(t[j + 1] - t[j], 1)
        frac = float(np.clip((now_s - t[j]) / dt, 0, 1))
        nxt = [float(pts[j + 1][0]), float(pts[j + 1][1])]

        shp = shapes.get(r.shape_id)
        if shp is not None and len(shp) > 1:
            cum, ds = project_on_shape(shp, pts)
            d_here = ds[j] + frac * (ds[j + 1] - ds[j])
            d_next = max(ds[j + 1], d_here)
            lat = float(np.interp(d_here, cum, shp[:, 0]))
            lon = float(np.interp(d_here, cum, shp[:, 1]))
            speed = (ds[j + 1] - ds[j]) / dt * 3.6

            d_ahead = min(d_here + 20.0, cum[-1])
            heading = bearing(
                lat, lon,
                float(np.interp(d_ahead, cum, shp[:, 0])),
                float(np.interp(d_ahead, cum, shp[:, 1])),
            )

            mid = shp[(cum > d_here) & (cum < d_next)]
            end = [float(np.interp(d_next, cum, shp[:, 0])), float(np.interp(d_next, cum, shp[:, 1]))]
            path = [[lat, lon]] + mid.tolist() + [end]
        else:
            lat = float(pts[j][0] + frac * (pts[j + 1][0] - pts[j][0]))
            lon = float(pts[j][1] + frac * (pts[j + 1][1] - pts[j][1]))
            speed = 0.0
            heading = None
            path = [[lat, lon], nxt]

        if heading is None:
            heading = bearing(lat, lon, nxt[0], nxt[1]) or 0.0

        sid_prev, sid_next = g["stop_id"].iloc[j], g["stop_id"].iloc[j + 1]
        out.append(
            dict(
                trip_id=r.trip_id,
                cls=re.sub(r"[^A-Za-z0-9_-]", "_", r.trip_id),
                lat=lat,
                lon=lon,
                heading=heading,
                path=path,
                headsign=r.trip_headsign,
                prev_stop=stops.at[sid_prev, "stop_name"] if sid_prev in stops.index else sid_prev,
                next_stop=stops.at[sid_next, "stop_name"] if sid_next in stops.index else sid_next,
                sched_speed=round(float(speed), 1),
            )
        )
    return out


def vehicle_html(short: str, color: str, text_color: str, heading: float, mode: str, big: bool) -> str:
    w, h, rad = {"0": (30, 30, 8), "11": (40, 26, 13)}.get(mode, (30, 30, 15))
    glow = "0 0 0 3px rgba(255,0,0,.6)," if big else ""
    scale = 1.3 if big else 1
    return (
        f"<div style='position:relative;width:44px;height:44px;transform:scale({scale});'>"
        f"<div style='position:absolute;left:0;top:0;right:0;bottom:0;transform:rotate({heading:.0f}deg);'>"
        f"<div style='position:absolute;left:50%;top:0;margin-left:-8px;"
        f"border-left:8px solid transparent;border-right:8px solid transparent;"
        f"border-bottom:12px solid {color};filter:drop-shadow(0 0 1px #fff);'></div></div>"
        f"<div style='position:absolute;left:50%;top:50%;width:{w}px;height:{h}px;"
        f"margin:-{h // 2}px 0 0 -{w // 2}px;border-radius:{rad}px;background:{color};"
        f"border:3px solid #fff;box-shadow:{glow}0 1px 5px rgba(0,0,0,.65);box-sizing:border-box;"
        f"display:flex;align-items:center;justify-content:center;color:{text_color};"
        f"font:700 12px sans-serif;'>{short}</div></div>"
    )


def dummy_info(trip_id: str) -> dict:
    h = int(hashlib.md5(trip_id.encode()).hexdigest(), 16)
    return {
        "Plate": f"TM-{h % 90 + 10:02d}-STP",
        "Occupancy (dummy)": f"{h % 100}%",
        "Passengers (dummy)": h % 80,
        "Delay (dummy)": f"{(h >> 8) % 6} min",
        "Doors (dummy)": ["closed", "open"][(h >> 12) % 2],
        "Last update (dummy)": "just now",
    }


# ----------------------- state
ss = st.session_state
ss.setdefault("route_id", "")
ss.setdefault("bus_id", None)
ss.setdefault("last_bus_click", None)
ss.setdefault("last_drawing_rid", None)
ss.setdefault("block_rid", None)  
if "pending_route" in ss:  
    ss["route_id"] = ss.pop("pending_route")
    ss["bus_id"] = None


def show_all_routes():
    ss["route_id"] = ""
    ss["bus_id"] = None
    ss["block_rid"] = ss.get("last_drawing_rid")


def on_route_change():
    ss["bus_id"] = None
    if not ss["route_id"]:
        ss["block_rid"] = ss.get("last_drawing_rid")


routes, trips, stops, shapes, st_times, bounds, cal, cal_dates, stops_json = load_static(
    str(GTFS_DIR)
)
rinfo = routes.set_index("route_id")
fallback = distinct_colors(rinfo.index[rinfo["route_color"] == ""])




# ----------------------------- sidebar
with st.sidebar:
    st.header("Controls")
    route_opts = [""] + sorted(
        routes["route_id"],
        key=lambda r: (len(rinfo.at[r, "route_short_name"]), rinfo.at[r, "route_short_name"]),
    )
    st.selectbox(
        "Route",
        route_opts,
        key="route_id",
        format_func=lambda r: "— none —"
        if not r
        else f"{rinfo.at[r, 'route_short_name']} · {rinfo.at[r, 'route_long_name']}",
        on_change=on_route_change,
    )
    st.button("Show all routes", on_click=show_all_routes)
    use_custom = st.checkbox("Override time (simulate)", value=False)
    now = datetime.now(TZ)
    if use_custom:
        tt = st.time_input("Time", value=now.time().replace(microsecond=0))
        now = now.replace(hour=tt.hour, minute=tt.minute, second=tt.second)
    st.caption(f"Reference time: {now:%Y-%m-%d %H:%M:%S}")
    st.button("Refresh")

now_s = now.hour * 3600 + now.minute * 60 + now.second
services = active_services(cal, cal_dates, now)
sel = ss["route_id"] or None

# ------------------------------------ map
fg = folium.FeatureGroup(name="transit")
trips_by_route = trips.groupby("route_id")
draw_routes = [sel] if sel else [r for r in routes["route_id"] if r in trips_by_route.groups]


def route_features(rid: str):
    color = route_color(rid)
    short = rinfo.at[rid, "route_short_name"]
    tcolor = text_color(rid)
    for shp_id in trips_by_route.get_group(rid)["shape_id"].dropna().unique():
        shp = shapes.get(shp_id)
        if shp is None:
            continue
        yield color, short, tcolor, shp[:, ::-1].tolist()


#1. visible lines
line_w = 7 if sel else 4
for rid in draw_routes:
    for color, short, tcolor, coords in route_features(rid):
        feature = {
            "type": "Feature",
            "properties": {
                "route_id": rid, "short": short, "color": color, "tcolor": tcolor, "hit": False,
            },
            "geometry": {"type": "LineString", "coordinates": coords},
        }
        folium.GeoJson(
            feature,
            style_function=lambda _f, c=color, w=line_w: {"color": c, "weight": w, "opacity": 0.95},
        ).add_to(fg)

#2. hit lines on top
if not sel:
    for rid in draw_routes:
        for color, short, tcolor, coords in route_features(rid):
            feature = {
                "type": "Feature",
                "properties": {
                    "route_id": rid, "short": short, "color": color, "tcolor": tcolor, "hit": True,
                },
                "geometry": {"type": "LineString", "coordinates": coords},
            }
            folium.GeoJson(
                feature,
                style_function=lambda _f, c=color: {"color": c, "weight": HIT_PX, "opacity": 0.01},
            ).add_to(fg)

#3. stops + vehicles
vehicles = []
if sel:
    color = route_color(sel)
    tcolor_sel = text_color(sel)
    path_color, casing_color = contrast_path_colors(color)
    short_sel = rinfo.at[sel, "route_short_name"]
    mode_sel = rinfo.at[sel, "route_type"]

    seen_stops = set()
    for tid in trips.loc[trips["route_id"] == sel, "trip_id"].drop_duplicates():
        if tid not in st_times.index:
            continue
        g = st_times.loc[tid]
        if isinstance(g, pd.Series):
            continue
        for sid in g["stop_id"]:
            if sid in seen_stops or sid not in stops.index:
                continue
            seen_stops.add(sid)
            folium.CircleMarker(
                (stops.at[sid, "stop_lat"], stops.at[sid, "stop_lon"]),
                radius=STOP_RADIUS,
                color="#ffffff",
                weight=2,
                fill=True,
                fill_color=color,
                fill_opacity=1,
                tooltip=stops.at[sid, "stop_name"],
            ).add_to(fg)

    vehicles = vehicles_for_route(sel, now_s, services, trips, stops, shapes, st_times, bounds)
    for v in vehicles:
        big = v["trip_id"] == ss["bus_id"]
        cls = f"vpath vp-{v['cls']}" + (" sel" if big else "")

        folium.PolyLine(v["path"], color=casing_color, weight=13, opacity=1, class_name=cls).add_to(fg)
        folium.PolyLine(v["path"], color=path_color, weight=8, opacity=1, class_name=cls).add_to(fg)
    for v in vehicles:
        big = v["trip_id"] == ss["bus_id"]
        folium.Marker(
            (v["lat"], v["lon"]),
            icon=folium.DivIcon(
                html=vehicle_html(short_sel, color, tcolor_sel, v["heading"], mode_sel, big),
                icon_size=(44, 44),
                icon_anchor=(22, 22),
                class_name=f"veh vid-{v['cls']}",
            ),
            tooltip=f"{short_sel} · {v['trip_id']}",
        ).add_to(fg)

base_map = folium.Map(location=MAP_CENTER, zoom_start=12, tiles=TILES)
HoverInfo(stops_json, TILE_FILTER).add_to(base_map)

col_map, col_info = st.columns([3, 1])
with col_map:
    result = st_folium(
        base_map,
        feature_group_to_add=fg,
        key="map",
        height=680,
        use_container_width=True,
        returned_objects=["last_active_drawing", "last_object_clicked_tooltip"],
    )


# -------------------------- click 
drawing = result.get("last_active_drawing") if result else None
drawing_rid = (drawing.get("properties") or {}).get("route_id") if drawing else None
if drawing_rid:
    ss["last_drawing_rid"] = drawing_rid
    if ss["block_rid"] and drawing_rid != ss["block_rid"]:
        ss["block_rid"] = None

if drawing_rid and not sel and drawing_rid != ss["block_rid"]:
    ss["pending_route"] = drawing_rid
    st.rerun()

tip = result.get("last_object_clicked_tooltip") if result else None
if sel and tip and "·" in tip and tip != ss["last_bus_click"]:
    clicked_id = tip.split("·", 1)[1].strip()
    if clicked_id in {v["trip_id"] for v in vehicles}:
        ss["last_bus_click"] = tip
        ss["bus_id"] = clicked_id
        st.rerun()

# ------------------------ info panel
with col_info:
    if not sel:
        st.subheader("Network")
        st.metric("Routes", len(routes))
        st.metric("Active services today", len(services))
        st.info("Click a route on the map (or use the sidebar).")
    else:
        route = rinfo.loc[sel]
        st.subheader(f"{MODE.get(route['route_type'], 'Vehicle')} {route['route_short_name']}")
        st.caption(route["route_long_name"])
        st.metric("Vehicles active now", len(vehicles))
        if not vehicles:
            st.warning("No active trips at this time (try the time override).")
        bus = next((v for v in vehicles if v["trip_id"] == ss["bus_id"]), None)
        if bus:
            st.divider()
            st.subheader(f"Vehicle {bus['trip_id']}")
            st.write(f"**Heading to:** {bus['headsign']}")
            st.write(f"**Previous stop:** {bus['prev_stop']}")
            st.write(f"**Next stop:** {bus['next_stop']}")
            st.write(f"**Scheduled speed:** {bus['sched_speed']} km/h")
            st.table(pd.Series(dummy_info(bus["trip_id"]), name="Value").astype(str))
        elif vehicles:
            st.info("Click a vehicle on the map.")