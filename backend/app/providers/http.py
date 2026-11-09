"""HTTP plumbing shared by the REST/SSE adapters: SSE parsing and error mapping."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import httpx

from app.providers.base import ProviderError

RETRYABLE_STATUSES = frozenset({429, 500, 502, 503, 504})
# 429s that more waiting cannot fix (Sarvam: a seat's spend budget is exhausted).
_PERMANENT_CODES = frozenset({"seat_budget_exhausted_error"})


async def iter_sse(response: httpx.Response) -> AsyncIterator[tuple[str | None, str]]:
    """Yield `(event_name, data)` per Server-Sent Event as soon as its blank line arrives."""
    event: str | None = None
    data: list[str] = []
    async for line in response.aiter_lines():
        if not line:
            if data:
                yield event, "\n".join(data)
            event, data = None, []
        elif line.startswith("event:"):
            event = line[6:].strip()
        elif line.startswith("data:"):
            data.append(line[5:].removeprefix(" "))
    if data:  # stream closed without the trailing blank line
        yield event, "\n".join(data)


async def error_from_response(provider: str, response: httpx.Response) -> ProviderError:
    """Map a non-2xx response to a `ProviderError`. Reads the (small) error body."""
    await response.aread()
    code, detail = _error_fields(response)
    retryable = response.status_code in RETRYABLE_STATUSES and code not in _PERMANENT_CODES
    message = f"HTTP {response.status_code} {code} {detail}".strip()
    return ProviderError(provider, message, retryable=retryable)


def _error_fields(response: httpx.Response) -> tuple[str, str]:
    try:
        body: Any = response.json()
    except ValueError:
        return "", response.text[:200]
    if isinstance(body, dict):
        # Sarvam: {"error": {...}}; Gnani: {"detail": {...}}.
        err = body.get("error") or body.get("detail")
        if isinstance(err, dict):
            return str(err.get("code") or err.get("error_code") or ""), str(err.get("message", ""))
    return "", str(body)[:200]
