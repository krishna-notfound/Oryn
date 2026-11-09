"""End-to-end latency benchmark for a running Oryn backend, with real providers.

Synthesises short test utterances with the real Timbre adapter, then plays the role of the
browser: connects to the backend WebSocket with a Supabase access token, streams each
utterance as 20 ms PCM frames at real-time cadence followed by silence and `audio.end`,
"plays" the reply (sends `playback.started` / `playback.finished`) and collects the
per-turn `metrics` events. One turn per scenario is interrupted ~400 ms into the reply to
check barge-in: `interrupted` must arrive and no audio of the old generation may follow it.

Start the server first, then (from `backend/`):

    ORYN_TOKEN=<access token> PYTHONPATH=. python scripts/benchmark_pipeline.py --turns 3

The token is a user access token (1 h lifetime); it is read from `--token` or `ORYN_TOKEN`.
Provider keys come from the usual `.env` (needed only to synthesise the test audio).
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import os
import statistics
import struct
import sys
import time
from dataclasses import dataclass, field
from typing import Any

import httpx
from websockets.asyncio.client import ClientConnection, connect

from app.config import Settings
from app.providers.gnani_timbre import GnaniTimbreTTS

INPUT_RATE = 16_000
FRAME_S = 0.02
FRAME_BYTES = int(INPUT_RATE * FRAME_S) * 2
LEAD_IN_S = 0.2
TRAILING_SILENCE_S = 0.6
INTERRUPT_AFTER_AUDIO_S = 0.4
TURN_TIMEOUT_S = 60.0
STAGES = (
    "stt_ms",
    "client_end_to_transcript_ms",
    "llm_ttft_ms",
    "tts_ttfb_ms",
    "speech_end_to_first_audio_ms",
    "client_end_to_first_audio_ms",
    "interrupt_to_stop_ms",
    "client_interrupt_to_ack_ms",
)


@dataclass(frozen=True)
class Scenario:
    label: str
    session_language: str  # sent in session.start (Prisma language)
    tts_language: str  # used to synthesise the test audio
    utterances: tuple[str, ...]


SCENARIOS = (
    Scenario(
        "english",
        "en-IN",
        "en-IN",
        (
            "What is a good way to unwind after a long day?",
            "Tell me something interesting about the moon.",
            "I had a pretty tiring day at work today.",
        ),
    ),
    Scenario(
        "hindi",
        "hi-IN",
        "hi-IN",
        (
            "मुझे आज बहुत थकान लग रही है।",
            "कोई अच्छी सी कहानी सुनाओ।",
            "आज रात खाने में क्या बनाऊँ?",
        ),
    ),
    Scenario(
        "hinglish",
        "hi-IN",
        "hi-en",
        (
            "aaj mera din thoda busy tha, kuch light suggest karo",
            "mujhe thoda motivation chahiye, kuch bolo",
            "weekend pe kya plan karna chahiye?",
        ),
    ),
)


@dataclass
class TurnResult:
    scenario: str
    index: int
    expected: str
    interrupted: bool
    transcript: str = ""
    reply: str = ""
    server: dict[str, float | None] = field(default_factory=dict)
    client: dict[str, float | None] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    stale_frames: int = 0

    def value(self, stage: str) -> float | None:
        return self.client.get(stage) if stage.startswith("client_") else self.server.get(stage)


class Player:
    """The browser's side of one turn: reads server events and acts like a playback queue."""

    def __init__(self, ws: ClientConnection) -> None:
        self._ws = ws
        self.output_rate = INPUT_RATE
        self.ready = asyncio.Event()
        self.reset("", 0, False)

    def reset(self, scenario: str, index: int, interrupted: bool) -> None:
        self.result = TurnResult(scenario, index, "", interrupted)
        self.metrics_seen = asyncio.Event()
        self.interrupted_seen = asyncio.Event()
        self.audio_started = asyncio.Event()
        self.speech_end_at = 0.0
        self.first_audio_at: float | None = None
        self.interrupt_sent_at: float | None = None
        self.audio_bytes = 0
        self.streamed_reply = ""  # kept for interrupted turns, which never get assistant.done
        self.generation: int | None = None
        self._interrupted_generation: int | None = None
        self._done_playing: asyncio.Task[None] | None = None

    async def pump(self) -> None:
        async for message in self._ws:
            now = time.perf_counter()
            if isinstance(message, bytes):
                await self._on_audio(message, now)
            else:
                await self._on_event(json.loads(message), now)

    async def _on_audio(self, frame: bytes, now: float) -> None:
        (generation,) = struct.unpack(">I", frame[:4])
        if generation == self._interrupted_generation:
            self.result.stale_frames += 1
            return
        self.audio_bytes += len(frame) - 4
        if self.first_audio_at is None:
            self.first_audio_at, self.generation = now, generation
            self.result.client["client_end_to_first_audio_ms"] = _ms(self.speech_end_at, now)
            self.audio_started.set()
            await self._send({"type": "playback.started", "generation_id": generation})

    async def _on_event(self, event: dict[str, Any], now: float) -> None:
        match event["type"]:
            case "session.ready":
                self.output_rate = event["output_sample_rate"]
                self.ready.set()
            case "transcript.final":
                self.result.transcript += event["text"]
                self.result.client["client_end_to_transcript_ms"] = _ms(self.speech_end_at, now)
            case "assistant.text":
                self.streamed_reply += event["delta"]
            case "assistant.done":
                self.result.reply = event["text"]
                self._done_playing = asyncio.create_task(self._finish_playback(event))
            case "interrupted":
                self._interrupted_generation = event["generation_id"]
                if self.interrupt_sent_at is not None:
                    self.result.client["client_interrupt_to_ack_ms"] = _ms(
                        self.interrupt_sent_at, now
                    )
                self.interrupted_seen.set()
            case "metrics":
                self.result.server = {k: event.get(k) for k in STAGES if k in event}
                self.metrics_seen.set()
            case "error":
                self.result.errors.append(f"{event['code']}: {event['message']}")

    async def _finish_playback(self, event: dict[str, Any]) -> None:
        """Browser playback: the queue drains once the received audio has played out."""
        if self.first_audio_at is None:
            return
        duration = self.audio_bytes / 2 / self.output_rate
        await asyncio.sleep(max(0.0, self.first_audio_at + duration - time.perf_counter()))
        await self._send({"type": "playback.finished", "generation_id": event["generation_id"]})

    async def _send(self, message: dict[str, Any]) -> None:
        with contextlib.suppress(Exception):  # socket closing at the end of a run
            await self._ws.send(json.dumps(message))

    async def interrupt_after_audio(self) -> None:
        await asyncio.wait_for(self.audio_started.wait(), TURN_TIMEOUT_S)
        await asyncio.sleep(INTERRUPT_AFTER_AUDIO_S)
        self.interrupt_sent_at = time.perf_counter()
        await self._send({"type": "user.interrupt"})
        await asyncio.wait_for(self.interrupted_seen.wait(), 5)
        await asyncio.sleep(0.5)  # any stale frame still in flight would show up here


