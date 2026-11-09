import asyncio
import base64
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import aclosing, asynccontextmanager

import pytest
from websockets.asyncio.server import ServerConnection, serve

from app.config import Settings
from app.providers.base import ProviderError
from app.providers.gnani_timbre import GnaniTimbreTTS
from app.providers.gnani_timbre_ws import GnaniTimbreRealtimeTTS
from app.providers.registry import build_providers

WAV_HEADER = b"RIFF\xff\xff\xff\xffWAVEfmt " + bytes(20) + b"data\xff\xff\xff\xff"


def audio_frame(index: int, pcm: bytes) -> str:
    data = {"chunk_index": index, "audio": base64.b64encode(pcm).decode(), "is_final": False}
    return json.dumps({"type": "audio", "data": data})


START = json.dumps({"type": "start", "message": "Streaming started", "request_id": "r"})
# Verified live: a request ends with TWO complete frames, the one with `data` first.
COMPLETE = json.dumps(
    {"type": "complete", "data": {"chunk_index": 9, "audio": "", "is_final": True}}
)
COMPLETE_TRAILER = json.dumps(
    {"type": "complete", "message": "Streaming completed", "request_id": "r"}
)


class FakeTimbre:
    """A local Timbre WebSocket. `script` handles one request (n = 1-based request count) and
    may block to mimic a slow synthesis; the default answers with two audio chunks."""

    def __init__(self) -> None:
        self.connections = 0
        self.closed = 0
        self.requests: list[dict[str, object]] = []
        self.headers: list[str | None] = []
        self.active = 0
        self.max_active = 0
        self.script: Callable[[ServerConnection, int], Awaitable[None]] = self.respond

    async def respond(self, ws: ServerConnection, n: int) -> None:
        await ws.send(START)
        await ws.send(audio_frame(0, WAV_HEADER + b"\x01\x02"))
        await ws.send(audio_frame(1, b"\x03\x04"))
        await ws.send(COMPLETE)
        await ws.send(COMPLETE_TRAILER)

    async def handler(self, ws: ServerConnection) -> None:
        self.connections += 1
        self.headers.append(ws.request.headers.get("X-API-Key-ID") if ws.request else None)
        try:
            async for raw in ws:
                self.requests.append(json.loads(raw))
                self.active += 1
                self.max_active = max(self.max_active, self.active)
                script = asyncio.create_task(self.script(ws, len(self.requests)))
                gone = asyncio.create_task(ws.wait_closed())
                try:  # a client that disconnects mid-synthesis stops the script, like the real one
                    await asyncio.wait({script, gone}, return_when=asyncio.FIRST_COMPLETED)
                finally:
                    script.cancel()
                    gone.cancel()
                    self.active -= 1
        finally:
            self.closed += 1


def settings_for(url: str) -> Settings:
    return Settings(
        _env_file=None,  # type: ignore[call-arg]
        gnani_api_key="test-key",  # type: ignore[arg-type]
        gnani_tts_voice="Nalini",
        gnani_tts_ws_url=url,
    )


