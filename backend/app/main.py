"""Composition root: wires settings, repositories, the runtime and the session layer."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware

from app.auth.profiles import ProfileRepository
from app.auth.supabase import Authenticator, SupabaseAuthenticator
from app.config import Settings, get_settings
from app.memory.api import build_memory_router
from app.persistence.conversations import (
    ConversationRepository,
    InMemoryConversationRepository,
    PostgrestConversationRepository,
)
from app.persistence.postgrest import PostgrestClient
from app.voice.latency import latency_stats
from app.websocket.session import (
    ConversationFactory,
    SessionRegistry,
    SessionServices,
    VoiceSession,
)
from app.wiring import OrynRuntime

Shutdown = Callable[[], Awaitable[None]]


def create_app(
    settings: Settings,
    conversation_factory: ConversationFactory,
    *,
    postgrest: PostgrestClient | None = None,
    profiles: ProfileRepository | None = None,
    conversations: ConversationRepository | None = None,
    authenticator: Authenticator | None = None,
    routers: Sequence[APIRouter] = (),
    on_shutdown: Sequence[Shutdown] = (),
) -> FastAPI:
    """Dependencies left as None are built from `settings`."""
    postgrest = postgrest or PostgrestClient(settings)
    profiles = profiles or ProfileRepository(postgrest)
    conversations = conversations or _conversation_repository(postgrest)
    authenticator = authenticator or SupabaseAuthenticator(settings)
    registry = SessionRegistry()
    services = SessionServices(
        settings=settings,
        authenticator=authenticator,
        profiles=profiles,
        conversations=conversations,
        conversation_factory=conversation_factory,
        registry=registry,
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        for shutdown in on_shutdown:
            await shutdown()
        await postgrest.aclose()

    app = FastAPI(title="Oryn", lifespan=lifespan)
    app.state.authenticator = authenticator
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["GET", "POST", "PATCH", "DELETE"],
        allow_headers=["Authorization", "Content-Type"],
    )
    for router in routers:
        app.include_router(router)

    @app.get("/health")
    async def health() -> dict[str, str | int]:
        return {"status": "ok", "active_sessions": len(registry)}

    @app.get("/metrics")
    async def metrics() -> dict[str, dict[str, float]]:
        """Rolling p50/p95 per pipeline stage, in milliseconds."""
        return latency_stats.summary()

    @app.websocket("/ws")
    async def voice_socket(websocket: WebSocket) -> None:
        await VoiceSession(websocket, services).run()

    return app


def _conversation_repository(postgrest: PostgrestClient) -> ConversationRepository:
    if postgrest.configured:
        return PostgrestConversationRepository(postgrest)
    return InMemoryConversationRepository()


def build_app(settings: Settings | None = None) -> FastAPI:
    """The production app (`uvicorn app.main:build_app --factory`): real providers, or fakes
    with PROVIDER_MODE=fake, plus memory and tools. Fails fast when live keys are missing."""
    settings = settings or get_settings()
    logging.basicConfig(level=settings.log_level)
    postgrest = PostgrestClient(settings)
    conversations = _conversation_repository(postgrest)
    authenticator = SupabaseAuthenticator(settings)
    runtime = OrynRuntime(settings, conversations)

    async def user_id_from_token(token: str) -> str:
        return (await authenticator.verify(token)).user_id

    return create_app(
        settings,
        runtime.create_conversation,
        postgrest=postgrest,
        conversations=conversations,
        authenticator=authenticator,
        routers=[build_memory_router(runtime.memory, user_id_from_token)],
        on_shutdown=[runtime.aclose],
    )
