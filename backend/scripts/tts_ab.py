"""A/B the Timbre transports (SSE vs persistent WebSocket) and output sample rates, live.

Synthesises the same ten short sentences (English, Hindi, Hinglish) strictly one after another
(the account allows one active request) for every transport x sample-rate cell, and reports
time-to-first-audio-byte and total synthesis time. Cells are interleaved round by round, so
drift in the service hits all of them alike. Needs real keys; run from `backend/`:

    PYTHONPATH=. python scripts/tts_ab.py --rounds 3 [--env-file .env]

Per cell the transport is fresh: WebSocket pays its handshake in `warm_up()`, SSE gets one
untimed one-word request to open its keep-alive connection. Both warm-up costs are reported
separately and are NOT part of the TTFB columns. TTFB is measured from calling `synthesize()`
to the first PCM chunk, so it includes the transport's own request overhead.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import math
import statistics
import time
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from typing import cast

import httpx

from app.config import Settings
from app.providers.base import TextToSpeech
from app.providers.gnani_timbre import GnaniTimbreTTS
from app.providers.gnani_timbre_ws import GnaniTimbreRealtimeTTS

# (text, language). Short first: the real first reply segment is often 2-3 words.
SENTENCES: tuple[tuple[str, str], ...] = (
    ("Haan bilkul, batao.", "hi-en"),
    ("Sure, I can help you with that.", "en-IN"),
    ("Theek hai, main abhi dekhta hoon.", "hi-en"),
    ("आज मौसम बहुत अच्छा है, बाहर घूमने चलें?", "hi-IN"),
    ("Okay, so what time works best for you tomorrow?", "en-IN"),
    ("Aapka order kal subah tak pahunch jayega, tension mat lo.", "hi-en"),
    ("मैं आपकी बात समझ गया, चलिए इसे एक साथ सुलझाते हैं।", "hi-IN"),
    ("That sounds like a great plan, let me note it down.", "en-IN"),
    ("Arre waah, yeh toh bahut badhiya khabar hai yaar!", "hi-en"),
    ("Chaliye ab hum agle step par chalte hain, ready ho?", "hi-en"),
)
INTERRUPTED = (
    "Yeh ek lamba vaakya hai jo kaafi der tak chalega, taaki hum beech mein ise rok sakein, "
    "theek hai na dost, aur phir dekhte hain ki agla jawab kitni jaldi aata hai.",
    "hi-en",
)
WARM_UP_TEXT = ("ek", "hi-en")
SAMPLE_RATES = (16_000, 24_000)
TRANSPORTS = ("sse", "ws")


@dataclass
class Cell:
    transport: str
    sample_rate: int
    ttfb_ms: list[float] = field(default_factory=list)
    first_sentence_ttfb_ms: list[float] = field(default_factory=list)
    total_ms: list[float] = field(default_factory=list)
    audio_ms: list[float] = field(default_factory=list)
    warm_up_ms: list[float] = field(default_factory=list)
    retries: int = 0
    failures: int = 0

    @property
    def label(self) -> str:
        return f"{self.transport:>3} {self.sample_rate // 1000:>2}k"


class RetryCounter(logging.Handler):
    """Counts adapter retries; a retry means a 429/transport blip polluted that sample."""

    def __init__(self) -> None:
        super().__init__(logging.WARNING)
        self.count = 0

    def emit(self, record: logging.LogRecord) -> None:
        if "retry" in record.getMessage():
            self.count += 1


def percentile(values: list[float], q: float) -> float:
    """Nearest-rank percentile; honest for the small samples this script produces."""
    ordered = sorted(values)
    return ordered[max(0, math.ceil(q * len(ordered)) - 1)]


async def open_tts(
    transport: str, base: Settings, sample_rate: int, client: httpx.AsyncClient
) -> tuple[TextToSpeech, float]:
    """A fresh, warmed transport and the warm-up time in ms."""
    settings = base.model_copy(update={"gnani_tts_sample_rate": sample_rate})
    started = time.perf_counter()
    if transport == "ws":
        ws = GnaniTimbreRealtimeTTS(settings)
        await ws.warm_up()
        return ws, (time.perf_counter() - started) * 1000
    tts = GnaniTimbreTTS(settings, client)
    async for _ in tts.synthesize(WARM_UP_TEXT[0], language=WARM_UP_TEXT[1]):
        pass
    return tts, (time.perf_counter() - started) * 1000


async def close_tts(tts: TextToSpeech) -> None:
    if isinstance(tts, GnaniTimbreRealtimeTTS):
        await tts.aclose()


async def first_chunk_ms(tts: TextToSpeech, text: str, language: str) -> float:
    started = time.perf_counter()
    async for _ in tts.synthesize(text, language=language):
        return (time.perf_counter() - started) * 1000
    raise RuntimeError("no audio")


async def run_interrupts(
    transport: str, base: Settings, repeats: int, gap_s: float, sample_rate: int = 24_000
) -> list[float]:
    """Barge-in cost: cut a long reply after its first chunk, then wait `gap_s` (the user's next
    turn: STT + LLM, >= ~1.5 s in practice) and time the next reply's TTFB.

    WebSocket must drop the socket and reconnect (its handshake is 0.3-4 s), SSE just closes the
    response, so this is where the persistent socket can lose.
    """
    client = httpx.AsyncClient(timeout=httpx.Timeout(15.0, connect=5.0))
    tts, _ = await open_tts(transport, base, sample_rate, client)
    after: list[float] = []
    try:
        for _ in range(repeats):
            stream = cast(
                "AsyncGenerator[bytes]", tts.synthesize(INTERRUPTED[0], language=INTERRUPTED[1])
            )
            async with contextlib.aclosing(stream):
                await anext(stream)  # first audio arrived: now the user barges in
            await asyncio.sleep(gap_s)
            after.append(await first_chunk_ms(tts, *SENTENCES[0]))
    finally:
        await close_tts(tts)
        await client.aclose()
    return after


async def run_cell(cell: Cell, base: Settings, counter: RetryCounter) -> None:
    client = httpx.AsyncClient(timeout=httpx.Timeout(15.0, connect=5.0))
    tts, warm_up_ms = await open_tts(cell.transport, base, cell.sample_rate, client)
    cell.warm_up_ms.append(warm_up_ms)
    bytes_per_ms = cell.sample_rate * 2 / 1000
    try:
        for index, (text, language) in enumerate(SENTENCES):
            retries_before = counter.count
            started = time.perf_counter()
            first: float | None = None
            audio_bytes = 0
            try:
                async for pcm in tts.synthesize(text, language=language):
                    if first is None:
                        first = (time.perf_counter() - started) * 1000
                    audio_bytes += len(pcm)
            except Exception as exc:  # report and carry on: one failed sample must not kill a run
                cell.failures += 1
                print(f"  ! {cell.label} sentence {index}: {exc!r}")
                continue
            total = (time.perf_counter() - started) * 1000
            cell.retries += counter.count - retries_before
            if first is None:
                cell.failures += 1
                continue
            cell.ttfb_ms.append(first)
            if index == 0:
                cell.first_sentence_ttfb_ms.append(first)
            cell.total_ms.append(total)
            cell.audio_ms.append(audio_bytes / bytes_per_ms)
    finally:
        await close_tts(tts)
        await client.aclose()


def report(cells: list[Cell]) -> None:
    header = (
        "cell    n  ttfb p50  p95  mean | short p50 | total p50  p95 | rtf p50 | warm-up ms | "
        "retry fail"
    )
    print(header)
    print("-" * len(header))
    for c in cells:
        if not c.ttfb_ms:
            print(f"{c.label}  no samples")
            continue
        rtf = statistics.median(t / a for t, a in zip(c.total_ms, c.audio_ms, strict=True))
        print(
            f"{c.label} {len(c.ttfb_ms):>3}  {percentile(c.ttfb_ms, 0.5):>7.0f} "
            f"{percentile(c.ttfb_ms, 0.95):>4.0f} {statistics.fmean(c.ttfb_ms):>5.0f} | "
            f"{statistics.median(c.first_sentence_ttfb_ms):>8.0f} | "
            f"{percentile(c.total_ms, 0.5):>8.0f} {percentile(c.total_ms, 0.95):>5.0f} | "
            f"{rtf:>7.2f} | {statistics.median(c.warm_up_ms):>10.0f} | "
            f"{c.retries:>5} {c.failures:>4}"
        )
    print(
        "\nttfb/total in ms; 'short' = TTFB of the first sentence only; rtf = synth time / audio."
    )
    for rate in SAMPLE_RATES:
        sse = next(c for c in cells if c.transport == "sse" and c.sample_rate == rate)
        ws = next(c for c in cells if c.transport == "ws" and c.sample_rate == rate)
        if sse.ttfb_ms and ws.ttfb_ms:
            delta = percentile(ws.ttfb_ms, 0.5) - percentile(sse.ttfb_ms, 0.5)
            print(f"{rate // 1000}k: ws - sse TTFB p50 = {delta:+.0f} ms")
    for transport in TRANSPORTS:
        lo = next(c for c in cells if c.transport == transport and c.sample_rate == 16_000)
        hi = next(c for c in cells if c.transport == transport and c.sample_rate == 24_000)
        if lo.ttfb_ms and hi.ttfb_ms:
            delta = percentile(lo.ttfb_ms, 0.5) - percentile(hi.ttfb_ms, 0.5)
            print(f"{transport}: 16k - 24k TTFB p50 = {delta:+.0f} ms")


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--rounds", type=int, default=3, help="passes over all four cells")
    parser.add_argument("--interrupts", type=int, default=4, help="barge-in repeats per transport")
    parser.add_argument("--env-file", default=".env")
    args = parser.parse_args()

    base = Settings(_env_file=args.env_file)
    if base.gnani_api_key is None or not base.gnani_tts_voice:
        raise SystemExit("GNANI_API_KEY and GNANI_TTS_VOICE are required")
    counter = RetryCounter()
    logging.getLogger("app.providers").addHandler(counter)

    cells = [Cell(t, r) for r in SAMPLE_RATES for t in TRANSPORTS]
    for round_no in range(args.rounds):
        shift = round_no % len(cells)  # rotate the start so no cell always runs first
        for cell in cells[shift:] + cells[:shift]:
            await run_cell(cell, base, counter)
        print(f"round {round_no + 1}/{args.rounds} done")
    report(cells)
    if args.interrupts:
        print("\nbarge-in: long reply cut after its first chunk, then TTFB of the next reply")
        for gap_s in (0.0, 1.5):
            for transport in TRANSPORTS:
                after = await run_interrupts(transport, base, args.interrupts, gap_s)
                print(
                    f"  gap {gap_s:.1f}s {transport:>3}: p50 {percentile(after, 0.5):>5.0f} ms, "
                    f"max {max(after):>5.0f} ms, n={len(after)}"
                )
        print(f"adapter retries over the whole run: {counter.count}")


if __name__ == "__main__":
    asyncio.run(main())
