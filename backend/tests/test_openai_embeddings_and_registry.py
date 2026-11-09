import json

import httpx
import pytest

from app.config import Settings
from app.providers.base import ProviderError
from app.providers.fakes import FakeEmbedder, FakeLanguageModel, FakeSpeechToText, FakeTextToSpeech
from app.providers.gnani_prisma import GnaniPrismaSTT
from app.providers.gnani_timbre import GnaniTimbreTTS
from app.providers.openai_embeddings import OpenAICompatibleEmbedder
from app.providers.registry import build_providers
from app.providers.sarvam_llm import SarvamLanguageModel


def settings(**values: object) -> Settings:
    return Settings(_env_file=None, **values)  # type: ignore[call-arg, arg-type]


# ---------------------------------------------------------------- embeddings


def make_embedder(handler: httpx.MockTransport) -> OpenAICompatibleEmbedder:
    cfg = settings(
        embeddings_base_url="https://emb.example/v1/",
        embeddings_api_key="emb-key",
        embeddings_model="text-embed",
        embeddings_dimensions=3,
    )
    return OpenAICompatibleEmbedder(cfg, httpx.AsyncClient(transport=handler))


async def test_embeds_in_input_order_with_declared_dimensions() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        rows = [
            {"index": 1, "embedding": [0.0, 1.0, 0.0]},
            {"index": 0, "embedding": [1.0, 0.0, 0.0]},
        ]
        return httpx.Response(200, json={"data": rows})

    embedder = make_embedder(httpx.MockTransport(handler))
    assert embedder.dimensions == 3
    vectors = await embedder.embed(["a", "b"])

    assert vectors == [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]]
    assert embedder.dimensions == 3
    assert str(seen[0].url) == "https://emb.example/v1/embeddings"
    assert seen[0].headers["authorization"] == "Bearer emb-key"
    assert json.loads(seen[0].content) == {
        "model": "text-embed",
        "input": ["a", "b"],
        "dimensions": 3,
    }


async def test_rows_without_index_keep_input_order_and_are_normalised() -> None:
    """Gemini's OpenAI-compatible endpoint omits `index`."""
    rows = [{"embedding": [3.0, 0.0, 0.0]}, {"embedding": [0.0, 0.0, 2.0]}]
    embedder = make_embedder(
        httpx.MockTransport(lambda _: httpx.Response(200, json={"data": rows}))
    )
    assert await embedder.embed(["a", "b"]) == [[1.0, 0.0, 0.0], [0.0, 0.0, 1.0]]


async def test_rejects_vectors_of_unexpected_size() -> None:
    rows = [{"index": 0, "embedding": [1.0, 0.0]}]
    embedder = make_embedder(
        httpx.MockTransport(lambda _: httpx.Response(200, json={"data": rows}))
    )
    with pytest.raises(ProviderError, match="2-d vectors"):
        await embedder.embed(["a"])


async def test_embedding_errors_map_to_provider_error() -> None:
    embedder = make_embedder(httpx.MockTransport(lambda _: httpx.Response(429, json={})))
    with pytest.raises(ProviderError) as info:
        await embedder.embed(["a"])
    assert info.value.retryable is True


# ---------------------------------------------------------------- registry


def test_fake_mode_returns_the_fakes_without_any_keys() -> None:
    providers = build_providers(settings(provider_mode="fake"))
    assert isinstance(providers.stt, FakeSpeechToText)
    assert isinstance(providers.tts, FakeTextToSpeech)
    assert isinstance(providers.llm, FakeLanguageModel)
    assert isinstance(providers.embedder, FakeEmbedder)


def test_live_mode_lists_every_missing_variable() -> None:
    with pytest.raises(RuntimeError) as info:
        build_providers(settings(provider_mode="live"))
    message = str(info.value)
    assert "GNANI_API_KEY" in message and "SARVAM_API_KEY" in message
    assert "GNANI_TTS_VOICE" in message


def test_live_mode_names_only_what_is_missing() -> None:
    with pytest.raises(RuntimeError) as info:
        build_providers(settings(gnani_api_key="g", gnani_tts_voice="Nalini"))
    assert "SARVAM_API_KEY" in str(info.value) and "GNANI_API_KEY" not in str(info.value)


async def test_live_mode_builds_real_adapters_and_closes_clients() -> None:
    providers = build_providers(
        settings(gnani_api_key="g", gnani_tts_voice="Nalini", sarvam_api_key="s")
    )
    assert isinstance(providers.stt, GnaniPrismaSTT)
    assert isinstance(providers.tts, GnaniTimbreTTS)
    assert isinstance(providers.llm, SarvamLanguageModel)
    assert providers.embedder is None  # no EMBEDDINGS_* configured
    await providers.aclose()


async def test_live_mode_builds_the_embedder_when_configured() -> None:
    providers = build_providers(
        settings(
            gnani_api_key="g",
            gnani_tts_voice="Nalini",
            sarvam_api_key="s",
            embeddings_base_url="https://emb.example/v1",
            embeddings_model="m",
            embeddings_dimensions=768,
        )
    )
    assert isinstance(providers.embedder, OpenAICompatibleEmbedder)
    assert providers.embedder.dimensions == 768
    await providers.aclose()


def test_live_mode_requires_embedding_dimensions_with_embeddings() -> None:
    with pytest.raises(RuntimeError, match="EMBEDDINGS_DIMENSIONS"):
        build_providers(
            settings(
                gnani_api_key="g",
                gnani_tts_voice="Nalini",
                sarvam_api_key="s",
                embeddings_base_url="https://emb.example/v1",
                embeddings_model="m",
            )
        )
