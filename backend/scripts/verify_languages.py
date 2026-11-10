"""Live check that every language and voice works end to end: Timbre -> Prisma.

Phase 1, per language (10): synthesise `SAMPLE_TEXT` with the language's default voice, stream the
audio into Prisma at real-time cadence once with `indic-auto` and once with the explicit language
code, and report the transcript, the detected language and a text-similarity score.
Phase 2, per voice (42): synthesise a two-word phrase in the voice's preferred language and check
that real audio comes back.

Timbre allows one active request, so everything runs strictly one after another. Needs real keys;
run from `backend/` (the key file is read through `Settings`, never printed):

    PYTHONPATH=. python scripts/verify_languages.py [--env-file .env] [--skip-voices]
"""

from __future__ import annotations

import argparse
import array
import asyncio
import difflib
import re
import sys
import time
import unicodedata
from dataclasses import dataclass

import httpx

from app.config import Settings
from app.providers.base import ProviderError, SpeechToTextStream, TranscriptEvent
from app.providers.gnani_prisma import FRAME_BYTES, GnaniPrismaSTT
from app.providers.gnani_timbre import GnaniTimbreTTS
from app.voice.catalog import DEFAULT_VOICE, LANGUAGES, VOICES
from app.voice.samples import SAMPLE_TEXT, TWO_WORDS

