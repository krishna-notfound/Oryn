"""Sarvam conversational LLM: streaming chat completions over a pooled HTTP client.

Contract and sources: `docs/context/providers.md` ("Sarvam LLM - verified contract").
`POST {base}/v1/chat/completions`, auth via the `api-subscription-key` header, SSE stream of
`chat.completion.chunk` objects terminated by `data: [DONE]`.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from typing import Any

import httpx

from app.config import Settings
from app.providers.base import (
    ChatMessage,
    LanguageModel,
    LLMChunk,
    ProviderError,
    ToolCall,
    ToolSpec,
)
from app.providers.http import error_from_response, iter_sse

NAME = "sarvam"
# Sarvam's variant post-trained for real-time dialogue and voice agents (32K context).
DEFAULT_MODEL = "sarvam-105b-conversations"


@dataclass(slots=True)
class _PendingToolCall:
    """Tool call assembled from streamed fragments (id/name first, arguments in pieces)."""

    id: str = ""
    name: str = ""
    arguments: list[str] = field(default_factory=list)

    def build(self) -> ToolCall:
        try:
            args = json.loads("".join(self.arguments) or "{}")
        except json.JSONDecodeError as exc:
            raise ProviderError(NAME, f"tool call {self.name!r} has invalid arguments") from exc
        return ToolCall(id=self.id, name=self.name, arguments=args)


def to_wire_message(message: ChatMessage) -> dict[str, Any]:
    wire: dict[str, Any] = {"role": message.role, "content": message.content}
    if message.tool_calls:
        wire["content"] = message.content or None
        wire["tool_calls"] = [
            {
                "id": call.id,
                "type": "function",
                "function": {"name": call.name, "arguments": json.dumps(call.arguments)},
            }
            for call in message.tool_calls
        ]
    if message.tool_call_id is not None:
        wire["tool_call_id"] = message.tool_call_id
    return wire


def to_wire_tool(tool: ToolSpec) -> dict[str, Any]:
    function = {"name": tool.name, "description": tool.description, "parameters": tool.parameters}
    return {"type": "function", "function": function}


class SarvamLanguageModel(LanguageModel):
    name = NAME

    def __init__(self, settings: Settings, client: httpx.AsyncClient) -> None:
        if settings.sarvam_api_key is None:
            raise ValueError("SARVAM_API_KEY is required for the Sarvam LLM")
        self._settings = settings
        self._client = client
        self._url = f"{settings.sarvam_base_url.rstrip('/')}/v1/chat/completions"
        self._headers = {"api-subscription-key": settings.sarvam_api_key.get_secret_value()}
        self.model = settings.llm_model or DEFAULT_MODEL

    async def stream(
        self, messages: Sequence[ChatMessage], tools: Sequence[ToolSpec] = ()
    ) -> AsyncIterator[LLMChunk]:
        body = self._request_body(messages, tools)
        pending: dict[int, _PendingToolCall] = {}
        finish_reason: str | None = None
        try:
            async with self._client.stream(
                "POST", self._url, json=body, headers=self._headers
            ) as response:
                if response.status_code != 200:
                    raise await error_from_response(NAME, response)
                async for _, data in iter_sse(response):
                    if data == "[DONE]":
                        break
                    for choice in json.loads(data).get("choices", ()):  # usage chunk: no choices
                        delta = choice.get("delta") or {}
                        if text := delta.get("content"):  # `reasoning_content` is never spoken
                            yield LLMChunk(text=text)
                        self._collect_tool_calls(pending, delta.get("tool_calls") or ())
                        finish_reason = choice.get("finish_reason") or finish_reason
        except httpx.TransportError as exc:
            raise ProviderError(NAME, f"transport error: {exc!r}", retryable=True) from exc
        except json.JSONDecodeError as exc:
            raise ProviderError(NAME, f"malformed SSE payload: {exc}") from exc
        calls = tuple(pending[i].build() for i in sorted(pending))
        if calls or finish_reason:
            yield LLMChunk(tool_calls=calls, finish_reason=finish_reason)

    def _request_body(
        self, messages: Sequence[ChatMessage], tools: Sequence[ToolSpec]
    ) -> dict[str, Any]:
        s = self._settings
        body: dict[str, Any] = {
            "model": self.model,
            "messages": [to_wire_message(m) for m in messages],
            "stream": True,
            "temperature": s.llm_temperature,
            "max_tokens": s.llm_max_tokens,
            # Thinking is on by default and would delay the first spoken token (and can eat
            # a small max_tokens budget entirely).
            "reasoning_effort": "none",
        }
        if tools:
            body["tools"] = [to_wire_tool(t) for t in tools]
        return body

    @staticmethod
    def _collect_tool_calls(
        pending: dict[int, _PendingToolCall], fragments: Sequence[dict[str, Any]]
    ) -> None:
        for fragment in fragments:
            call = pending.setdefault(fragment.get("index", 0), _PendingToolCall())
            call.id = fragment.get("id") or call.id
            function = fragment.get("function") or {}
            call.name = function.get("name") or call.name
            if arguments := function.get("arguments"):
                call.arguments.append(arguments)
