from __future__ import annotations

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .config import ApiConfig
from .errors import ApiError, TransportError


def raise_for_error(resp: requests.Response) -> None:
    if resp.status_code < 400:
        return
    code, message = "HTTP_ERROR", resp.reason or ""
    try:
        payload = resp.json()
        err = payload.get("error") if isinstance(payload, dict) else None
        if isinstance(err, dict):
            code = err.get("code", code)
            message = err.get("message", message)
    except ValueError:
        pass
    raise ApiError(resp.status_code, code, message)


class Transport:
    def __init__(self, config: ApiConfig, session: requests.Session | None = None):
        self.base_url = config.base_url.rstrip("/")
        self.timeout_s = config.timeout_s
        self.verify_tls = config.verify_tls
        self.session = session or requests.Session()
        retry = Retry(
            total=config.get_retries, backoff_factor=0.5,
            status_forcelist=(500, 502, 503, 504),
            allowed_methods=frozenset({"GET"}),
            raise_on_status=False,
        )
        adapter = HTTPAdapter(max_retries=retry)
        self.session.mount("http://", adapter)
        self.session.mount("https://", adapter)

    def request(self, method: str, path: str, *, headers: dict | None = None,
                params: dict | None = None, data: bytes | None = None) -> requests.Response:
        try:
            return self.session.request(
                method, self.base_url + path, headers=headers, params=params, data=data,
                timeout=self.timeout_s, verify=self.verify_tls,
            )
        except requests.RequestException as exc:
            raise TransportError(str(exc)) from exc