"""Synchronous HTTP client for EAOS admin/evolution API endpoints."""

from __future__ import annotations

import json
from typing import Any

import httpx
import typer


class ApiClient:
    """Thin wrapper around httpx with auth header injection."""

    def __init__(self, base_url: str, token: str) -> None:
        self._base_url = base_url.rstrip("/")
        self._token = token

    def _headers(self) -> dict[str, str]:
        headers: dict[str, str] = {}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        return headers

    def get(self, path: str) -> Any:
        resp = httpx.get(
            f"{self._base_url}{path}",
            headers=self._headers(),
            timeout=30.0,
        )
        resp.raise_for_status()
        return resp.json()

    def post(self, path: str, json_body: dict[str, Any] | None = None) -> Any:
        resp = httpx.post(
            f"{self._base_url}{path}",
            headers=self._headers(),
            json=json_body,
            timeout=30.0,
        )
        resp.raise_for_status()
        return resp.json()


def print_json(data: Any) -> None:
    """Pretty-print JSON to stdout via typer.echo."""
    typer.echo(json.dumps(data, indent=2, default=str))
