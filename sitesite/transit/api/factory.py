"""Environment variables:

TRANSIT_API_URL           base URL (default http://localhost:3000/api/v1)
TRANSIT_API_TIME_FORMAT   "unix" (default) or "iso" for from/to
TRANSIT_API_TOKEN         sent as Authorization: Bearer <token> on every endpoint
TRANSIT_API_KEY_HEADER    with TRANSIT_API_KEY: a header-based key instead, e.g. X-Api-Key
TRANSIT_API_KEY
TRANSIT_API_ENDPOINTS     JSON: {"<endpoint name>": "/new/path" | null}, e.g. {"pass_product": "/passes/{product_id}"}
"""
from __future__ import annotations

import json
import os

from .auth import Auth, BearerAuth, HeaderAuth, NoAuth
from .client import ApiClient
from .config import ApiConfig
from .endpoints import Endpoints


def default_auth_from_env() -> Auth:
    token = os.getenv("TRANSIT_API_TOKEN")
    if token:
        return BearerAuth(token)
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