from __future__ import annotations

import logging
import time

log = logging.getLogger("transit.api")


class TransportError(Exception):
    """No answer (network down, timeout)."""


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(f"{status} {code}: {message}")
        self.status = status
        self.code = code
        self.message = message

    @property
    def retryable(self) -> bool:
        return self.status >= 500


class EndpointUnavailable(Exception):
    """The endpoint is None in Endpoints."""


class AuthNotConfigured(Exception):
    """The endpoint's auth key has no provider in ApiClient.auth."""

CONNECTION_LOST = "Connection to the server lost. Retrying…"
_last_transport_failure = 0.0   # time.monotonic() of the last call that got no answer


def note_transport_failure() -> None:
    global _last_transport_failure
    _last_transport_failure = time.monotonic()


def transport_failed_within(seconds: float) -> bool:
    """Did a call get no answer in the last `seconds`? Then it's worth asking /health if the server is up."""
    return time.monotonic() - _last_transport_failure < seconds


def user_message(exc: BaseException) -> str:
    """What the page says about a failed call: plain words, no technical detail. The detail goes to the
    log (the terminal Streamlit runs in)."""
    log.warning("API call failed: %s: %s", type(exc).__name__, exc)
    if isinstance(exc, TransportError):
        note_transport_failure()
        return CONNECTION_LOST
    if isinstance(exc, ApiError):
        if exc.status == 401:
            return "The server refused the display token (check DISPLAY_TOKEN in .env)."
        if exc.status >= 500:
            return "The server had a problem. Retrying…"
    return "Couldn't load this data. Retrying…"
