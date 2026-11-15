from __future__ import annotations

import json

from branca.element import MacroElement
from jinja2 import Template

from ..config import Bounds


class LockToArea(MacroElement):
    _template = Template(
        """
{% macro script(this, kwargs) %}
(function () {
  var map = {{ this._parent.get_name() }};
  var area = L.latLngBounds({{ this.bounds_json }});
  var focus = {{ this.focus_json }};   // a line to show whole: fitted once the map knows its real size
  var focused = false;
  var tries = 0;
  var lastSize = null, settled = false;
  // The smallest zoom at which the area covers the whole frame (inside=true): no grey around it. Only applied
  // once the frame's size has SETTLED (the same on two checks 150 ms apart): a new map frame briefly has a
  // temporary size, and a minimum worked out from that would wrongly zoom in and lose the view the map was
  // opened at. Zooming in to the minimum keeps the current center (setZoom), never jumps to the area's middle.
  function lock() {
    var s = map.getSize();
    if (!s.x || !s.y) {
      if (tries++ < 50) setTimeout(lock, 100);
      return;
    }
    if (!settled) {
      if (!lastSize || lastSize.x !== s.x || lastSize.y !== s.y) {
        lastSize = s;
        if (tries++ < 50) setTimeout(lock, 150);
        return;
      }
      settled = true;
    }
    var z = map.getBoundsZoom(area, true);
    if (focus && !focused) {
      focused = true;
      map.setMinZoom(z);
      map.fitBounds(L.latLngBounds(focus), {padding: [24, 24], animate: false});
      return;   // the fit ends in a moveend, which runs lock() again
    }
    if (map.getMinZoom() !== z) map.setMinZoom(z);
    if (map.getZoom() < z) map.setZoom(z, {animate: false});
  }
  map.whenReady(function () { lock(); setTimeout(lock, 250); setTimeout(lock, 1000); });
  map.on("resize", lock);
  map.on("zoomend", lock);
  map.on("moveend", lock);
})();
{% endmacro %}
"""
    )

    def __init__(self, bounds: Bounds, focus: Bounds | None = None):
        """`focus`: a line's box to fit in the frame when the map opens (in the browser, at the map's real size)."""
        super().__init__()
        self._name = "LockToArea"
        (s, w), (n, e) = bounds
        self.bounds_json = json.dumps([[s, w], [n, e]])
        self.focus_json = json.dumps([[focus[0][0], focus[0][1]], [focus[1][0], focus[1][1]]]) if focus else "null"