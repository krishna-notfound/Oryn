import json
from typing import Any

import httpx
import pytest

from app.config import Settings
from app.providers.base import ChatMessage, LLMChunk, ProviderError, ToolCall, ToolSpec
from app.providers.sarvam_llm import SarvamLanguageModel, to_wire_message

SETTINGS = Settings(
    _env_file=None,  # type: ignore[call-arg]
    sarvam_api_key="sk_test",  # type: ignore[arg-type]
)


def sse_body(*chunks: dict[str, Any] | str) -> httpx.Response:
    lines = [c if isinstance(c, str) else json.dumps(c) for c in chunks]
    body = "".join(f"data: {line}\n\n" for line in lines)
    return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=body)


def delta(**fields: Any) -> dict[str, Any]:
    return {"choices": [{"index": 0, "delta": fields, "finish_reason": None}]}


def finish(reason: str) -> dict[str, Any]:
    return {"choices": [{"index": 0, "delta": {}, "finish_reason": reason}]}


def make_llm(handler: Any) -> SarvamLanguageModel:
    return SarvamLanguageModel(SETTINGS, httpx.AsyncClient(transport=httpx.MockTransport(handler)))


async def run(llm: SarvamLanguageModel, **kwargs: Any) -> list[LLMChunk]:
    return [c async for c in llm.stream([ChatMessage("user", "hi")], **kwargs)]


async def test_streams_text_deltas_then_finish_reason() -> None:
    llm = make_llm(
        lambda _: sse_body(
            delta(role="assistant", content="Na"),
            delta(content="maste"),
            finish("stop"),
            {"choices": [], "usage": {"total_tokens": 3}},
            "[DONE]",
        )
    )
    assert await run(llm) == [
        LLMChunk(text="Na"),
        LLMChunk(text="maste"),
        LLMChunk(finish_reason="stop"),
    ]


async def test_reasoning_deltas_are_never_yielded() -> None:
    llm = make_llm(lambda _: sse_body(delta(reasoning="thinking"), delta(content="ok"), "[DONE]"))
    assert await run(llm) == [LLMChunk(text="ok")]


async def test_text_is_yielded_before_the_stream_ends() -> None:
    class Body(httpx.AsyncByteStream):
        async def __aiter__(self):  # type: ignore[no-untyped-def]
            yield f"data: {json.dumps(delta(content='first'))}\n\n".encode()
            raise AssertionError("consumer should have received 'first' before this is read")

    llm = make_llm(lambda _: httpx.Response(200, stream=Body()))
    stream = llm.stream([ChatMessage("user", "hi")])
    assert await anext(stream) == LLMChunk(text="first")
    await stream.aclose()


async def test_live_tool_call_shape_with_null_fields() -> None:
    """Captured from sarvam-105b-conversations: absent fields are explicit nulls."""
    llm = make_llm(
        lambda _: sse_body(
            delta(
                tool_calls=[
                    {
                        "index": 0,
                        "id": "chatcmpl-tool-9d72",
                        "type": "function",
                        "function": {"name": "get_weather", "arguments": None},
                    }
                ]
            ),
            delta(
                tool_calls=[
                    {
                        "index": 0,
                        "id": None,
                        "function": {"name": None, "arguments": '{"city": "Mum'},
                    }
                ]
            ),
            delta(
                tool_calls=[
                    {"index": 0, "id": None, "function": {"name": None, "arguments": 'bai"}'}}
                ]
            ),
            finish("tool_calls"),
            "[DONE]",
        )
    )
    assert await run(llm) == [
        LLMChunk(
            tool_calls=(ToolCall("chatcmpl-tool-9d72", "get_weather", {"city": "Mumbai"}),),
            finish_reason="tool_calls",
        )
    ]


