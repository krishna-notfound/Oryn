"""REST endpoints that let a user see, edit and delete what Oryn remembers about them.

No `from __future__ import annotations` here: FastAPI resolves the route annotations
(including the `UserId` alias defined inside the router builder) at runtime.
"""

import logging
from collections.abc import Awaitable, Callable
from typing import Annotated, Self

from fastapi import APIRouter, Depends, HTTPException, Response, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from app.conversation.ports import Memory
from app.memory.store import MAX_CONTENT_CHARS, SQLiteMemoryStore

logger = logging.getLogger(__name__)

Authenticate = Callable[[str], Awaitable[str]]
"""Maps a Bearer token to a verified user id; raises if the token is invalid."""


class MemoryOut(BaseModel):
    id: str
    content: str
    category: str
    confidence: float

    @classmethod
    def of(cls, memory: Memory) -> Self:
        return cls(
            id=memory.id,
            content=memory.content,
            category=memory.category,
            confidence=memory.confidence,
        )


class MemoryList(BaseModel):
    memories: list[MemoryOut]


class MemoryPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_CONTENT_CHARS)
    ] = Field(description="The corrected memory text")


def build_memory_router(store: SQLiteMemoryStore, authenticate: Authenticate) -> APIRouter:
    """Routes are always scoped to the user id returned by `authenticate`; the client
    never states who it is, so one user can never address another user's memories."""
    router = APIRouter(tags=["memories"])
    bearer = HTTPBearer(auto_error=False)

    async def current_user_id(
        credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    ) -> str:
        unauthorized = HTTPException(
            status.HTTP_401_UNAUTHORIZED, "Invalid or missing token", {"WWW-Authenticate": "Bearer"}
        )
        if credentials is None:
            raise unauthorized
        try:
            return await authenticate(credentials.credentials)
        except HTTPException:
            raise
        except Exception:
            logger.info("memory API authentication failed")
            raise unauthorized from None

    UserId = Annotated[str, Depends(current_user_id)]

    @router.get("/memories")
    async def list_memories(user_id: UserId) -> MemoryList:
        return MemoryList(memories=[MemoryOut.of(m) for m in await store.list(user_id)])

    @router.patch("/memories/{memory_id}")
    async def edit_memory(memory_id: str, patch: MemoryPatch, user_id: UserId) -> MemoryOut:
        updated = await store.update(user_id, memory_id, patch.content)
        if updated is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Memory not found")
        return MemoryOut.of(updated)

    @router.delete("/memories/{memory_id}", status_code=status.HTTP_204_NO_CONTENT)
    async def delete_memory(memory_id: str, user_id: UserId) -> Response:
        if not await store.delete(user_id, memory_id):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Memory not found")
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @router.delete("/memories", status_code=status.HTTP_204_NO_CONTENT)
    async def delete_all_memories(user_id: UserId) -> Response:
        await store.delete_all(user_id)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    return router
