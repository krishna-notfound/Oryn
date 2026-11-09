# F07a — Sarvam LLM adapter

**Status:** IMPLEMENTED · **Branch:** `feat/F06-providers` · **Phase:** 2 · **Depends on:** F02 · **Part of:** F07 (engine is `feat/F07-engine`)

## Overview / problem
Stream replies from Sarvam's conversational model behind the `LanguageModel` port, with tool
calls delivered complete, so the engine can start TTS on the first sentence.

## Goals · Non-goals
- Goals: SSE streaming, immediate text deltas, tool-call assembly, correct `retryable` mapping.
- Non-goals: retries/fallbacks (the engine decides), reasoning traces, non-streaming mode.

## User stories
- As a user I hear a natural, colloquial reply begin almost immediately, in my language.

## Functional requirements
- `POST {SARVAM_BASE_URL}/v1/chat/completions`, header `api-subscription-key`.
- Model `LLM_MODEL` or default `sarvam-105b-conversations`. `stream: true`, `reasoning_effort: "none"`,
  `temperature`/`max_tokens` from settings, `tools` when given.
- Yield `LLMChunk(text=delta.content)` per delta; one final chunk with complete `ToolCall`s and `finish_reason`.
- Map `ChatMessage` (system/user/assistant, assistant with `tool_calls`, tool with `tool_call_id`).

## Non-functional requirements (latency, limits)
Pooled keep-alive client; timeout `LLM_TIMEOUT_S`; thinking disabled so the first token is not delayed.

## Design (classes, files, data flow)
`backend/app/providers/sarvam_llm.py`: `to_wire_message`, `to_wire_tool`, `_PendingToolCall`,
`SarvamLanguageModel`. Verified API facts: `docs/context/providers.md` ("Sarvam LLM - verified contract").

## Data / API / WebSocket changes
None.

## Security
Key only in the request header; prompts and replies are not logged.

## Failure and edge cases
| Situation | Result |
| --- | --- |
| 403 `invalid_api_key_error` (Sarvam's "401"), 400, 402 | non-retryable |
| 429 (rate / model capacity), 500, 502, 503 | retryable |
| 429 `seat_budget_exhausted_error` | non-retryable |
| connect/read error | retryable |
| malformed SSE JSON / invalid tool arguments JSON | non-retryable `ProviderError` |
| `reasoning_content` deltas | ignored (never spoken) |

## Acceptance criteria
Text chunks arrive before the stream ends; fragmented tool calls come out whole; errors map as above.

## Test cases
`backend/tests/test_sarvam_llm.py` (18 tests, `httpx.MockTransport`) and a live smoke test.

## Implementation notes (what actually got built, deviations)
- Default model lives in the adapter (`DEFAULT_MODEL`) because `Settings.llm_model` defaults to `""`
  and `config.py` was out of scope; an explicit `LLM_MODEL` always wins.
- Tools are sent when provided. UNVERIFIED: streamed tool-call delta shape and tool support on the
  conversations variant (details in `providers.md`); the accumulator accepts both fragmented and whole calls.
- The final chunk is emitted after `[DONE]` (one event after `finish_reason`), costing no audible latency.

## Verification evidence
`pytest -q`: 58 passed, 2 skipped (live). `ruff check .`, `ruff format --check .`, `mypy app`: clean.
Live call against Sarvam: NOT RUN (no keys in this environment).
