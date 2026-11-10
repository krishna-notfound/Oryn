"""REST endpoints for the Settings voice picker: the catalogue and short voice previews.

No `from __future__ import annotations` here: FastAPI resolves the route annotations
(including the `Authenticated` alias defined inside the router builder) at runtime.
"""

import asyncio
import io
import logging
import wave
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel

from app.providers.base import ProviderError, TextToSpeech
from app.voice.catalog import DEFAULT_VOICE, LANGUAGES, VOICES, find_voice, speaks
from app.voice.samples import sample_for

logger = logging.getLogger(__name__)

Authenticate = Callable[[str], Awaitable[str]]
"""Maps a Bearer token to a verified user id; raises if the token is invalid."""

PREVIEW_CACHE_SIZE = 64  # bounded so memory stays flat however many (voice, language) pairs exist
PREVIEW_TIMEOUT_S = 12.0  # slot wait + synthesis; beyond this Timbre is treated as busy
RETRY_AFTER_S = 3
CACHE_CONTROL = "private, max-age=86400"  # a voice's sample never changes


class LanguageOut(BaseModel):
    code: str
    name: str


class VoiceOut(BaseModel):
    name: str
    gender: str
    language: str
    hinglish: bool


class VoiceCatalog(BaseModel):
    languages: list[LanguageOut]
    voices: list[VoiceOut]
    defaults: dict[str, str]  # language code -> default voice name


def wav_bytes(pcm: bytes, sample_rate: int) -> bytes:
    """Wrap mono 16-bit PCM in a WAV container so a browser <audio> element can play it."""
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(pcm[: len(pcm) - len(pcm) % 2])
    return buffer.getvalue()


class PreviewCache:
    """Least-recently-used cache of synthesised previews, keyed by (voice, language)."""

    def __init__(self, size: int) -> None:
        self._size = size
        self._items: OrderedDict[tuple[str, str], bytes] = OrderedDict()

    def get(self, key: tuple[str, str]) -> bytes | None:
        value = self._items.get(key)
        if value is not None:
            self._items.move_to_end(key)
        return value

    def put(self, key: tuple[str, str], value: bytes) -> None:
        self._items[key] = value
        self._items.move_to_end(key)
        while len(self._items) > self._size:
            self._items.popitem(last=False)


def build_voice_router(tts: TextToSpeech, authenticate: Authenticate) -> APIRouter:
    router = APIRouter(tags=["voices"])
    bearer = HTTPBearer(auto_error=False)
    cache = PreviewCache(PREVIEW_CACHE_SIZE)
    # Timbre allows one request at a time account-wide and live calls share that slot; previews
    # queue behind one lock so ten quick taps do not pile up as ten competing syntheses.
    synthesis = asyncio.Lock()

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
            logger.info("voice API authentication failed")
            raise unauthorized from None

    Authenticated = Annotated[str, Depends(current_user_id)]

    def busy(reason: str) -> HTTPException:
        return HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            f"Voice preview is busy ({reason}); try again shortly",
            {"Retry-After": str(RETRY_AFTER_S)},
        )

    async def synthesize(text: str, language: str, voice: str) -> bytes:
        pcm = bytearray()
        async for chunk in tts.synthesize(text, language=language, voice=voice):
            pcm += chunk
        return wav_bytes(bytes(pcm), tts.output_format.sample_rate)

    @router.get("/voices")
    async def list_voices(_: Authenticated) -> VoiceCatalog:
        return VoiceCatalog(
            languages=[LanguageOut(code=c, name=n) for c, n in LANGUAGES.items()],
            voices=[
                VoiceOut(name=v.name, gender=v.gender, language=v.language, hinglish=v.hinglish)
                for v in VOICES
            ],
            defaults=dict(DEFAULT_VOICE),
        )

    @router.get("/voices/{name}/preview")
    async def preview(
        name: str, _: Authenticated, language: Annotated[str | None, Query()] = None
    ) -> Response:
        voice = find_voice(name)
        if voice is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Voice not found")
        language = language or voice.language
        if language not in LANGUAGES or not speaks(voice, language):
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, f"{voice.name} does not speak {language}"
            )
        key = (voice.name, language)
        audio = cache.get(key)
        if audio is None:
            try:
                async with asyncio.timeout(PREVIEW_TIMEOUT_S), synthesis:
                    audio = cache.get(key)  # a queued twin request may have filled it
                    if audio is None:
                        audio = await synthesize(sample_for(language), language, voice.name)
                        cache.put(key, audio)
            except TimeoutError:
                raise busy("timed out") from None
            except ProviderError as exc:
                logger.warning(
                    "voice preview failed", extra={"voice": voice.name, "error": str(exc)}
                )
                if exc.retryable:
                    raise busy("provider unavailable") from None
                raise HTTPException(
                    status.HTTP_502_BAD_GATEWAY, "Voice preview unavailable"
                ) from None
        return Response(audio, media_type="audio/wav", headers={"Cache-Control": CACHE_CONTROL})

    return router
