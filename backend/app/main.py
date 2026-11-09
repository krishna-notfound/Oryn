"""Composition root: wires settings, repositories and the session layer into a FastAPI app."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware

from app.auth.profiles import ProfileRepository
from app.auth.supabase import Authenticator, SupabaseAuthenticator
from app.config import Settings, get_settings
from app.conversation.unconfigured import unconfigured_factory
from app.persistence.conversations import (
    ConversationRepository,
    InMemoryConversationRepository,
    PostgrestConversationRepository,
)
from app.persistence.postgrest import PostgrestClient
from app.websocket.session import (
    ConversationFactory,
    SessionRegistry,
    SessionServices,
    VoiceSession,
)


def create_app(
    settings: Settings,
    conversation_factory: ConversationFactory,
    *,
    profiles: ProfileRepository | None = None,
    conversations: ConversationRepository | None = None,
    authenticator: Authenticator | None = None,
) -> FastAPI:
    """Dependencies left as None are built from `settings`."""
    postgrest = PostgrestClient(settings)
    profiles = profiles or ProfileRepository(postgrest)
    if conversations is None:
        conversations = (
            PostgrestConversationRepository(postgrest)
            if postgrest.configured
            else InMemoryConversationRepository()
        )
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
        await postgrest.aclose()

    app = FastAPI(title="Oryn", lifespan=lifespan)
    app.state.authenticator = authenticator
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["GET", "POST", "PATCH", "DELETE"],
        allow_headers=["Authorization", "Content-Type"],
    )

    @app.get("/health")
    async def health() -> dict[str, str | int]:
        return {"status": "ok", "active_sessions": len(registry)}

    @app.websocket("/ws")
    async def voice_socket(websocket: WebSocket) -> None:
        await VoiceSession(websocket, services).run()

    return app


# Placeholder engine until the orchestrator wires the real one.
app = create_app(get_settings(), unconfigured_factory)
