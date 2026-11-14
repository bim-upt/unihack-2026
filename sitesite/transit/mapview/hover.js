try {
  var STOPS = CFG.stops;

  if (CFG.tileFilter) { map.getPanes().tilePane.style.filter = CFG.tileFilter; }

  var css = document.createElement("style");
  css.textContent =
    ".vpath{opacity:0;pointer-events:none !important}" +
    ".vpath.show,.vpath.sel{opacity:1}";
  document.head.appendChild(css);

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
      styleRoute(current, CFG.highlight);
      showStops(current, res[current].color);
    }
  }

  map.on("mousemove", function (e) {
    var t = Date.now();
    if (t - lastRun < CFG.throttleMs) return;
    lastRun = t;
    update(e);
  });
  map.on("mouseout", clearAll);
} catch (err) {
  console.error("HoverInfo failed", err);
}