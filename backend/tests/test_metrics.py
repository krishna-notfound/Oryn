"""/metrics: protected by a bearer token, and reports latency percentiles plus process health."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app.observability as observability
from app.config import Settings
from app.main import build_app
from app.observability import LoopLagMonitor, rss_bytes
from app.voice.latency import latency_stats
from app.websocket.protocol import TurnMetrics

TOKEN = "s3cret-metrics-token"


def client_for(tmp_path: Path, **settings: object) -> TestClient:
    return TestClient(
        build_app(Settings(provider_mode="fake", memory_db_path=tmp_path / "m.db", **settings))  # type: ignore[arg-type]
    )


def test_metrics_requires_token(tmp_path: Path) -> None:
    with client_for(tmp_path, metrics_token=TOKEN) as client:
        assert client.get("/metrics").status_code == 401
        assert client.get("/metrics", headers={"Authorization": "Bearer nope"}).status_code == 401
        assert client.get("/metrics", headers={"Authorization": TOKEN}).status_code == 401
        ok = client.get("/metrics", headers={"Authorization": f"Bearer {TOKEN}"})
        assert ok.status_code == 200


def test_metrics_does_not_exist_without_a_configured_token(tmp_path: Path) -> None:
    with client_for(tmp_path) as client:
        assert client.get("/metrics").status_code == 404
        assert client.get("/metrics", headers={"Authorization": "Bearer "}).status_code == 404


def test_metrics_reports_percentiles_sessions_and_process_health(tmp_path: Path) -> None:
    latency_stats.clear()
    for value in range(1, 31):
        latency_stats.record(TurnMetrics(turn_id=value, generation_id=value, llm_ttft_ms=value))
    try:
        with client_for(tmp_path, metrics_token=TOKEN, max_sessions=7) as client:
            body = client.get("/metrics", headers={"Authorization": f"Bearer {TOKEN}"}).json()
    finally:
        latency_stats.clear()

    assert set(body["latency_ms"]["llm_ttft_ms"]) == {"count", "p50", "p95", "p99"}
    assert (body["active_sessions"], body["max_sessions"]) == (0, 7)
    assert "event_loop_lag_ms" in body and "rss_bytes" in body


async def test_loop_lag_monitor_sees_a_blocked_event_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(observability, "TICK_S", 0.01)
    monitor = LoopLagMonitor()
    assert monitor.summary() == {}
    monitor.start()
    await asyncio.sleep(0.05)
    time.sleep(0.15)  # noqa: ASYNC251 - a callback that hogs the loop, as a stall would
    await asyncio.sleep(0.05)
    await monitor.stop()

    summary = monitor.summary()
    assert summary["max"] >= 100
    assert summary["p50"] < summary["max"]


def test_rss_is_a_positive_number_where_supported() -> None:
    value = rss_bytes()
    assert value is None or value > 1_000_000
