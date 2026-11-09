"""Builds the provider set the app runs with, from `Settings`."""

from __future__ import annotations

from dataclasses import dataclass, field

import httpx

from app.config import Settings
from app.providers.base import Embedder, LanguageModel, SpeechToText, TextToSpeech
from app.providers.fakes import (
    FakeEmbedder,
    FakeLanguageModel,
    FakeSpeechToText,
    FakeTextToSpeech,
)
from app.providers.gnani_prisma import GnaniPrismaSTT
from app.providers.gnani_timbre import GnaniTimbreTTS
from app.providers.openai_embeddings import OpenAICompatibleEmbedder
from app.providers.sarvam_llm import SarvamLanguageModel

_TTS_CONNECT_TIMEOUT_S = 5.0
_TTS_READ_TIMEOUT_S = 15.0


@dataclass(frozen=True, slots=True)
class ProviderSet:
    stt: SpeechToText
    tts: TextToSpeech
    llm: LanguageModel
    embedder: Embedder | None
    _clients: tuple[httpx.AsyncClient, ...] = field(default=(), repr=False)

    async def aclose(self) -> None:
        """Close the shared HTTP clients. Call once at application shutdown."""
        for client in self._clients:
            await client.aclose()


def _missing_env(settings: Settings) -> list[str]:
    required = {
        "GNANI_API_KEY": settings.gnani_api_key,
        "GNANI_TTS_VOICE": settings.gnani_tts_voice,
        "SARVAM_API_KEY": settings.sarvam_api_key,
    }
    return [name for name, value in required.items() if not value]


def build_providers(settings: Settings) -> ProviderSet:
    if settings.provider_mode == "fake":
        return ProviderSet(
            FakeSpeechToText(), FakeTextToSpeech(), FakeLanguageModel(), FakeEmbedder()
        )
    if missing := _missing_env(settings):
        raise RuntimeError(
            f"PROVIDER_MODE=live but these environment variables are not set: {', '.join(missing)}"
        )

    # Long keep-alive: the next sentence of a reply reuses the warm TLS connection.
    pool = httpx.Limits(max_keepalive_connections=10, keepalive_expiry=60.0)
    tts_client = httpx.AsyncClient(
        limits=pool,
        timeout=httpx.Timeout(_TTS_READ_TIMEOUT_S, connect=_TTS_CONNECT_TIMEOUT_S),
    )
    llm_client = httpx.AsyncClient(
        limits=pool, timeout=httpx.Timeout(settings.llm_timeout_s, connect=_TTS_CONNECT_TIMEOUT_S)
    )
    clients = [tts_client, llm_client]

    embedder: Embedder | None = None
    if settings.embeddings_base_url and settings.embeddings_model:
        embeddings_client = httpx.AsyncClient(timeout=settings.llm_timeout_s)
        clients.append(embeddings_client)
        embedder = OpenAICompatibleEmbedder(settings, embeddings_client)

    return ProviderSet(
        stt=GnaniPrismaSTT(settings),
        tts=GnaniTimbreTTS(settings, tts_client),
        llm=SarvamLanguageModel(settings, llm_client),
        embedder=embedder,
        _clients=tuple(clients),
    )
