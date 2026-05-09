"""Small read-only client for the existing Telegram Monitor localhost API."""

from __future__ import annotations

import json
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urljoin
from urllib.request import Request, urlopen


class ApiUnavailable(RuntimeError):
    """Raised when the local monitor API cannot be reached safely."""


class ApiClient:
    def __init__(self, api_url: str, timeout: float = 3.0):
        self.api_url = api_url.rstrip("/")
        self.timeout = timeout

    def get_json(self, path: str, params: dict[str, Any] | None = None) -> Any:
        query = f"?{urlencode({k: v for k, v in (params or {}).items() if v is not None})}" if params else ""
        url = urljoin(f"{self.api_url}/", path.lstrip("/")) + query
        request = Request(url, method="GET", headers={"Accept": "application/json"})
        try:
            with urlopen(request, timeout=self.timeout) as response:
                raw = response.read().decode("utf-8")
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            raise ApiUnavailable(str(exc)) from exc
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ApiUnavailable(f"invalid JSON from {url}") from exc

    def status(self) -> dict[str, Any]:
        return self.get_json("/status")

    def health(self) -> dict[str, Any]:
        return self.get_json("/health")

    def dialogs(self) -> list[dict[str, Any]]:
        return self.get_json("/dialogs")

    def groups(self) -> list[dict[str, Any]]:
        return self.get_json("/groups")

    def messages(self, minutes: int, dialog: str | None, dialog_type: str | None, limit: int) -> dict[str, Any]:
        return self.get_json(
            "/messages",
            {"minutes": minutes, "dialog": dialog, "type": dialog_type, "limit": limit},
        )
