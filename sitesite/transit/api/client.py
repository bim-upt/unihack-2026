from __future__ import annotations

from datetime import datetime, timezone
from typing import Mapping

import requests

from .auth import Auth, NoAuth
from .config import ApiConfig
from .endpoints import Endpoints
from .errors import AuthNotConfigured, EndpointUnavailable
from .http import Transport, raise_for_error


class ApiClient:

    def __init__(self, config: ApiConfig | None = None, endpoints: Endpoints | None = None,
                 auth: Mapping[str, Auth] | None = None, transport: Transport | None = None):
        self.config = config or ApiConfig()
        self.endpoints = endpoints or Endpoints()
        self.transport = transport or Transport(self.config)
        self.auth: dict[str, Auth] = {"none": NoAuth(), "default": NoAuth(), **(auth or {})}

    def has(self, name: str) -> bool:
        return getattr(self.endpoints, name, None) is not None

    def time_value(self, v: datetime | int | float | None):
        if v is None:
            return None
        if isinstance(v, (int, float)):
            v = datetime.fromtimestamp(v, timezone.utc)
        if v.tzinfo is None:
            v = v.replace(tzinfo=timezone.utc)
        if self.config.time_format == "iso":
            return v.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        return int(v.timestamp())

    def call(self, name: str, *, path: dict | None = None, query: dict | None = None,
             headers: dict | None = None) -> requests.Response:
        ep = getattr(self.endpoints, name, None)
        if ep is None:
            raise EndpointUnavailable(name)
        auth = self.auth.get(ep.auth)
        if auth is None:
            raise AuthNotConfigured(f"Endpoint {name!r} needs auth {ep.auth!r}, which is not configured.")
        url_path = ep.url_path(**(path or {}))
        params = ep.query(**(query or {}))

        def send() -> requests.Response:
            h = {"Accept": "application/json", **(headers or {}), **auth.headers()}
            return self.transport.request(ep.method, url_path, headers=h, params=params or None)

        resp = send()
        if resp.status_code == 401 and auth.on_unauthorized():
            resp = send()
        raise_for_error(resp)
        return resp

    def call_json(self, name: str, **kw):
        resp = self.call(name, **kw)
        return resp.json() if resp.content else None