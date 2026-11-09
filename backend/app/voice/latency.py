"""Per-turn latency marks and rolling p50/p95 aggregates."""

from __future__ import annotations

import statistics
from collections import deque
from time import perf_counter
from typing import Literal

from app.websocket.protocol import TurnMetrics

Stage = Literal[
    "speech_end",
    "stt_final",
    "llm_request",
    "llm_first_token",
    "tts_request",
    "tts_first_byte",
    "first_audio_sent",
    "playback_started",
    "interrupt",
    "stop_complete",
]

WINDOW = 200  # most recent turns kept per metric
METRICS = (
    "stt_ms",
    "llm_ttft_ms",
    "tts_ttfb_ms",
    "speech_end_to_first_audio_ms",
    "interrupt_to_stop_ms",
)


class TurnTimer:
    """Monotonic timestamps for one turn. The first mark of a stage wins, so
    "first token" and "first byte" can be marked on every chunk."""

    def __init__(self) -> None:
        self._marks: dict[Stage, float] = {}

    def mark(self, stage: Stage, at: float | None = None) -> None:
        self._marks.setdefault(stage, perf_counter() if at is None else at)

    def metrics(self, turn_id: int, generation_id: int) -> TurnMetrics:
        # Server VAD gives no "speech end", so latency is then measured from the final transcript.
        origin = self._marks.get("speech_end", self._marks.get("stt_final"))
        return TurnMetrics(
            turn_id=turn_id,
            generation_id=generation_id,
            stt_ms=self._between("speech_end", "stt_final"),
            llm_ttft_ms=self._between("llm_request", "llm_first_token"),
            tts_ttfb_ms=self._between("tts_request", "tts_first_byte"),
            speech_end_to_first_audio_ms=_ms(origin, self._marks.get("first_audio_sent")),
            interrupt_to_stop_ms=self._between("interrupt", "stop_complete"),
        )

    def _between(self, start: Stage, end: Stage) -> float | None:
        return _ms(self._marks.get(start), self._marks.get(end))


def _ms(start: float | None, end: float | None) -> float | None:
    return None if start is None or end is None else round((end - start) * 1000, 1)


class LatencyStats:
    """Bounded rolling window per metric; cheap enough to record on every turn."""

    def __init__(self, window: int = WINDOW) -> None:
        self._samples: dict[str, deque[float]] = {name: deque(maxlen=window) for name in METRICS}

    def record(self, metrics: TurnMetrics) -> None:
        for name, samples in self._samples.items():
            if (value := getattr(metrics, name)) is not None:
                samples.append(value)

    def summary(self) -> dict[str, dict[str, float]]:
        """`{metric: {"count", "p50", "p95"}}` for every metric with samples."""
        return {
            name: {
                "count": len(samples),
                "p50": round(statistics.median(samples), 1),
                "p95": round(_p95(samples), 1),
            }
            for name, samples in self._samples.items()
            if samples
        }

    def clear(self) -> None:
        for samples in self._samples.values():
            samples.clear()


def _p95(samples: deque[float]) -> float:
    if len(samples) < 2:
        return samples[0]
    return statistics.quantiles(samples, n=20, method="inclusive")[-1]


# Process-wide aggregate; the app can expose `latency_stats.summary()` at /metrics.
latency_stats = LatencyStats()