def _ms(start: float, end: float) -> float:
    return round((end - start) * 1000, 1)


async def synthesise(settings: Settings, scenario: Scenario) -> list[bytes]:
    """Test audio from the real Timbre adapter, requested at the 16 kHz the backend expects."""
    settings = settings.model_copy(update={"gnani_tts_sample_rate": INPUT_RATE})
    async with httpx.AsyncClient(timeout=30) as client:
        tts = GnaniTimbreTTS(settings, client)
        return [
            b"".join([pcm async for pcm in tts.synthesize(text, language=scenario.tts_language)])
            for text in scenario.utterances
        ]


async def stream_utterance(ws: ClientConnection, player: Player, pcm: bytes) -> None:
    """Real-time 20 ms frames: lead-in silence, speech, trailing silence, then `audio.end`."""
    speech_frames = [pcm[i : i + FRAME_BYTES] for i in range(0, len(pcm), FRAME_BYTES)]
    silence = bytes(FRAME_BYTES)
    lead = [silence] * int(LEAD_IN_S / FRAME_S)
    tail = [silence] * int(TRAILING_SILENCE_S / FRAME_S)
    start = time.perf_counter()
    for i, frame in enumerate(lead + speech_frames + tail):
        await asyncio.sleep(max(0.0, start + i * FRAME_S - time.perf_counter()))
        await ws.send(frame.ljust(FRAME_BYTES, b"\x00"))
        if i == len(lead) + len(speech_frames) - 1:
            player.speech_end_at = time.perf_counter()
    await ws.send(json.dumps({"type": "audio.end"}))


