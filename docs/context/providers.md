# Providers

Interfaces: `backend/app/providers/base.py`. Fakes: `backend/app/providers/fakes.py`.
Raw Gnani findings with citations: `providers-gnani-research.md` (read before touching adapters).

| Role       | Default adapter                       | File |
| ---------- | ------------------------------------- | ---- |
| STT        | **Gnani Prisma** streaming WS         | `providers/gnani_prisma.py` |
| TTS        | **Gnani Timbre** streaming SSE        | `providers/gnani_timbre.py` |
| LLM        | **Sarvam** chat completions (SSE)     | `providers/sarvam_llm.py` |
| Embeddings | OpenAI-compatible `/embeddings`       | `providers/openai_embeddings.py` |
| Test/dev   | Fakes (`PROVIDER_MODE=fake`)          | `providers/fakes.py` |

Built by `providers/registry.py` from `Settings` (`build_providers`; `ProviderSet.aclose()` at shutdown).
Shared SSE parsing and HTTP error mapping: `providers/http.py`.

## Gnani Prisma (STT) — verified contract

- `wss://api.vachana.ai/stt/v3/stream`; config in **upgrade headers**:
  `x-api-key-id`, `language_code`, `model` (`prisma-v3.0`), `x-sample-rate` (16000),
  `format` (`codemix` for Hinglish on v3.0).
- Send binary PCM s16le mono frames of **exactly 1024 bytes**, at real-time cadence.
- Server JSON: `connected`, `speech_start`, `processing`, `transcript`(final only: `text`,
  `segment_id`, `utterance_id`, `latency`…), `error`. Client may send `{"type":"commit"}`.
- No partial transcripts. 15-minute session cap; 60 s idle timeout.
- Languages: `hi-IN en-IN bn-IN gu-IN kn-IN ml-IN mr-IN pa-IN ta-IN te-IN`.

## Gnani Timbre (TTS) — verified contract

- `wss://api.vachana.ai/api/v1/tts` (also SSE `POST /api/v1/tts/sse`), header `X-API-Key-ID`.
- Body: `{text, model: "timbre-v2.5", voice, language, audio_config:{sample_rate: 24000,
  num_channels: 1, sample_width: 2, encoding: "linear_pcm", container: "raw"}}`.
- Response frames: `start` → `audio{chunk_index, audio(base64), is_final}` → `complete` / `error`.
- Always send `model` explicitly (v2.0 rejected).

## Errors and retries (both)

401/402/403 → not retryable (`ProviderError(retryable=False)`).
429, 5xx, connection reset → retry ×3 with 0.5/1/2 s backoff, but **never** retry once audio
for the current generation has started playing.

## Timbre transport choice (implemented)

SSE (`POST /api/v1/tts/sse`) over one long-lived pooled `httpx.AsyncClient` (keep-alive 60 s),
requesting `container: "raw"`, `encoding: "linear_pcm"`, mono, 16-bit at
`GNANI_TTS_SAMPLE_RATE`. Why not the WebSocket: it has no cancel message, so barge-in would
mean dropping the socket (new handshake) or draining stale audio; the docs recommend one
request at a time per socket and contradict themselves on headers-vs-body config. With SSE,
each sentence is an independent request on a warm connection and cancelling closes the response.
Defensive: a RIFF/WAV header on any chunk is stripped (the docs do not say whether `raw`
chunks are ever headed); odd-length chunks are re-aligned to 16-bit samples. Retries (0.5/1/2 s)
happen only before the first audio byte of a request. In-stream `error` events are not retried.

## Sarvam LLM — verified contract

Verified 2026-11-09 from the official docs (raw markdown, append `.md` to any page URL).

- **Endpoint**: `POST https://api.sarvam.ai/v1/chat/completions` (`SARVAM_BASE_URL` + path).
  `/v1` serves `sarvam-105b` and `sarvam-105b-conversations`; `/v2` is the OpenAI-compatible
  one but does **not** serve the conversations variant.
  https://docs.sarvam.ai/api-reference/chat/chat-completions-v1.md ,
  https://docs.sarvam.ai/api/token-factory/chat-completions.md
- **Auth**: header `api-subscription-key: <key>` (also accepts `Authorization: Bearer <key>`).
- **Model**: `sarvam-105b-conversations` (32K context, "post-trained for real-time dialogue and
  voice-agent workloads", same price as 105B) is the adapter default; `sarvam-105b` (128K, flagship
  reasoning/agentic) via `LLM_MODEL`. `sarvam-m` is deprecated and gone.
  https://docs.sarvam.ai/api/token-factory/models/sarvam-105b.md
- **Request**: `messages`, `model`, `stream`, `temperature` (default 0.2), `max_tokens`
  (default 2048), `tools` (`{"type":"function","function":{name,description,parameters}}`),
  `tool_choice`, `reasoning_effort` (`none|low|medium|high|max`), `n` must be 1.
  **Thinking is on by default**; it streams `delta.reasoning_content` first and can consume a
  small `max_tokens` entirely (`finish_reason: "length"`, empty content). The adapter sends
  `reasoning_effort: "none"` and never speaks `reasoning_content`.
- **Streaming**: `stream: true` -> SSE, each event `data: {chat.completion.chunk}` with
  `choices[0].delta.content`; last data chunk has `usage` and empty `choices`; terminated by
  `data: [DONE]`. `finish_reason`: `stop | length | tool_calls | content_filter`.
- **Tools**: function calling documented (`finish_reason: "tool_calls"`, `message.tool_calls[]`
  with `id` like `call-<uuid>`, stringified-JSON `arguments`; reply with
  `{"role":"tool","tool_call_id","content"}` after the assistant message echoing `tool_calls`).
  `parallel_tool_calls: false` is available.
- **Errors** (`{"error":{"message","code","request_id"}}`): bad key is **403**
  `invalid_api_key_error` (not 401); 400/422 bad request; 402 `insufficient_quota_error`;
  429 `rate_limit_exceeded_error` / `model_rate_limit_exceeded_error` (retry; honour
  `Retry-After`) / `seat_budget_exhausted_error` (do **not** retry); 500/502/503 retry.
  Adapter: retryable = 429/500/502/503/504 except `seat_budget_exhausted_error`.
  https://docs.sarvam.ai/api/getting-started/errors-troubleshooting.md

UNVERIFIED (confirm with a live call, `pytest -m live`):
- The exact shape of **streamed** tool-call deltas (docs show only non-streaming tool calls).
  The adapter assumes the OpenAI shape (`delta.tool_calls[{index,id,function:{name,arguments}}]`,
  arguments in fragments) and also copes with a whole call in one delta.
- Whether `sarvam-105b-conversations` supports tool calling at all (the docs' tool section is
  written for `sarvam-105b`). If not, set `LLM_MODEL=sarvam-105b` for tool turns.
- Whether `reasoning_effort: "none"` (enum in the API reference) vs `null` (cURL note) is the
  accepted way to disable thinking; the adapter uses the documented enum value `"none"`.
- Time-to-first-token and rate-limit numbers (not published).

## Embeddings

`OpenAICompatibleEmbedder`: `POST {EMBEDDINGS_BASE_URL}/embeddings`
`{model, input: [...]}`, `Authorization: Bearer` when a key is set. Built only when both
`EMBEDDINGS_BASE_URL` and `EMBEDDINGS_MODEL` are set (otherwise `ProviderSet.embedder is None`).
`dimensions` is `0` until the first response, then the vector length.

## Language exposure

The UI only offers languages in **both** Prisma and Timbre lists (currently the ten above).