STT_RATE = 16_000
LEAD_IN_S = 0.3
TAIL_SILENCE_S = 1.0
TRANSCRIPT_TIMEOUT_S = 20.0
QUIET_AFTER_COMMIT_S = 3.0
HANDSHAKE_ATTEMPTS = 3
MIN_VOICE_AUDIO_S = 0.3
FRAME_S = (FRAME_BYTES // 2) / STT_RATE


def resample(pcm: bytes, source_rate: int, target_rate: int = STT_RATE) -> bytes:
    """Linear-interpolation resample of mono 16-bit little-endian PCM."""
    if source_rate == target_rate:
        return pcm
    samples = array.array("h")
    samples.frombytes(pcm[: len(pcm) - len(pcm) % 2])
    count = int(len(samples) * target_rate / source_rate)
    out = array.array("h")
    for i in range(count):
        pos = i * source_rate / target_rate
        j = int(pos)
        frac = pos - j
        nxt = samples[min(j + 1, len(samples) - 1)]
        out.append(int(samples[j] * (1 - frac) + nxt * frac))
    return out.tobytes()


def normalize(text: str) -> str:
    """Lower-case, strip punctuation and collapse spaces, so scoring compares words only."""
    kept = "".join(c for c in text.lower() if not unicodedata.category(c).startswith("P"))
    return re.sub(r"\s+", " ", kept).strip()


def similarity(expected: str, heard: str) -> float:
    return difflib.SequenceMatcher(None, normalize(expected), normalize(heard)).ratio()


async def synthesize(tts: GnaniTimbreTTS, text: str, language: str, voice: str) -> bytes:
    pcm = bytearray()
    async for chunk in tts.synthesize(text, language=language, voice=voice):
        pcm += chunk
    return resample(bytes(pcm), tts.output_format.sample_rate)


@dataclass
class Heard:
    text: str = ""
    languages: tuple[str, ...] = ()  # detected code per transcript segment, in order
    error: str = ""

    @property
    def language(self) -> str | None:
        return "+".join(dict.fromkeys(self.languages)) or None


async def open_stream(stt: GnaniPrismaSTT, language: str) -> SpeechToTextStream:
    """The upgrade sometimes times out (seen live); one more try is what a reconnect does."""
    for attempt in range(HANDSHAKE_ATTEMPTS):
        try:
            return await stt.open_stream(language)
        except ProviderError:
            if attempt == HANDSHAKE_ATTEMPTS - 1:
                raise
            await asyncio.sleep(1.0)
    raise AssertionError("unreachable")


async def transcribe(stt: GnaniPrismaSTT, language: str, pcm: bytes) -> Heard:
    """Stream `pcm` at real-time cadence, with a lead-in and trailing silence, then commit.
    Collects every segment Prisma emits until it has been quiet after the commit."""
    lead = bytes(int(LEAD_IN_S * STT_RATE) * 2)
    tail = bytes(int(TAIL_SILENCE_S * STT_RATE) * 2)
    audio = lead + pcm + tail
    try:
        stream = await open_stream(stt, language)
    except ProviderError as exc:
        return Heard(error=str(exc))
    texts: list[str] = []
    languages: list[str] = []
    last_event = time.monotonic()

    async def feed() -> None:
        started = time.monotonic()
        for n, offset in enumerate(range(0, len(audio), FRAME_BYTES)):
            await stream.send_audio(audio[offset : offset + FRAME_BYTES])
            delay = started + (n + 1) * FRAME_S - time.monotonic()
            if delay > 0:
                await asyncio.sleep(delay)
        await stream.finish()

    async def collect() -> None:
        nonlocal last_event
        async for event in stream.events():
            if isinstance(event, TranscriptEvent) and event.text.strip():
                texts.append(event.text.strip())
                languages.append(event.language or "?")
                last_event = time.monotonic()

    feeder = asyncio.create_task(feed())
    collector = asyncio.create_task(collect())
    error = ""
    try:
        deadline = time.monotonic() + len(audio) / 2 / STT_RATE + TRANSCRIPT_TIMEOUT_S
        while time.monotonic() < deadline and not collector.done():
            await asyncio.sleep(0.1)
            if feeder.done() and texts and time.monotonic() - last_event > QUIET_AFTER_COMMIT_S:
                break
        for task in (feeder, collector):
            if task.done() and not task.cancelled() and (failure := task.exception()):
                error = str(failure)
        if not texts and not error:
            error = "no transcript"
    finally:
        for task in (feeder, collector):
            task.cancel()
        await asyncio.gather(feeder, collector, return_exceptions=True)
        await stream.close()
    return Heard(" ".join(texts), tuple(languages), error)


async def verify_languages(stt: GnaniPrismaSTT, tts: GnaniTimbreTTS) -> list[str]:
    print("\n== Languages: default voice -> Prisma ==")
    header = (
        "| language | voice | TTS ok | indic-auto detected | auto match "
        "| auto fidelity | explicit fidelity |"
    )
    rows = [header, "|---|---|---|---|---|---|---|"]
    failures: list[str] = []
    for code, name in LANGUAGES.items():
        voice, text = DEFAULT_VOICE[code], SAMPLE_TEXT[code]
        try:
            pcm = await synthesize(tts, text, code, voice)
        except ProviderError as exc:
            print(f"{code} {voice}: TTS FAILED {exc}")
            rows.append(f"| {name} ({code}) | {voice} | no | - | - | - | - |")
            failures.append(f"{code}: tts {exc}")
            continue
        seconds = len(pcm) / 2 / STT_RATE
        auto = await transcribe(stt, "auto", pcm)
        explicit = await transcribe(stt, code, pcm)
        match = auto.language == code
        auto_fid, explicit_fid = similarity(text, auto.text), similarity(text, explicit.text)
        print(f"{code} {voice} tts={seconds:.1f}s")
        print(f"  auto     lang={auto.language} fid={auto_fid:.2f} {auto.error or auto.text!r}")
        print(
            f"  explicit lang={explicit.language} fid={explicit_fid:.2f} "
            f"{explicit.error or explicit.text!r}"
        )
        if not match:
            failures.append(f"{code}: indic-auto detected {auto.language}")
        rows.append(
            f"| {name} ({code}) | {voice} | yes ({seconds:.1f} s) | {auto.language or '-'} | "
            f"{'yes' if match else 'NO'} | {auto_fid:.0%} | {explicit_fid:.0%} |"
        )
    print("\n" + "\n".join(rows))
    return failures


async def verify_voices(tts: GnaniTimbreTTS) -> list[str]:
    print("\n== Voices: two-word phrase in each voice's preferred language ==")
    failures: list[str] = []
    ok = 0
    for voice in VOICES:
        try:
            pcm = await synthesize(tts, TWO_WORDS[voice.language], voice.language, voice.name)
        except ProviderError as exc:
            print(f"FAIL {voice.name} ({voice.language}): {exc}")
            failures.append(f"{voice.name} ({voice.language}): {exc}")
            continue
        seconds = len(pcm) / 2 / STT_RATE
        if seconds < MIN_VOICE_AUDIO_S:
            print(f"FAIL {voice.name} ({voice.language}): only {seconds:.2f}s of audio")
            failures.append(f"{voice.name} ({voice.language}): {seconds:.2f}s of audio")
            continue
        ok += 1
        print(f"ok   {voice.name:<9} {voice.gender:<6} {voice.language} {seconds:.1f}s")
    print(f"\n{ok}/{len(VOICES)} voices synthesised")
    return failures


async def main(env_file: str, skip_voices: bool) -> int:
    settings = Settings(_env_file=env_file)
    if settings.gnani_api_key is None:
        print("GNANI_API_KEY is not set")
        return 2
    # Ask Timbre for 16 kHz so Prisma gets its native rate; `resample` covers any other rate.
    settings = settings.model_copy(update={"gnani_tts_sample_rate": STT_RATE})
    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=5.0)) as client:
        tts = GnaniTimbreTTS(settings, client)
        stt = GnaniPrismaSTT(settings)
        failures = await verify_languages(stt, tts)
        if not skip_voices:
            failures += await verify_voices(tts)
    print("\nFailures:" if failures else "\nAll checks passed.")
    for failure in failures:
        print(" -", failure)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]  # Indic text on cp1252
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--env-file", default=".env")
    parser.add_argument("--skip-voices", action="store_true")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(main(args.env_file, args.skip_voices)))
