"""search_memory / save_memory: the model's explicit window onto the user's long-term memory.

Both are consent-gated: when the user has memory turned off they do nothing and say so.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.conversation.ports import UserContext
from app.memory.store import MAX_CONTENT_CHARS, SQLiteMemoryStore
from app.tools.registry import Tool

_MEMORY_OFF = "Memory is turned off for this user, so nothing was {action}."


class SearchMemoryArgs(BaseModel):
    query: str = Field(min_length=1, max_length=200, description="What to look for")


class SearchMemoryTool(Tool[SearchMemoryArgs]):
    name = "search_memory"
    description = "Look up things you remember about the user (preferences, interests, goals)."
    args_model = SearchMemoryArgs
    timeout_s = 5.0

    def __init__(self, store: SQLiteMemoryStore) -> None:
        self._store = store

    async def run(self, args: SearchMemoryArgs, user: UserContext) -> str:
        if not user.memory_enabled:
            return _MEMORY_OFF.format(action="searched")
        memories = await self._store.relevant(user.user_id, args.query)
        if not memories:
            return "No matching memories."
        return "\n".join(f"- {m.content}" for m in memories)


class SaveMemoryArgs(BaseModel):
    content: str = Field(
        min_length=1,
        max_length=MAX_CONTENT_CHARS,
        description="One short sentence about the user, e.g. 'Prefers vegetarian food'",
    )


class SaveMemoryTool(Tool[SaveMemoryArgs]):
    name = "save_memory"
    description = (
        "Remember a fact about the user. Use only when they ask you to remember something."
    )
    args_model = SaveMemoryArgs
    timeout_s = 5.0

    def __init__(self, store: SQLiteMemoryStore) -> None:
        self._store = store

    async def run(self, args: SaveMemoryArgs, user: UserContext) -> str:
        if not user.memory_enabled:
            return _MEMORY_OFF.format(action="saved")
        await self._store.add(user.user_id, args.content, "explicit")
        return "Saved."
