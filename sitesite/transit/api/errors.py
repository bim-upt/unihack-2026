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


LOG_EVERY_S = 30.0   # the same failure is logged at most this often (a live page would repeat it every second)
_last_logged: dict[str, tuple[float, int]] = {}


def _log_throttled(exc: BaseException) -> None:
    """Log a failed call, but the same failure (type and message) only once per LOG_EVERY_S, with the number
    of repeats that were left out since. A different failure is logged straight away."""
    key = f"{type(exc).__name__}: {exc}"[:300]
    now = time.monotonic()
    last, skipped = _last_logged.get(key, (0.0, 0))
    if now - last < LOG_EVERY_S:
        _last_logged[key] = (last, skipped + 1)
        return
    _last_logged[key] = (now, 0)
    if len(_last_logged) > 200:   # forget old ones
        for k in sorted(_last_logged, key=lambda k: _last_logged[k][0])[:100]:
            del _last_logged[k]
    log.warning("API call failed: %s%s", key, f" (and {skipped} more times since the last report)" if skipped else "")


def user_message(exc: BaseException) -> str:
    """What the page says about a failed call: plain words, no technical detail. The detail goes to the
    log (the terminal Streamlit runs in)."""
    _log_throttled(exc)
    if isinstance(exc, TransportError):
        note_transport_failure()
        return CONNECTION_LOST
    if isinstance(exc, ApiError):
        if exc.status == 401:
            return "The server refused the display token (check DISPLAY_TOKEN in .env)."
        if exc.status >= 500:
            return "The server had a problem. Retrying…"
    return "Couldn't load this data. Retrying…"
