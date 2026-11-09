import asyncio
import base64
import json

import httpx
import pytest

from app.config import Settings
from app.providers.base import ProviderError
from app.providers.gnani_timbre import GnaniTimbreTTS, strip_wav_header

SETTINGS = Settings(
    _env_file=None,  # type: ignore[call-arg]
    gnani_api_key="test-key",  # type: ignore[arg-type]
    gnani_tts_voice="Nalini",
)
WAV_HEADER = b"RIFF\xff\xff\xff\xffWAVEfmt " + bytes(20) + b"data\xff\xff\xff\xff"


def sse(*events: tuple[str, dict[str, object]]) -> httpx.Response:
    body = "".join(f"event: {name}\ndata: {json.dumps(data)}\n\n" for name, data in events)
    return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=body)


def chunk(index: int, pcm: bytes) -> tuple[str, dict[str, object]]:
    audio = base64.b64encode(pcm).decode()
    return "chunk", {"chunk_index": index, "audio": audio, "is_final": False}


START = ("start", {"status": "streaming_started", "text": "hi"})
COMPLETE = ("complete", {"chunk_index": 9, "audio": "", "is_final": True})


def make_tts(handler: httpx.MockTransport | None = None, **kwargs: object) -> GnaniTimbreTTS:
    client = httpx.AsyncClient(transport=handler)
    return GnaniTimbreTTS(SETTINGS, client, backoff=(0, 0, 0), **kwargs)  # type: ignore[arg-type]


async def pcm_of(tts: GnaniTimbreTTS, text: str = "namaste") -> list[bytes]:
    return [c async for c in tts.synthesize(text, language="hi-IN")]


def test_strip_wav_header_only_when_riff_present() -> None:
    assert strip_wav_header(WAV_HEADER + b"\x01\x02") == b"\x01\x02"
    assert strip_wav_header(b"\x01\x02\x03\x04") == b"\x01\x02\x03\x04"


async def test_yields_pcm_in_order_stripping_a_wav_header() -> None:
    transport = httpx.MockTransport(
        lambda _: sse(START, chunk(0, WAV_HEADER + b"\x01\x02"), chunk(1, b"\x03\x04"), COMPLETE)
    )
    assert await pcm_of(make_tts(transport)) == [b"\x01\x02", b"\x03\x04"]


async def test_never_splits_a_16_bit_sample_across_chunks() -> None:
    transport = httpx.MockTransport(lambda _: sse(chunk(0, b"\x01\x02\x03"), chunk(1, b"\x04")))
    assert await pcm_of(make_tts(transport)) == [b"\x01\x02", b"\x03\x04"]


async def test_request_targets_sse_endpoint_with_raw_pcm_config() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return sse(chunk(0, b"\x00\x00"))

    tts = make_tts(httpx.MockTransport(handler))
    await pcm_of(tts)

    request = seen[0]
    assert str(request.url) == "https://api.vachana.ai/api/v1/tts/sse"
    assert request.headers["x-api-key-id"] == "test-key"
    assert json.loads(request.content) == {
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
    assert tts.output_format.sample_rate == 24_000


async def test_explicit_voice_overrides_the_configured_one() -> None:
    seen: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return sse(chunk(0, b"\x00\x00"))

    tts = make_tts(httpx.MockTransport(handler))
    _ = [c async for c in tts.synthesize("hi", language="en-IN", voice="Devika")]
    assert seen[0]["voice"] == "Devika"


async def test_retries_503_before_the_first_byte() -> None:
    statuses = iter([503, 429])

    def handler(_: httpx.Request) -> httpx.Response:
        status = next(statuses, 200)
        return httpx.Response(status) if status != 200 else sse(chunk(0, b"\x01\x02"))

    assert await pcm_of(make_tts(httpx.MockTransport(handler))) == [b"\x01\x02"]


async def test_retries_connect_errors_before_the_first_byte() -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise httpx.ConnectError("refused", request=request)
        return sse(chunk(0, b"\x01\x02"))

    assert await pcm_of(make_tts(httpx.MockTransport(handler))) == [b"\x01\x02"]
    assert attempts == 2


async def test_gives_up_after_three_retries() -> None:
    attempts = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(500)

    with pytest.raises(ProviderError) as info:
        await pcm_of(make_tts(httpx.MockTransport(handler)))
    assert attempts == 4 and info.value.retryable is True


@pytest.mark.parametrize("status", [401, 402, 403])
async def test_auth_errors_are_not_retried(status: int) -> None:
    attempts = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        body = {"detail": {"error_code": "INVALID_API_KEY", "message": "bad key"}}
        return httpx.Response(status, json=body)

    with pytest.raises(ProviderError) as info:
        await pcm_of(make_tts(httpx.MockTransport(handler)))
    assert attempts == 1 and info.value.retryable is False


async def test_error_after_the_first_byte_is_not_retried() -> None:
    attempts = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return sse(chunk(0, b"\x01\x02"), ("error", {"type": "server", "message": "gone"}))

    received: list[bytes] = []
    tts = make_tts(httpx.MockTransport(handler))
    with pytest.raises(ProviderError, match="gone"):
        async for pcm in tts.synthesize("hi", language="en-IN"):
            received.append(pcm)
    assert received == [b"\x01\x02"] and attempts == 1


async def test_error_event_before_audio_is_a_provider_error() -> None:
    transport = httpx.MockTransport(lambda _: sse(START, ("error", {"message": "bad text"})))
    with pytest.raises(ProviderError, match="bad text"):
        await pcm_of(make_tts(transport))


async def test_closing_the_consumer_closes_the_response() -> None:
    closed = False

    class Body(httpx.AsyncByteStream):
        async def __aiter__(self):  # type: ignore[no-untyped-def]
            audio = base64.b64encode(b"ab").decode()
            yield f"event: chunk\ndata: {json.dumps({'audio': audio})}\n\n".encode()
            yield b"event: chunk\ndata: {}\n\n"

        async def aclose(self) -> None:
            nonlocal closed
            closed = True

    transport = httpx.MockTransport(lambda _: httpx.Response(200, stream=Body()))
    stream = make_tts(transport).synthesize("hi", language="en-IN")
    assert await anext(stream) == b"ab"
    await stream.aclose()  # what cancelling the consumer triggers
    assert closed


async def test_second_synthesis_waits_for_the_first_to_finish() -> None:
    """The account 429s simultaneous requests, so they queue behind one another."""
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return sse(START, chunk(0, b"\x01\x02"), chunk(1, b"\x03\x04"), COMPLETE)

    tts = make_tts(httpx.MockTransport(handler))
    first = tts.synthesize("one", language="en-IN")
    assert await anext(first) == b"\x01\x02"  # the first request now holds the slot

    second = asyncio.create_task(pcm_of(tts, "two"))
    await asyncio.sleep(0.05)
    assert len(requests) == 1 and not second.done()

    assert [c async for c in first] == [b"\x03\x04"]
    assert await second == [b"\x01\x02", b"\x03\x04"]
    assert len(requests) == 2
