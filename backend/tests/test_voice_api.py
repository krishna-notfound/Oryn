import asyncio
import io
import wave
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import build_app
from app.providers.base import AudioFormat, ProviderError, TextToSpeech
from app.voice.api import PREVIEW_CACHE_SIZE, build_voice_router
from app.voice.catalog import DEFAULT_VOICE, LANGUAGES, VOICES

TOKENS = {"token-alice": "alice"}
AUTH = {"Authorization": "Bearer token-alice"}


async def fake_authenticate(token: str) -> str:
    try:
        return TOKENS[token]
    except KeyError:
        raise PermissionError("bad token") from None


class RecordingTTS(TextToSpeech):
    name = "recording-tts"
    output_format = AudioFormat(sample_rate=24_000)

    def __init__(self, error: ProviderError | None = None, delay: float = 0.0) -> None:
        self.calls: list[tuple[str, str, str | None]] = []
        self.error = error
        self.delay = delay

    async def synthesize(
        self, text: str, *, language: str, voice: str | None = None
    ) -> AsyncIterator[bytes]:
        self.calls.append((text, language, voice))
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error:
            raise self.error
        yield b"\x01\x00" * 100
        yield b"\x02\x00" * 50 + b"\x07"  # an odd trailing byte must not corrupt the WAV


def make_client(tts: TextToSpeech) -> TestClient:
    app = FastAPI()
    app.include_router(build_voice_router(tts, fake_authenticate))
    return TestClient(app)


def test_voices_requires_a_valid_token() -> None:
    client = make_client(RecordingTTS())
    assert client.get("/voices").status_code == 401
    assert client.get("/voices", headers={"Authorization": "Bearer nope"}).status_code == 401
    assert client.get("/voices/Poorvi/preview").status_code == 401


def test_voices_lists_languages_voices_and_defaults() -> None:
    body = make_client(RecordingTTS()).get("/voices", headers=AUTH).json()
    assert set(body) == {"languages", "voices", "defaults"}
    assert body["languages"] == [{"code": c, "name": n} for c, n in LANGUAGES.items()]
    assert len(body["languages"]) == 10
    assert len(body["voices"]) == len(VOICES) == 42
    assert set(body["voices"][0]) == {"name", "gender", "language", "hinglish"}
    poorvi = next(v for v in body["voices"] if v["name"] == "Poorvi")
    assert poorvi == {"name": "Poorvi", "gender": "female", "language": "hi-IN", "hinglish": True}
    assert body["defaults"] == DEFAULT_VOICE


def test_preview_returns_a_playable_wav_in_the_voices_language() -> None:
    tts = RecordingTTS()
    response = make_client(tts).get("/voices/Nalini/preview", headers=AUTH)
    assert response.status_code == 200
    assert response.headers["content-type"] == "audio/wav"
    with wave.open(io.BytesIO(response.content)) as wav:
        assert (wav.getnchannels(), wav.getsampwidth(), wav.getframerate()) == (1, 2, 24_000)
        assert wav.getnframes() == 150  # 300 bytes of whole samples; the odd byte is dropped
    text, language, voice = tts.calls[0]
    assert (language, voice) == ("hi-IN", "Nalini") and text


def test_preview_name_is_case_insensitive_and_language_selects_the_sample() -> None:
    tts = RecordingTTS()
    client = make_client(tts)
    assert client.get("/voices/poorvi/preview?language=en-IN", headers=AUTH).status_code == 200
    _, language, voice = tts.calls[0]
    assert (language, voice) == ("en-IN", "Poorvi")


def test_preview_unknown_voice_is_404_and_never_reaches_tts() -> None:
    tts = RecordingTTS()
    assert make_client(tts).get("/voices/Nobody/preview", headers=AUTH).status_code == 404
    assert tts.calls == []


@pytest.mark.parametrize("language", ["xx-IN", "auto", "ta-IN"])  # unknown, auto, not Nalini's
def test_preview_rejects_a_language_the_voice_does_not_speak(language: str) -> None:
    tts = RecordingTTS()
    response = make_client(tts).get(f"/voices/Nalini/preview?language={language}", headers=AUTH)
    assert response.status_code == 400
    assert tts.calls == []


def test_preview_is_cached_per_voice_and_language() -> None:
    tts = RecordingTTS()
    client = make_client(tts)
    first = client.get("/voices/Poorvi/preview", headers=AUTH)
    again = client.get("/voices/Poorvi/preview", headers=AUTH)
    client.get("/voices/Poorvi/preview?language=en-IN", headers=AUTH)
    assert first.content == again.content
    assert len(tts.calls) == 2
    assert "max-age" in again.headers["cache-control"]


def test_preview_cache_is_bounded_to_64() -> None:
    assert PREVIEW_CACHE_SIZE <= 64


def test_preview_cache_evicts_the_least_recently_used_entry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.voice.api.PREVIEW_CACHE_SIZE", 3)
    tts = RecordingTTS()
    client = make_client(tts)
    names = ["Poorvi", "Nalini", "Deepak", "Asmita"]
    for name in names:
        assert client.get(f"/voices/{name}/preview", headers=AUTH).status_code == 200
    assert len(tts.calls) == 4  # Poorvi was evicted by Asmita
    client.get("/voices/Asmita/preview", headers=AUTH)
    assert len(tts.calls) == 4  # newest: still cached
    client.get("/voices/Poorvi/preview", headers=AUTH)
    assert len(tts.calls) == 5  # oldest: synthesised again


def test_busy_timbre_is_503_with_retry_after_and_is_not_cached() -> None:
    tts = RecordingTTS(error=ProviderError("gnani-timbre", "HTTP 429", retryable=True))
    client = make_client(tts)
    response = client.get("/voices/Poorvi/preview", headers=AUTH)
    assert response.status_code == 503
    assert int(response.headers["retry-after"]) >= 1
    tts.error = None
    assert client.get("/voices/Poorvi/preview", headers=AUTH).status_code == 200


def test_permanent_provider_failure_is_502() -> None:
    tts = RecordingTTS(error=ProviderError("gnani-timbre", "bad voice", retryable=False))
    assert make_client(tts).get("/voices/Poorvi/preview", headers=AUTH).status_code == 502


def test_waiting_too_long_for_a_synthesis_slot_is_503(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.voice.api.PREVIEW_TIMEOUT_S", 0.05)
    response = make_client(RecordingTTS(delay=1.0)).get("/voices/Poorvi/preview", headers=AUTH)
    assert response.status_code == 503 and "retry-after" in response.headers


def test_production_app_mounts_the_voice_routes_behind_auth(tmp_path: Path) -> None:
    settings = Settings(provider_mode="fake", memory_db_path=tmp_path / "m.db")
    with TestClient(build_app(settings)) as client:
        assert client.get("/voices").status_code == 401
        assert client.get("/voices/Poorvi/preview").status_code == 401
