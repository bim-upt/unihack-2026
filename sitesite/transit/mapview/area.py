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
  function lock() {
    var s = map.getSize();
    if (!s.x || !s.y) return;
    // inside=true: the area always covers the whole frame, so no grey shows around it
    map.setMinZoom(map.getBoundsZoom(area, true));
  }
  map.whenReady(lock);
  map.on("resize", lock);
})();
{% endmacro %}
"""
    )

    def __init__(self, bounds: Bounds):
        super().__init__()
        self._name = "LockToArea"
        (s, w), (n, e) = bounds
        self.bounds_json = json.dumps([[s, w], [n, e]])