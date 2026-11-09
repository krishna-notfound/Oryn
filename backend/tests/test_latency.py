from app.voice.latency import LatencyStats, Stage, TurnTimer
from app.websocket.protocol import TurnMetrics


def test_timer_builds_metrics_from_marks() -> None:
    timer = TurnTimer()
    marks: list[tuple[Stage, float]] = [
        ("speech_end", 1.0),
        ("stt_final", 1.25),
        ("llm_request", 1.26),
        ("llm_first_token", 1.46),
        ("tts_request", 1.5),
        ("tts_first_byte", 1.7),
        ("first_audio_sent", 1.75),
        ("interrupt", 3.0),
        ("stop_complete", 3.04),
    ]
    for stage, at in marks:
        timer.mark(stage, at)

    metrics = timer.metrics(turn_id=2, generation_id=3)

    assert (metrics.turn_id, metrics.generation_id) == (2, 3)
    assert metrics.stt_ms == 250.0
    assert metrics.llm_ttft_ms == 200.0
    assert metrics.tts_ttfb_ms == 200.0
    assert metrics.speech_end_to_first_audio_ms == 750.0
    assert metrics.interrupt_to_stop_ms == 40.0


def test_first_mark_of_a_stage_wins() -> None:
    timer = TurnTimer()
    timer.mark("llm_request", 1.0)
    timer.mark("llm_first_token", 1.1)
    timer.mark("llm_first_token", 5.0)
    assert timer.metrics(1, 1).llm_ttft_ms == 100.0


def test_missing_stages_are_none() -> None:
    metrics = TurnTimer().metrics(1, 1)
    assert metrics.stt_ms is None and metrics.speech_end_to_first_audio_ms is None


def test_without_speech_end_latency_is_measured_from_final_transcript() -> None:
    timer = TurnTimer()
    timer.mark("stt_final", 2.0)
    timer.mark("first_audio_sent", 2.5)
    metrics = timer.metrics(1, 1)
    assert metrics.stt_ms is None
    assert metrics.speech_end_to_first_audio_ms == 500.0


def test_percentiles_over_samples() -> None:
    stats = LatencyStats()
    for value in range(1, 101):
        stats.record(TurnMetrics(turn_id=value, generation_id=value, llm_ttft_ms=float(value)))
    summary = stats.summary()["llm_ttft_ms"]
    assert summary["count"] == 100
    assert summary["p50"] == 50.5
    assert 94 <= summary["p95"] <= 96
    assert "stt_ms" not in stats.summary()


def test_single_sample_percentiles() -> None:
    stats = LatencyStats()
    stats.record(TurnMetrics(turn_id=1, generation_id=1, stt_ms=120.0))
    assert stats.summary()["stt_ms"] == {"count": 1, "p50": 120.0, "p95": 120.0}


def test_window_is_bounded() -> None:
    stats = LatencyStats(window=5)
    for value in range(20):
        stats.record(TurnMetrics(turn_id=value, generation_id=value, stt_ms=float(value)))
    summary = stats.summary()["stt_ms"]
    assert summary["count"] == 5 and summary["p50"] == 17.0