@asynccontextmanager
async def fake_timbre() -> AsyncIterator[tuple[FakeTimbre, Settings]]:
    fake = FakeTimbre()
    async with serve(fake.handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        yield fake, settings_for(f"ws://127.0.0.1:{port}")


async def pcm_of(tts: GnaniTimbreRealtimeTTS, text: str = "namaste") -> list[bytes]:
    return [c async for c in tts.synthesize(text, language="hi-IN")]


async def eventually(check: Callable[[], bool]) -> None:
    async with asyncio.timeout(2.0):
        while not check():  # noqa: ASYNC110 - polling a counter owned by the fake server
            await asyncio.sleep(0.005)


async def test_two_sequential_syntheses_reuse_one_connection() -> None:
    async with fake_timbre() as (fake, settings):
        tts = GnaniTimbreRealtimeTTS(settings, backoff=(0,))
        first = await pcm_of(tts, "pehla")
        second = await pcm_of(tts, "doosra")
        await tts.aclose()
    assert first == second == [b"\x01\x02", b"\x03\x04"]
    assert fake.connections == 1
    assert [r["text"] for r in fake.requests] == ["pehla", "doosra"]


async def test_config_goes_in_the_json_body_and_the_key_in_the_header() -> None:
    async with fake_timbre() as (fake, settings):
        tts = GnaniTimbreRealtimeTTS(settings)
        await pcm_of(tts)
        await tts.aclose()
    assert fake.headers == ["test-key"]
    assert fake.requests[0] == {
        "text": "namaste",
        "model": "timbre-v2.5",
        "voice": "Nalini",
        "language": "hi-IN",
        "audio_config": {
            "sample_rate": 24_000,
            "num_channels": 1,
            "sample_width": 2,
            "encoding": "linear_pcm",
            "container": "raw",
        },
    }


async def test_wav_header_stripped_and_samples_realigned() -> None:
    async with fake_timbre() as (fake, settings):

        async def odd(ws: ServerConnection, _: int) -> None:
            await ws.send(START)
            await ws.send(audio_frame(0, WAV_HEADER + b"\x01\x02\x03"))
            await ws.send(audio_frame(1, b"\x04"))
            await ws.send(COMPLETE)

        fake.script = odd
        tts = GnaniTimbreRealtimeTTS(settings)
        assert await pcm_of(tts) == [b"\x01\x02", b"\x03\x04"]
        await tts.aclose()


async def test_stale_trailing_complete_does_not_end_the_next_request_early() -> None:
    async with fake_timbre() as (fake, settings):
        tts = GnaniTimbreRealtimeTTS(settings)
        await pcm_of(tts)  # leaves the trailer frame unread on the socket
        assert await pcm_of(tts) == [b"\x01\x02", b"\x03\x04"]
        await tts.aclose()
    assert fake.connections == 1


async def test_warm_up_opens_the_socket_before_any_request() -> None:
    async with fake_timbre() as (fake, settings):
        tts = GnaniTimbreRealtimeTTS(settings)
        await tts.warm_up()
        assert fake.connections == 1 and not fake.requests
        await pcm_of(tts)
        await tts.aclose()
    assert fake.connections == 1


async def test_cancellation_mid_stream_closes_the_socket_and_next_call_reconnects() -> None:
    async with fake_timbre() as (fake, settings):
        gate = asyncio.Event()

        async def slow(ws: ServerConnection, n: int) -> None:
            if n > 1:
                return await fake.respond(ws, n)
            await ws.send(START)
            await ws.send(audio_frame(0, b"\x01\x02"))
            await gate.wait()  # synthesis keeps going until the client goes away

        fake.script = slow
        tts = GnaniTimbreRealtimeTTS(settings)
        stream = tts.synthesize("lamba", language="hi-IN")
        async with aclosing(stream):
            assert await anext(stream) == b"\x01\x02"
        await eventually(lambda: fake.closed == 1)  # the stale socket is really gone
        gate.set()
        assert await pcm_of(tts) == [b"\x01\x02", b"\x03\x04"]
        await tts.aclose()
    assert fake.connections == 2


async def test_cancelling_the_task_frees_the_slot_and_reconnects_in_background() -> None:
    async with fake_timbre() as (fake, settings):
        started = asyncio.Event()

        async def stall(ws: ServerConnection, n: int) -> None:
            if n > 1:
                return await fake.respond(ws, n)
            await ws.send(START)
            started.set()
            await asyncio.sleep(30)

        fake.script = stall
        tts = GnaniTimbreRealtimeTTS(settings)
        task = asyncio.create_task(pcm_of(tts))
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await eventually(lambda: fake.connections == 2)  # background reconnect, nobody asked
        assert await pcm_of(tts) == [b"\x01\x02", b"\x03\x04"]
        await tts.aclose()
    assert fake.connections == 2


async def test_error_frame_raises_provider_error_and_keeps_the_socket() -> None:
    async with fake_timbre() as (fake, settings):

        async def bad(ws: ServerConnection, n: int) -> None:
            if n > 1:
                return await fake.respond(ws, n)
            await ws.send(json.dumps({"type": "error", "message": "voice not found"}))

        fake.script = bad
        tts = GnaniTimbreRealtimeTTS(settings, backoff=(0, 0))
        with pytest.raises(ProviderError, match="voice not found") as info:
            await pcm_of(tts)
        assert not info.value.retryable
        assert await pcm_of(tts) == [b"\x01\x02", b"\x03\x04"]
        await tts.aclose()
    assert fake.connections == 1 and len(fake.requests) == 2


async def test_busy_account_error_is_retried_before_audio() -> None:
    async with fake_timbre() as (fake, settings):

        async def busy(ws: ServerConnection, n: int) -> None:
            if n > 1:
                return await fake.respond(ws, n)
            msg = "Only 1 active request is allowed at a time."
            await ws.send(json.dumps({"type": "error", "message": msg}))

        fake.script = busy
        tts = GnaniTimbreRealtimeTTS(settings, backoff=(0, 0))
        assert await pcm_of(tts) == [b"\x01\x02", b"\x03\x04"]
        await tts.aclose()
    assert len(fake.requests) == 2


async def test_connection_lost_before_audio_is_retried_on_a_new_socket() -> None:
    async with fake_timbre() as (fake, settings):

        async def drop(ws: ServerConnection, n: int) -> None:
            if n > 1:
                return await fake.respond(ws, n)
            await ws.close(1011)

        fake.script = drop
        tts = GnaniTimbreRealtimeTTS(settings, backoff=(0, 0))
        assert await pcm_of(tts) == [b"\x01\x02", b"\x03\x04"]
        await tts.aclose()
    assert fake.connections == 2


async def test_connection_lost_after_audio_is_not_retried() -> None:
    async with fake_timbre() as (fake, settings):

        async def drop_late(ws: ServerConnection, _: int) -> None:
            await ws.send(START)
            await ws.send(audio_frame(0, b"\x01\x02"))
            await ws.close(1011)

        fake.script = drop_late
        tts = GnaniTimbreRealtimeTTS(settings, backoff=(0, 0))
        with pytest.raises(ProviderError) as info:
            await pcm_of(tts)
        assert info.value.retryable
        await tts.aclose()
    assert len(fake.requests) == 1


async def test_unreachable_server_is_a_retryable_provider_error() -> None:
    tts = GnaniTimbreRealtimeTTS(settings_for("ws://127.0.0.1:1"), backoff=(0,))
    with pytest.raises(ProviderError) as info:
        await pcm_of(tts)
    assert info.value.retryable
    await tts.aclose()


async def test_concurrent_requests_queue_on_the_shared_account_slot() -> None:
    async with fake_timbre() as (fake, settings):

        async def slowish(ws: ServerConnection, _: int) -> None:
            await ws.send(START)
            await asyncio.sleep(0.05)
            await ws.send(audio_frame(0, b"\x01\x02"))
            await ws.send(COMPLETE)

        fake.script = slowish
        tts = GnaniTimbreRealtimeTTS(settings)
        results = await asyncio.gather(pcm_of(tts, "a"), pcm_of(tts, "b"))
        await tts.aclose()
    assert results == [[b"\x01\x02"], [b"\x01\x02"]]
    assert fake.max_active == 1 and fake.connections == 1


async def test_aclose_closes_the_socket_and_refuses_new_work() -> None:
    async with fake_timbre() as (fake, settings):
        tts = GnaniTimbreRealtimeTTS(settings)
        await tts.warm_up()
        await tts.aclose()
        await eventually(lambda: fake.closed == 1)
        with pytest.raises(ProviderError):
            await tts.warm_up()
    assert fake.connections == 1


def live_settings(**values: object) -> Settings:
    return Settings(
        _env_file=None,  # type: ignore[call-arg]
        gnani_api_key="g",  # type: ignore[arg-type]
        gnani_tts_voice="Nalini",
        sarvam_api_key="s",  # type: ignore[arg-type]
        **values,  # type: ignore[arg-type]
    )


def test_transport_setting_defaults_and_url() -> None:
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    assert settings.gnani_tts_ws_url == "wss://api.vachana.ai/api/v1/tts"
    assert settings.gnani_tts_transport in ("ws", "sse")


async def test_registry_builds_the_selected_transport_and_closes_it() -> None:
    sse = build_providers(live_settings(gnani_tts_transport="sse"))
    assert isinstance(sse.tts, GnaniTimbreTTS)
    await sse.aclose()

    async with fake_timbre() as (fake, base):
        ws = build_providers(
            live_settings(gnani_tts_transport="ws", gnani_tts_ws_url=base.gnani_tts_ws_url)
        )
        assert isinstance(ws.tts, GnaniTimbreRealtimeTTS)
        await ws.tts.warm_up()
        await ws.aclose()
        await eventually(lambda: fake.closed == 1)
