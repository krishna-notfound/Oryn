"""Per-turn latency marks and rolling p50/p95/p99 aggregates."""

from __future__ import annotations

import statistics
from collections import deque
from time import perf_counter
from typing import Literal

from app.websocket.protocol import TurnMetrics

Stage = Literal[
    "speech_end",
    "commit_sent",
    "stt_final",
    "turn_started",
    "llm_request",
    "llm_first_token",
    "first_segment",
    "tts_slot_acquired",
    "tts_request",
    "tts_first_byte",
    "first_audio_sent",
    "playback_started",
    "cancel_requested",
    "cancel_completed",
]

WINDOW = 200  # most recent turns kept per metric
P99_MIN_SAMPLES = 20  # below this the 99th percentile is just the maximum
METRICS = (
    "stt_ms",
    "llm_ttft_ms",
    "segmentation_ms",
    "tts_slot_wait_ms",
    "tts_ttfb_ms",
    "server_first_audio_ms",
    "speech_end_to_first_audio_ms",
    "interrupt_to_stop_ms",
    "perceived_ms",  # reported by the browser, see LatencyStats.record_perceived
)


class TurnTimer:
    """Monotonic timestamps for one turn. The first mark of a stage wins, so
    "first token" and "first byte" can be marked on every chunk."""

    def __init__(self) -> None:
        self._marks: dict[Stage, float] = {}

    def mark(self, stage: Stage, at: float | None = None) -> None:
        self._marks.setdefault(stage, perf_counter() if at is None else at)

    def metrics(self, turn_id: int, generation_id: int) -> TurnMetrics:
        return TurnMetrics(
            turn_id=turn_id,
            generation_id=generation_id,
            stt_ms=self._between("speech_end", "stt_final"),
            llm_ttft_ms=self._between("llm_request", "llm_first_token"),
            segmentation_ms=self._between("llm_first_token", "first_segment"),
            tts_slot_wait_ms=self._between("tts_request", "tts_slot_acquired"),
            tts_ttfb_ms=self._between("tts_request", "tts_first_byte"),
            server_first_audio_ms=self._between("stt_final", "first_audio_sent"),
            speech_end_to_first_audio_ms=self._between("speech_end", "first_audio_sent"),
            interrupt_to_stop_ms=self._between("cancel_requested", "cancel_completed"),
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
            if (value := getattr(metrics, name, None)) is not None:
                samples.append(value)

    def record_perceived(self, perceived_ms: float) -> None:
        """The browser's own speech-end -> first audible audio measurement."""
        self._samples["perceived_ms"].append(perceived_ms)

    def summary(self) -> dict[str, dict[str, float]]:
        """`{metric: {"count", "p50", "p95", "p99"?}}` for every metric with samples; `p99`
        appears once there are enough samples for it to mean something."""
        out: dict[str, dict[str, float]] = {}
        for name, samples in self._samples.items():
            if not samples:
                continue
            row = {
                "count": len(samples),
                "p50": round(statistics.median(samples), 1),
                "p95": round(_percentile(samples, 95), 1),
            }
            if len(samples) >= P99_MIN_SAMPLES:
                row["p99"] = round(_percentile(samples, 99), 1)
            out[name] = row
        return out

    def clear(self) -> None:
        for samples in self._samples.values():
            samples.clear()


def _percentile(samples: deque[float], q: int) -> float:
    if len(samples) < 2:
        return samples[0]
    return statistics.quantiles(samples, n=100, method="inclusive")[q - 1]


# Process-wide aggregate, served at /metrics.
latency_stats = LatencyStats()
