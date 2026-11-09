"""One real call per provider. Skipped unless GNANI_API_KEY, GNANI_TTS_VOICE and SARVAM_API_KEY
are set; run with `pytest -m live`."""

import asyncio

import httpx
import pytest

from app.config import Settings
from app.providers.base import ChatMessage
from app.providers.gnani_prisma import GnaniPrismaSTT
from app.providers.gnani_timbre import GnaniTimbreTTS
from app.providers.sarvam_llm import SarvamLanguageModel

pytestmark = pytest.mark.live


async def test_sarvam_streams_a_reply(live_settings: Settings) -> None:
    async with httpx.AsyncClient(timeout=live_settings.llm_timeout_s) as client:
        llm = SarvamLanguageModel(live_settings, client)
        chunks = [
            c async for c in llm.stream([ChatMessage("user", "Say hello in one short line.")])
        ]
    assert "".join(c.text for c in chunks).strip()
    assert chunks[-1].finish_reason


async def test_timbre_returns_pcm_and_prisma_accepts_it(live_settings: Settings) -> None:
    async with httpx.AsyncClient(timeout=15) as client:
        tts = GnaniTimbreTTS(live_settings, client)
        pcm = b"".join(
            [c async for c in tts.synthesize("Namaste, kaise hain aap?", language="hi-IN")]
        )
    assert len(pcm) > 2 * tts.output_format.sample_rate // 2  # over half a second of audio

    stt = GnaniPrismaSTT(live_settings)
    stream = await stt.open_stream("hi-IN")
    try:
        await stream.send_audio(bytes(4096))  # silence: the session must open and accept frames
        await stream.finish()
        await asyncio.sleep(0.5)
    finally:
        await stream.close()
