"""Loads the verified user's profile into a `UserContext`."""

from __future__ import annotations

import logging
from typing import Any

from app.conversation.ports import UserContext
from app.persistence.postgrest import PersistenceError, PostgrestClient

log = logging.getLogger(__name__)

_COLUMNS = (
    "preferred_name,primary_language,extra_languages,style,response_length,"
    "listen_first,memory_enabled,timezone,interests"
)


class ProfileRepository:
    def __init__(self, client: PostgrestClient) -> None:
        self._client = client

    async def load(self, user_id: str) -> UserContext:
        """`user_id` must come from a verified JWT. A missing row or an unreachable
        database yields defaults: personalisation is optional, talking is not."""
        try:
            rows = await self._client.select(
                "profiles", {"id": f"eq.{user_id}", "select": _COLUMNS, "limit": "1"}
            )
        except PersistenceError as exc:
            log.warning("profile unavailable, using defaults", extra={"error": str(exc)})
            return UserContext(user_id=user_id)
        return _to_context(user_id, rows[0]) if rows else UserContext(user_id=user_id)


def _to_context(user_id: str, row: dict[str, Any]) -> UserContext:
    defaults = UserContext(user_id=user_id)
    length = row.get("response_length")
    return UserContext(
        user_id=user_id,
        preferred_name=row.get("preferred_name"),
        language=row.get("primary_language") or defaults.language,
        extra_languages=tuple(row.get("extra_languages") or ()),
        style=row.get("style") or defaults.style,
        response_length=length if length in ("short", "detailed") else defaults.response_length,
        listen_first=row.get("listen_first", defaults.listen_first),
        memory_enabled=row.get("memory_enabled", defaults.memory_enabled),
        save_history=row.get("save_history", defaults.save_history),
        timezone=row.get("timezone") or defaults.timezone,
        interests=tuple(row.get("interests") or ()),
    )
