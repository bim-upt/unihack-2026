"""Builds the ApiClient from environment variables (.env is loaded by transit/__init__.py).

TRANSIT_API_URL           base URL (default http://localhost:3000/api/v1)
DISPLAY_TOKEN             display token, sent as Authorization: Bearer <token> on every call
TRANSIT_API_TOKEN         same, alternative name (takes priority)
TRANSIT_API_KEY_HEADER    with TRANSIT_API_KEY: a header-based key instead
TRANSIT_API_KEY
TRANSIT_API_TIME_FORMAT   "iso" (default) or "unix" for from/to
TRANSIT_API_ENDPOINTS     JSON: {"<endpoint name>": "/new/path" | null}
"""
from __future__ import annotations

import json
import os

from .auth import Auth, BearerAuth, HeaderAuth, NoAuth
from .client import ApiClient
from .config import ApiConfig
from .endpoints import Endpoints


def default_auth_from_env() -> Auth:
    token = os.getenv("TRANSIT_API_TOKEN") or os.getenv("DISPLAY_TOKEN")
    if token:
        return BearerAuth(token.strip())
    key_header, key = os.getenv("TRANSIT_API_KEY_HEADER"), os.getenv("TRANSIT_API_KEY")
    if key_header and key:
        return HeaderAuth(key_header, key)
    return NoAuth()


def build_client(config: ApiConfig | None = None, endpoints: Endpoints | None = None,
                 auth: dict[str, Auth] | None = None) -> ApiClient:
    endpoints = endpoints or Endpoints()
    overrides = os.getenv("TRANSIT_API_ENDPOINTS")
    if overrides:
        endpoints = endpoints.override(**json.loads(overrides))
    return ApiClient(
        config or ApiConfig.from_env(),
        endpoints,
        {"default": default_auth_from_env(), **(auth or {})},
    )