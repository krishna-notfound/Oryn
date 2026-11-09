"""Composition root: wires settings, repositories, the runtime and the session layer."""

from __future__ import annotations

import hmac
import logging
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from contextlib import asynccontextmanager
from typing import Annotated, Any

from fastapi import APIRouter, FastAPI, Header, HTTPException, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from pydantic import SecretStr

from app.auth.profiles import ProfileRepository
from app.auth.supabase import Authenticator, SupabaseAuthenticator
from app.config import Settings, get_settings
from app.memory.api import build_memory_router
from app.observability import LoopLagMonitor, rss_bytes
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

    loop_lag = LoopLagMonitor()

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        loop_lag.start()
        yield
        await loop_lag.stop()
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
    async def metrics(authorization: Annotated[str | None, Header()] = None) -> dict[str, Any]:
        """Per-stage p50/p95/p99 (ms), event-loop lag, memory and session count. Bearer
        `METRICS_TOKEN`; with no token configured the endpoint does not exist."""
        _require_bearer(settings.metrics_token, authorization)
        return {
            "latency_ms": latency_stats.summary(),
            "event_loop_lag_ms": loop_lag.summary(),
            "rss_bytes": rss_bytes(),
            "active_sessions": len(registry),
            "max_sessions": settings.max_sessions,
        }

    @app.websocket("/ws")
    async def voice_socket(websocket: WebSocket) -> None:
        await VoiceSession(websocket, services).run()

    return app


def _require_bearer(expected: SecretStr | None, authorization: str | None) -> None:
    if expected is None:
        raise HTTPException(status_code=404)
    scheme, _, presented = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not hmac.compare_digest(
        presented.encode(), expected.get_secret_value().encode()
    ):
        raise HTTPException(status_code=401, headers={"WWW-Authenticate": "Bearer"})


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
