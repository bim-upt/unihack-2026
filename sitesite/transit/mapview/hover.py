
from __future__ import annotations

import json
from pathlib import Path

from branca.element import MacroElement
from jinja2 import Template

_JS = Path(__file__).with_name("hover.js").read_text(encoding="utf-8")


class HoverInfo(MacroElement):
    _template = Template(
        """
{% macro script(this, kwargs) %}
(function () {
  var map = {{ this._parent.get_name() }};
  var CFG = {{ this.config_json }};
{{ this.js }}
})();
{% endmacro %}
"""
    )

    def __init__(self, stops_by_route: dict, tile_filter: str, highlight_px: int = 3, throttle_ms: int = 50):
        super().__init__()
        self._name = "HoverInfo"
        self.config_json = json.dumps({
            "stops": stops_by_route,
            "tileFilter": tile_filter,
            "highlight": highlight_px,
            "throttleMs": throttle_ms,
        })
        self.js = _JS