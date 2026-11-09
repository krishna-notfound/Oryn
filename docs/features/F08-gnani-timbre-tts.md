# F08 — Gnani Timbre TTS (backend part)

**Status:** IMPLEMENTED (backend adapter; browser playback lives on `feat/F01-web`) · **Branch:** `feat/F06-providers` · **Phase:** 2 · **Depends on:** F02

## Overview / problem
Stream the assistant's sentences as PCM from Gnani Timbre with minimum time-to-first-audio, behind
the `TextToSpeech` port, and stop immediately when the user barges in.

## Goals · Non-goals
- Goals: low-latency streaming synthesis, retries before first byte, cancellation-safe.
- Non-goals: voice cloning, SSML, mulaw/opus output, browser playback.

## User stories
- As a user I hear the reply start quickly and stop the moment I interrupt.

## Functional requirements
- `POST {GNANI_BASE_URL}/api/v1/tts/sse`, header `X-API-Key-ID`.
- Body: `text`, `model`, `voice`, `language`, `audio_config{sample_rate, num_channels:1, sample_width:2, encoding:"linear_pcm", container:"raw"}`.
- Decode base64 `audio` of each SSE event and yield PCM immediately; strip any RIFF/WAV header.
- `output_format = AudioFormat(sample_rate=GNANI_TTS_SAMPLE_RATE)`.

## Non-functional requirements (latency, limits)
One pooled keep-alive client (no TLS handshake per sentence). Connect timeout 5 s, read 15 s.

## Design (classes, files, data flow)
`backend/app/providers/gnani_timbre.py`: `strip_wav_header()`, `GnaniTimbreTTS` (`synthesize` = retry
loop around `_stream_once`). Shared SSE parsing and HTTP error mapping in `providers/http.py`.
**Transport: SSE, not WebSocket** (see `docs/context/providers.md`): independent requests on a warm
connection; cancelling just closes the response; the WS has no cancel message and its docs are
contradictory (headers vs JSON body).

## Data / API / WebSocket changes
None.

## Security
Key sent only as `X-API-Key-ID`; text and audio are not logged.

## Failure and edge cases
| Situation | Result |
| --- | --- |
| 429 / 5xx / connect or read error before first byte | retry after 0.5 / 1 / 2 s, then `ProviderError(retryable=True)` |
| 401 / 402 / 403 / 400 | `ProviderError(retryable=False)`, no retry |
| `error` SSE event | `ProviderError(retryable=False)` |
| failure after the first PCM chunk | raised, never retried (audio already playing) |
| consumer cancelled / generator closed | HTTP response closed via `contextlib.aclosing` |
| chunk with odd byte count | last byte carried into the next chunk (no split samples) |

## Acceptance criteria
PCM order preserved, headers stripped, retries only pre-first-byte, request body exactly as above.

## Test cases
`backend/tests/test_gnani_timbre.py` (14 tests, `httpx.MockTransport`) and a live smoke test.

## Implementation notes (what actually got built, deviations)
- `container: "raw"` is documented as headerless, but the docs do not say whether chunks may still
  carry RIFF; the defensive strip costs one `startswith`.
- Backoff is injectable (`backoff=`) so tests do not sleep.
- `GNANI_TTS_VOICE` has no default (voice is model-specific); live mode refuses to start without it.
- Not verified live: chunk granularity and time-to-first-audio.

## Verification evidence
`pytest -q`: 58 passed, 2 skipped (live). `ruff check .`, `ruff format --check .`, `mypy app`: clean.
Live call against the real Timbre: NOT RUN (no keys in this environment).
