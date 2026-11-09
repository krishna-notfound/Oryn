"""Minimal Supabase PostgREST client using the service-role key.

The service-role key bypasses RLS, so callers must only use it after verifying the user's
JWT and must always filter by that verified user id.
"""

from __future__ import annotations

from typing import Any

import httpx

from app.config import Settings

_TIMEOUT = httpx.Timeout(5.0, connect=3.0)


class PersistenceError(Exception):
    """Supabase was unreachable, misconfigured or returned an error."""


class PostgrestClient:
    def __init__(self, settings: Settings, http: httpx.AsyncClient | None = None) -> None:
        key = settings.supabase_service_role_key
        self._base = f"{settings.supabase_url.rstrip('/')}/rest/v1" if settings.supabase_url else ""
        self._key = key.get_secret_value() if key else ""
        self._http = http or httpx.AsyncClient(timeout=_TIMEOUT)

    @property
    def configured(self) -> bool:
        return bool(self._base and self._key)

    async def select(self, table: str, params: dict[str, str]) -> list[dict[str, Any]]:
        rows = await self._request("GET", table, params=params)
        return rows if isinstance(rows, list) else []

    async def insert(self, table: str, row: dict[str, Any]) -> dict[str, Any]:
        """Insert one row and return it as stored (including server defaults)."""
        rows = await self._request("POST", table, json=row, prefer="return=representation")
        if not isinstance(rows, list) or not rows:
            raise PersistenceError(f"insert into {table} returned no row")
        stored: dict[str, Any] = rows[0]
        return stored

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _request(
        self,
        method: str,
        table: str,
        *,
        params: dict[str, str] | None = None,
        json: dict[str, Any] | None = None,
        prefer: str | None = None,
    ) -> Any:
        if not self.configured:
            raise PersistenceError("supabase is not configured")
        headers = {"apikey": self._key, "Authorization": f"Bearer {self._key}"}
        if prefer:
            headers["Prefer"] = prefer
        try:
            response = await self._http.request(
                method, f"{self._base}/{table}", params=params, json=json, headers=headers
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise PersistenceError(f"{table}: HTTP {exc.response.status_code}") from None
        except httpx.HTTPError as exc:
            raise PersistenceError(f"{table}: {type(exc).__name__}") from None
        return response.json()
