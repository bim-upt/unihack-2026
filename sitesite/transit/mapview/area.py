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
  var tries = 0;
  // The smallest zoom at which the area covers the whole frame (inside=true): no grey around it. Applied when
  // the map is ready, again after it gets its size (inside Streamlit it can start at 0x0), on every resize,
  // and after every zoom or move: a zoom asked for below it (the page's own "zoom 0") is pulled back up.
  function lock() {
    var s = map.getSize();
    if (!s.x || !s.y) {
      if (tries++ < 50) setTimeout(lock, 100);
      return;
    }
    var z = map.getBoundsZoom(area, true);
    if (map.getMinZoom() !== z) map.setMinZoom(z);
    if (map.getZoom() < z) map.setView(area.getCenter(), z, {animate: false});
  }
  map.whenReady(function () { lock(); setTimeout(lock, 250); setTimeout(lock, 1000); });
  map.on("resize", lock);
  map.on("zoomend", lock);
  map.on("moveend", lock);
})();
{% endmacro %}
"""
    )

    def __init__(self, bounds: Bounds):
        super().__init__()
        self._name = "LockToArea"
        (s, w), (n, e) = bounds
        self.bounds_json = json.dumps([[s, w], [n, e]])