async def test_fragmented_tool_calls_arrive_complete_in_one_chunk() -> None:
    def fragment(index: int, **function: str) -> dict[str, Any]:
        call: dict[str, Any] = {"index": index, "function": function}
        if "name" in function:
            call["id"] = f"call-{index}"
        return delta(tool_calls=[call])

    llm = make_llm(
        lambda _: sse_body(
            fragment(0, name="remember", arguments='{"fa'),
            fragment(0, arguments='ct": "likes tea"}'),
            fragment(1, name="get_time", arguments=""),
            finish("tool_calls"),
            "[DONE]",
        )
    )
    assert await run(llm) == [
        LLMChunk(
            tool_calls=(
                ToolCall("call-0", "remember", {"fact": "likes tea"}),
                ToolCall("call-1", "get_time", {}),
            ),
            finish_reason="tool_calls",
        )
    ]


async def test_invalid_tool_arguments_raise_provider_error() -> None:
    llm = make_llm(
        lambda _: sse_body(
            delta(
                tool_calls=[{"index": 0, "id": "c", "function": {"name": "f", "arguments": "{"}}]
            ),
            finish("tool_calls"),
        )
    )
    with pytest.raises(ProviderError, match="invalid arguments"):
        await run(llm)


async def test_request_uses_v1_endpoint_key_header_and_conversational_model() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return sse_body("[DONE]")

    tool = ToolSpec("get_time", "Current time", {"type": "object", "properties": {}})
    await run(make_llm(handler), tools=[tool])

    request = seen[0]
    assert str(request.url) == "https://api.sarvam.ai/v1/chat/completions"
    assert request.headers["api-subscription-key"] == "sk_test"
    body = json.loads(request.content)
    assert body["model"] == "sarvam-105b-conversations"
    assert body["stream"] is True
    assert "reasoning_effort" not in body  # the API rejects "none" for this model
    assert body["messages"] == [{"role": "user", "content": "hi"}]
    assert body["tools"] == [
        {
            "type": "function",
            "function": {
                "name": "get_time",
                "description": "Current time",
                "parameters": {"type": "object", "properties": {}},
            },
        }
    ]


async def test_configured_model_overrides_the_default() -> None:
    seen: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return sse_body("[DONE]")

    settings = SETTINGS.model_copy(update={"llm_model": "sarvam-105b"})
    llm = SarvamLanguageModel(settings, httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    await run(llm)
    assert seen[0]["model"] == "sarvam-105b" and "tools" not in seen[0]


def test_maps_tool_round_trip_messages_to_the_wire_format() -> None:
    call = ToolCall("call-1", "remember", {"fact": "tea"})
    assistant = to_wire_message(ChatMessage("assistant", "", tool_calls=(call,)))
    tool = to_wire_message(ChatMessage("tool", '{"ok": true}', tool_call_id="call-1"))

    assert assistant == {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": "call-1",
                "type": "function",
                "function": {"name": "remember", "arguments": '{"fact": "tea"}'},
            }
        ],
    }
    assert tool == {"role": "tool", "content": '{"ok": true}', "tool_call_id": "call-1"}


@pytest.mark.parametrize(
    ("status", "code", "retryable"),
    [
        (403, "invalid_api_key_error", False),  # Sarvam reports bad keys as 403, not 401
        (401, "authentication_error", False),
        (402, "insufficient_quota_error", False),
        (400, "invalid_request_error", False),
        (429, "rate_limit_exceeded_error", True),
        (429, "model_rate_limit_exceeded_error", True),
        (429, "seat_budget_exhausted_error", False),
        (500, "internal_server_error", True),
        (503, "model_overloaded", True),
    ],
)
async def test_http_errors_map_to_provider_error(status: int, code: str, retryable: bool) -> None:
    body = {"error": {"message": "nope", "code": code}}
    llm = make_llm(lambda _: httpx.Response(status, json=body))
    with pytest.raises(ProviderError) as info:
        await run(llm)
    assert info.value.retryable is retryable and code in str(info.value)


async def test_transport_failure_is_retryable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    with pytest.raises(ProviderError) as info:
        await run(make_llm(handler))
    assert info.value.retryable is True
