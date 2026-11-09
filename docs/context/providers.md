# Providers

Interfaces: `backend/app/providers/base.py`. Fakes: `backend/app/providers/fakes.py`.
Raw Gnani findings with citations: `providers-gnani-research.md` (read before touching adapters).

| Role       | Default adapter                       | File |
| ---------- | ------------------------------------- | ---- |
| STT        | **Gnani Prisma** streaming WS         | `providers/gnani_prisma.py` |
| TTS        | **Gnani Timbre** streaming WS         | `providers/gnani_timbre.py` |
| LLM        | OpenAI-compatible chat completions    | `providers/openai_compatible.py` |
| Embeddings | OpenAI-compatible `/embeddings`       | `providers/openai_compatible.py` |
| Test/dev   | Fakes (`PROVIDER_MODE=fake`)          | `providers/fakes.py` |

Built by `providers/registry.py` from `Settings`.

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

## LLM

No Gnani-hosted LLM API exists (Evon is open-weights only). One adapter class speaks the
OpenAI chat-completions streaming format; vendor is chosen by env:
`LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL`. Candidates to benchmark for TTFT and Hinglish
quality: Sarvam chat, Groq/Llama, OpenAI, self-hosted Evon (vLLM). Model names never appear
outside config.

## Language exposure

The UI only offers languages in **both** Prisma and Timbre lists (currently the ten above).
