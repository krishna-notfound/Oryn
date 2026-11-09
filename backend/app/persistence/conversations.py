"""Conversation and message metadata in Supabase Postgres.

Every method takes the verified `user_id` and filters by it. Whether messages are stored
at all (`profiles.save_history`) is the caller's decision.
"""

from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Literal

from app.persistence.postgrest import PostgrestClient

Role = Literal["user", "assistant"]
Mode = Literal["voice", "text"]
_MAX_RECENT = 100


@dataclass(frozen=True, slots=True)
class StoredMessage:
    role: Role
    content: str
    interrupted: bool = False


def _is_uuid(value: str) -> bool:
    try:
        uuid.UUID(value)
    except ValueError:
        return False
    return True


def _clamp(limit: int) -> int:
    return max(1, min(limit, _MAX_RECENT))


class ConversationRepository(ABC):
    @abstractmethod
    async def create(self, user_id: str, mode: Mode) -> str: ...

    @abstractmethod
    async def owns(self, conversation_id: str, user_id: str) -> bool:
        """True if the conversation exists and belongs to `user_id`."""

    @abstractmethod
    async def add_message(
        self, conversation_id: str, user_id: str, role: Role, content: str, interrupted: bool
    ) -> None: ...

    @abstractmethod
    async def recent(self, conversation_id: str, user_id: str, limit: int) -> list[StoredMessage]:
        """Last `limit` messages, oldest first."""


class PostgrestConversationRepository(ConversationRepository):
    def __init__(self, client: PostgrestClient) -> None:
        self._client = client

    async def create(self, user_id: str, mode: Mode) -> str:
        row = await self._client.insert("conversations", {"user_id": user_id, "mode": mode})
        return str(row["id"])

    async def owns(self, conversation_id: str, user_id: str) -> bool:
        if not _is_uuid(conversation_id):
            return False
        rows = await self._client.select(
            "conversations",
            {
                "id": f"eq.{conversation_id}",
                "user_id": f"eq.{user_id}",
                "select": "id",
                "limit": "1",
            },
        )
        return bool(rows)

    async def add_message(
        self, conversation_id: str, user_id: str, role: Role, content: str, interrupted: bool
    ) -> None:
        await self._client.insert(
            "messages",
            {
                "conversation_id": conversation_id,
                "user_id": user_id,
                "role": role,
                "content": content,
                "interrupted": interrupted,
            },
        )

    async def recent(self, conversation_id: str, user_id: str, limit: int) -> list[StoredMessage]:
        rows = await self._client.select(
            "messages",
            {
                "conversation_id": f"eq.{conversation_id}",
                "user_id": f"eq.{user_id}",
                "select": "role,content,interrupted",
                "order": "created_at.desc,id.desc",
                "limit": str(_clamp(limit)),
            },
        )
        return [
            StoredMessage(r["role"], r["content"], bool(r.get("interrupted")))
            for r in reversed(rows)
        ]


class InMemoryConversationRepository(ConversationRepository):
    """For fake mode and tests. Same ownership rules as the real thing."""

    def __init__(self) -> None:
        self._owners: dict[str, str] = {}
        self._messages: dict[str, list[StoredMessage]] = {}

    async def create(self, user_id: str, mode: Mode) -> str:
        conversation_id = str(uuid.uuid4())
        self._owners[conversation_id] = user_id
        self._messages[conversation_id] = []
        return conversation_id

    async def owns(self, conversation_id: str, user_id: str) -> bool:
        return self._owners.get(conversation_id) == user_id

    async def add_message(
        self, conversation_id: str, user_id: str, role: Role, content: str, interrupted: bool
    ) -> None:
        if not await self.owns(conversation_id, user_id):
            raise KeyError(conversation_id)
        self._messages[conversation_id].append(StoredMessage(role, content, interrupted))

    async def recent(self, conversation_id: str, user_id: str, limit: int) -> list[StoredMessage]:
        if not await self.owns(conversation_id, user_id):
            return []
        return self._messages[conversation_id][-_clamp(limit) :]