async def run_scenario(
    url: str, token: str, scenario: Scenario, audio: list[bytes], turns: int
) -> list[TurnResult]:
    results: list[TurnResult] = []
    async with connect(url, max_size=None, max_queue=None) as ws:
        player = Player(ws)
        pump = asyncio.create_task(player.pump())
        await ws.send(
            json.dumps(
                {"type": "session.start", "token": token, "language": scenario.session_language}
            )
        )
        # Like the browser, stream the mic only once the session is ready (Prisma connects
        # during the handshake); frames sent earlier would arrive as one burst.
        await asyncio.wait_for(player.ready.wait(), TURN_TIMEOUT_S)
        for index in range(turns):
            interrupted = index == 0  # one barge-in run per language
            player.reset(scenario.label, index, interrupted)
            player.result.expected = scenario.utterances[index % len(scenario.utterances)]
            await stream_utterance(ws, player, audio[index % len(audio)])
            waiters = [player.metrics_seen.wait()]
            if interrupted:
                waiters.append(player.interrupt_after_audio())
            try:
                await asyncio.wait_for(asyncio.gather(*waiters), TURN_TIMEOUT_S)
            except TimeoutError:
                player.result.errors.append("timeout waiting for the turn to finish")
            if player._done_playing:
                await player._done_playing
            player.result.reply = player.result.reply or f"(cut off) {player.streamed_reply}"
            results.append(player.result)
            await asyncio.sleep(0.3)
        await ws.send(json.dumps({"type": "session.end"}))
        pump.cancel()
    return results


def percentile(values: list[float], q: float) -> float:
    ordered = sorted(values)
    pos = (len(ordered) - 1) * q
    low = int(pos)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (pos - low)


def print_turns(results: list[TurnResult]) -> None:
    print("\nPER-TURN (ms)")
    print(f"{'lang':9}{'#':>2} {'int':>3} " + " ".join(f"{s[:11]:>11}" for s in STAGES))
    for r in results:
        cells = " ".join(f"{'-' if (v := r.value(s)) is None else v:>11}" for s in STAGES)
        print(f"{r.scenario:9}{r.index:>2} {'yes' if r.interrupted else '':>3} {cells}")
    print("\nTRANSCRIPTS AND REPLIES")
    for r in results:
        flag = " [INTERRUPTED]" if r.interrupted else ""
        print(f"- {r.scenario} #{r.index}{flag}")
        print(f"    said : {r.expected}")
        print(f"    heard: {r.transcript}")
        print(f"    reply: {r.reply}")
        for err in r.errors:
            print(f"    ERROR: {err}")


def print_summary(results: list[TurnResult]) -> None:
    groups = {"all": results} | {
        label: [r for r in results if r.scenario == label]
        for label in dict.fromkeys(r.scenario for r in results)
    }
    print("\nSUMMARY p50 / p95 (n)")
    for name, rows in groups.items():
        print(f"[{name}]")
        for stage in STAGES:
            values = [v for r in rows if (v := r.value(stage)) is not None]
            if values:
                p50, p95 = statistics.median(values), percentile(values, 0.95)
                print(f"  {stage:30} {p50:8.0f} / {p95:8.0f}  (n={len(values)})")
    stale = sum(r.stale_frames for r in results)
    ran = [r for r in results if r.interrupted]
    acked = [r for r in ran if r.client.get("client_interrupt_to_ack_ms") is not None]
    print(
        f"\nBARGE-IN: {len(acked)}/{len(ran)} interrupted turns acknowledged; "
        f"stale audio frames after `interrupted`: {stale}"
    )


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--url", default="ws://localhost:8000/ws")
    parser.add_argument("--token", default=os.environ.get("ORYN_TOKEN"))
    parser.add_argument("--turns", type=int, default=3, help="turns per language (max 3)")
    parser.add_argument("--env-file", default=".env", help="provider keys for the test audio")
    parser.add_argument("--only", choices=[s.label for s in SCENARIOS], action="append")
    args = parser.parse_args()
    if not args.token:
        sys.exit("pass --token or set ORYN_TOKEN")

    settings = Settings(_env_file=args.env_file)
    scenarios = [s for s in SCENARIOS if not args.only or s.label in args.only]
    results: list[TurnResult] = []
    for scenario in scenarios:
        print(f"synthesising {scenario.label} test audio ...", flush=True)
        audio = await synthesise(settings, scenario)
        print(f"running {scenario.label} ...", flush=True)
        results += await run_scenario(
            args.url, args.token, scenario, audio, min(args.turns, len(audio))
        )
    print_turns(results)
    print_summary(results)
    failed = any(r.errors for r in results) or any(r.stale_frames for r in results)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
