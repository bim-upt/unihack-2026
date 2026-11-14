from __future__ import annotations


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