from __future__ import annotations

from typing import Callable, Protocol


class Auth(Protocol):
    def headers(self) -> dict[str, str]: ...

    def on_unauthorized(self) -> bool:
        ...


class NoAuth:
    def headers(self) -> dict[str, str]:
        return {}

    def on_unauthorized(self) -> bool:
        return False


class BearerAuth:

    def __init__(self, token: str | Callable[[], str]):
        self._token = token

    def headers(self) -> dict[str, str]:
        tok = self._token() if callable(self._token) else self._token
        return {"Authorization": f"Bearer {tok}"}

    def on_unauthorized(self) -> bool:
        return False


class HeaderAuth:
    def __init__(self, name: str, value: str):
        self._name = name
        self._value = value

    def headers(self) -> dict[str, str]:
        return {self._name: self._value}

    def on_unauthorized(self) -> bool:
        return False