# Gnani provider research: Prisma STT, Timbre TTS, LLM

Researched 2026-11-09 from official docs (docs.gnani.ai, fetched as raw markdown and via WebFetch), PyPI, and vendor pages. WebFetch output for some pages was summarised by a small model; where the raw markdown was downloaded and read directly this is marked [RAW].

Key finding: the live API host is **https://api.vachana.ai** (Gnani's "Vachana" speech API gateway). The older hosts shown on gnani.ai/dev pages (api.gnani.ai/prisma/v3/..., api.gnani.ai/timbre/v2/synthesize) were NOT confirmed by the official docs and are treated as legacy/unverified.

---------------------------------------------------------------------------

## VERIFIED

### 0. Common
- Base URL: `https://api.vachana.ai` (all paths relative). Source: https://docs.gnani.ai/api/introduction/introduction.md
- Auth: single header `X-API-Key-ID: <API_KEY>` on every request (REST, SSE, WebSocket upgrade). Same key for STT, TTS, VC. No org id / user id needed (SDK >= 0.7). Sources: https://docs.gnani.ai/api/introduction/quick-start.md , https://docs.gnani.ai/python-sdk/introduction.md [RAW]
- Keys are generated on the Gnani API platform: https://app.gnani.ai/voice (python-sdk/introduction.md [RAW]).
- Optional `X-API-Request-ID` correlation header on STT (echoed as `request_id`). https://docs.gnani.ai/api/STT/speech-to-text.md
- Latency claim: "P95 ~200 ms for STT"; no TTS latency figure. https://docs.gnani.ai/api/introduction/introduction.md
- Docs index: https://docs.gnani.ai/llms.txt ; OpenAPI/AsyncAPI specs listed there (/STT.yml, /TTS.yml, /stt-websocket.yml, /tts-websocket.yml). The .yml URLs returned 404 or an HTML shell via fetch; not parsed.

### 1. Prisma STT

Models: `prisma-v2.5` (default when `model` omitted) and `prisma-v3.0` (opt-in, released Oct 2026). Same endpoints for both; `/stt/v3` in the path is a path version, not the model version. https://docs.gnani.ai/api/STT/models/overview.md , https://docs.gnani.ai/api/introduction/changelog.md (entry dated 22 Oct 2026)

| Model | Notes (models/overview.md) |
|---|---|
| prisma-v2.5 | One language per request, per-language models; ITN number formatting, word boosting (`bias_list`), find/replace. Best for monolingual audio. |
| prisma-v3.0 | `indic-auto` language detection, mixed-language (code-mix) speech, context hints (`keyterms`, `topic`, `previous_text`, `speaker_names`), romanised output. |

#### 1a. REST (clips <= 30 s)
Source: https://docs.gnani.ai/api/STT/speech-to-text.md , limits: https://docs.gnani.ai/api/STT/limits.md
- `POST https://api.vachana.ai/stt/v3`, `multipart/form-data`
- Headers: `X-API-Key-ID` (required), `X-API-Request-ID` (optional)
- Fields: `audio_file` (required; WAV/MP3/OGG/FLAC/AAC/M4A; <= 30 s; min 0.1 s), `language_code` (required; optional only for `format=transliterate` on v3.0), `model`, `format` (`verbatim` default | `transcribe` | v3.0 only: `codemix`, `transliterate`), `enable_substitution`, `substitution_map` (<= 10 rules), v3.0: `keyterms` (<= 200), `topic` (<= 1600 chars), `previous_text` (last 600 chars kept), `speaker_names` (1-4); v2.5: `bias_list` (<= 100 words) + `bias_score` (0.0/0.5/1.0/1.5/2.0).
- 200 response: `{success, request_id, transcript, model, detected_language_code, processing_time, end_to_end_latency}`; `truncated:true` only if cut; v2.5 adds `output.literal`. Empty transcript is a valid success.
- Errors: `{"success":false,"error":{"type","message"}}`; 422 `{"detail":[...]}` for missing fields; auth/rate errors use `{"detail":{"error_code",...}}`.

#### 1b. Realtime WebSocket (the one relevant to live voice) [RAW: https://docs.gnani.ai/api/STT/stt-websocket.md]
- URL: `wss://api.vachana.ai/stt/v3/stream`
- ALL config goes in **WebSocket upgrade headers** (not query string, not first message). Browser `WebSocket` cannot set custom headers; the doc says to proxy via your own backend (this also keeps the API key off the client).
- Headers: `x-api-key-id` (required), `language_code` (required; e.g. `hi-IN`, `en-IN`, `indic-auto`, or v3.0 list `hi-IN,en-IN` up to 4), `model` (`prisma-v3.0`|`prisma-v2.5`; omitted = v2.5; invalid value silently ignored), `x-sample-rate` (8000/16000/44100/48000; default 16000), `format` (`verbatim` default, `transcribe`, v3.0: `codemix`, `transliterate`), `x-idle-timeout-sec` (default 60, 5-900), `x-api-request-id`, `itn_native_numerals` (v2.5), `enable_substitution`, `substitution_map`, `bias_list`, `bias_score` (v2.5), `keyterms`/`topic`/`previous_text`/`speaker_names` (v3.0). Older spellings `lang_code`, `x-model`, `x-format`, `x-keyterms` still accepted; new name wins if both sent.
- VAD headers (fixed per connection): `x-vad-threshold` (0.7, 0-1), `x-min-silence-ms` (500, 100-2000), `x-min-speech-ms` (100; 200 at 8k; 50-2000), `x-min-segment-ms` (120), `x-max-speech-s` (disabled; 5-120). (Defaults from the WebFetch summary of the same page; not re-verified in the RAW table.)
- Audio: raw PCM binary frames, signed 16-bit little-endian, mono, **exactly 1,024 bytes per frame (512 samples)** = 32 ms @16 kHz, 64 ms @8 kHz. Must be sent at real-time cadence (no burst; degrades VAD). 44.1/48 kHz input is resampled server-side but still 1,024-byte frames. No container (WAV/MP3) mid-stream.
- Flow: connect -> server `connected` -> client streams frames -> server `speech_start` / `processing` (VAD end-of-speech) -> server `transcript`.
- Server messages (JSON text frames, all have `type` + ISO-8601 `timestamp`):
  - `connected`: `message`, `config.sample_rate`, `config.chunk_size` (512); v3.0 adds `request_id`, `config.language_code`, `config.format`, `config.language_candidates`.
  - `speech_start` (v3.0 also `utterance_id`).
  - `processing`: end of speech detected, transcription started.
  - `transcript`: `text`, `audio_duration_ms`, `segment_id` (`<request_id>-seg-<N>`), `segment_index` (1-based), `latency` (ms to transcribe the segment), `utterance_id`, `is_final` (**always true**), v3.0 `detected_language_code`, v2.5 `output.literal`.
  - `settings_updated`, `error` (`message`, `timestamp`).
- **No interim/partial transcripts.** The only transcript event is final per VAD-detected segment. https://docs.gnani.ai/api/STT/models/prisma-v3.md states "Realtime streaming does not offer token-by-token streaming." The last utterance is only emitted after silence (or a `commit`).
- Client control frames (JSON text): `{"type":"commit"}` (close open utterance now); `{"type":"update_settings", language_code?, format?, itn_native_numerals?, bias:{list,score}?, substitution:{enabled,map}?}` -> `settings_updated`. VAD settings cannot be changed mid-session.
- Close/limits: session max 15 min (then `error` + close 1001); idle default 60 s; bad handshake header -> `error` then close 1008; bad `substitution_map` -> close 1008 reason `Invalid x-substitution-map`; missing/invalid key -> HTTP 401 on upgrade; per-org concurrent-session cap -> rejected with `error` naming the limit.
- The doc's own Python example sleeps 0.1 s between chunks, which contradicts the 32 ms cadence rule; follow the stated rule.

#### 1c. Batch STT
Endpoints documented under https://docs.gnani.ai/api/STTBatch/Introduction.md (create/start/get/list/files/cancel job). Limits: 4 h per file, 100 files per job, 10 MB per upload (5 GiB via presigned). Not studied in detail; not needed for live voice.

#### 1d. Languages (STT) https://docs.gnani.ai/api/STT/languages.md
bn-IN, en-IN, gu-IN, hi-IN, kn-IN, ml-IN, mr-IN, pa-IN, ta-IN, te-IN. Both models share the list. English entry: "Accepts English-Hindi mixed audio and generates output in English (Latin script)". `indic-auto` is v3.0-only. Code-mix handling: v3.0 `format=codemix` keeps English words in Latin script inside native-script text (example: "मेरा account नंबर एक दो तीन चार पाँच है"); `format=transliterate` returns fully romanised text (Hinglish-style, "to theek hai"); docs warn romanisation accuracy has not been measured. https://docs.gnani.ai/api/STT/models/prisma-v3.md

#### 1e. Limits / errors / retry
- Rate limits: numbers not published ("will be listed" in account at app.gnani.ai). 429 `RATE_LIMITED`; no `Retry-After` header. https://docs.gnani.ai/api/STT/limits.md
- Auth errors (all services): 401 `MISSING_API_KEY`, 401 `INVALID_API_KEY`, 402 insufficient credit, 403 `SCOPE_NOT_ALLOWED`, 429 `RATE_LIMITED`; body `{"detail":{"error_code","message","status_code"}}`. https://docs.gnani.ai/api/introduction/errors.md
- Retry guidance (errors.md): retry connection errors and 429/500/503/504 with exponential backoff (e.g. 0.5, 1, 2 s; 3 attempts); do not retry other 4xx; honour `Retry-After` if it ever appears.
- Some accounts allow only ONE active request at a time (429 while a request runs) - TTS pages.

### 2. Timbre TTS

Models: `timbre-v2.5` (recommended; 42 voices) and `timbre-v3.0` (13 voices, all 10 languages, inline audio tags). `timbre-v2.0` is deprecated: the SDK default is still v2.0 but the API rejects it (400, "supported models are timbre-v2.5"), so always pass `model` explicitly. https://docs.gnani.ai/python-sdk/text-to-speech.md [RAW], https://docs.gnani.ai/api/introduction/introduction.md

Three transports, same request body: REST `POST https://api.vachana.ai/api/v1/tts/inference`, SSE `POST https://api.vachana.ai/api/v1/tts/sse`, WebSocket `wss://api.vachana.ai/api/v1/tts`. Sources: https://docs.gnani.ai/api/TTS/tts-inference.md , https://docs.gnani.ai/api/TTS/tts-sse.md , https://docs.gnani.ai/api/TTS/tts-websocket.md

#### 2a. Request body (JSON)
| Field | Notes |
|---|---|
| `text` | required. Write numbers/dates/currency as spoken words. v3.0 supports inline tags `<coughs> <excited> <exhales> <giggle> <laugh> <pauses> <shouting> <sigh> <slow> <sniffles> <whispers> <yawns> <clears throat>`; v2.5 strips tags; tag-only text -> 400. No documented max length (overlong -> 500). |
| `model` | required: `timbre-v2.5` or `timbre-v3.0` |
| `voice` | required in prose; must belong to the chosen model's catalog |
| `language` | `auto`, `hi-IN`, `en-IN`, `hi-en` (Hinglish), `ta-IN`, `te-IN`, `kn-IN`, `ml-IN`, `mr-IN`, `pa-IN`, `bn-IN`, `gu-IN` |
| `speed` | default 1.0; numeric 0.85-1.15 or "slow"(0.85)/"medium"(1.0)/"fast"(1.15) |
| `audio_config` | object (required on REST/SSE; optional on WS) |

`audio_config`: `sample_rate` (default 48000; 8000/16000/22050/24000/44100/48000; forced 8000 for mulaw/alaw), `num_channels` (1), `sample_width` (2 bytes), `encoding` (`linear_pcm` default, `pcm_s16le`, `pcm_mulaw`, `pcm_alaw`, `oggopus`), `container` (`wav` default, `raw`, `mp3`, `ogg`, `mulaw`, `alaw`), `bitrate` (mp3 only: 32k-192k, default 128k).
Content-Types: wav `audio/wav`; raw `application/octet-stream`; mp3 `audio/mpeg`; ogg `audio/ogg`; mulaw `audio/basic`; alaw `audio/alaw`. `raw`+`linear_pcm` = headerless 16-bit PCM. OGG Opus is delivered as ONE chunk after synthesis completes (no streaming).

#### 2b. Responses
- REST: 200 with the complete binary audio in the requested container.
- SSE (`text/event-stream`): events `start` {status:"streaming_started", text}; `chunk` {chunk_index, audio (base64), is_final:false}; `complete` {chunk_index, audio:"", is_final:true}; `error` {type, message}.
- WebSocket: auth via `X-API-Key-ID` upgrade header; send ONE JSON synthesis request message per synthesis; server sends `{"type":"start","message":"Streaming started","request_id":...}`, repeated `{"type":"audio","data":{"chunk_index","audio":"<base64>","is_final":false}}`, then `{"type":"complete","data":{"chunk_index","audio":"","is_final":true}}`, or `{"type":"error","message":...}`. Connection stays open after recoverable errors; may send another request on the same socket after `complete`; docs recommend one request at a time. Docs say "Pass all configuration as WebSocket upgrade headers" but the examples send text/voice/model/etc. in the JSON message body (doc inconsistency; follow the JSON-message form shown in the examples, verify with a live call).
- Default chunk audio: 48 kHz, mono, 16-bit, WAV container as configured, base64 inside JSON. SDK note: a WAV (RIFF) header is added by the SDK only when collecting `linear_pcm`+`wav` into a file; streamed bytes are otherwise returned untouched. Whether the server puts a header on every chunk or only the first is NOT stated.
- Errors: 400 invalid body; 500 unexpected/too-long text/invalid embedding; 503 unavailable; 403 FORBIDDEN; 401/402/403/429 per auth table above. TTS WS has no documented close codes, rate limits, or text-length limits.

#### 2c. Voices https://docs.gnani.ai/api/TTS/available-voices.md
- timbre-v3.0 (13, all 10 languages): Avinash(M), Chetana(F), Devansh(M), Harshad(M), Kalpana(F), Lakshmi(F), Prakash(M), Riyaa(F), Shivani(F), Shubham(M), Tarika(F), Vasudha(F), Yashraj(M).
- timbre-v2.5 (42; preferred language): Hindi - Ambuja F, Bhavna F, Chitra F, Deepak M, Hemraj M, Jalaj M, Jwala F, Nalini F, Omkar M, Roopesh M, Urmila F, Vikrant M, Yashvi F; Hinglish - Poorvi F; English - Devika F, Girish M, Kaveri F, Pranav M, Shlok M, Trupti F; Tamil - Asmita F, Brinda F, Noopur F, Trisha F, Vedika F; Telugu - Lavanya F, Lehara F, Suhana F, Varuni F, Yukti F; Kannada - Kavin M, Saanvi F; Malayalam - Reshma F, Riyaan M; Marathi - Ishaan M, Zahira F; Punjabi - Mehuli F, Zayan M; Bengali - Dhruva M, Kirra F; Gujarati - Falak F, Veera M.
- Voices are not shared between models.

#### 2d. Latency
No TTS latency numbers in official docs. Qualitative only: WebSocket is "lowest latency / lowest time-to-first-audio"; SSE and WS are for progressive playback. https://docs.gnani.ai/python-sdk/text-to-speech.md [RAW]

### 3. Official SDK
- PyPI package `gnani-vachana` v0.9.0 (released 2026-10-27, MIT, author Genvoice / support@gnani.ai). `pip install gnani-vachana` (or `uv add gnani-vachana`). Import package is `gnani`. Python requirement is stated as 3.9+ (SDK intro, PyPI) and 3.10+ (some pages). Repo: github.com/Gnani-AI-Mintlify/Gnani-Vachana. https://pypi.org/project/gnani-vachana/ , https://docs.gnani.ai/python-sdk/introduction.md
- Classes: `gnani.stt` -> `GnaniSTTClient` (REST), `GnaniSTTStreamClient` (WS), batch clients; `gnani.tts` -> `GnaniTTSClient` (REST), `GnaniTTSStreamClient` (SSE), `GnaniTTSRealtimeClient` (WS, async). Key from `api_key=` or env `GNANI_API_KEY`.
- STT stream client: `GnaniSTTStreamClient(api_key=None, language_code="en-IN", sample_rate=16000, format="verbatim", itn_native_numerals=False, base_url="https://api.vachana.ai")`; `await connect()`, `await send_audio(chunk)`, `await close()`, `stream_audio(...)`, async-iterate events (`StreamConnectedEvent`, `StreamProcessingEvent`, `StreamTranscriptEvent`, `StreamErrorEvent`). https://docs.gnani.ai/python-sdk/speech-to-text.md
- TTS realtime: `async with GnaniTTSRealtimeClient() as c: async for chunk in c.synthesize(text, voice="Nalini", model="timbre-v2.5", language="hi-IN")`; typed events via `synthesize_events()` (`TTSStartEvent`, `TTSAudioChunkEvent`, `TTSCompletedEvent`).
- Integration plugins listed by Gnani: LiveKit and Pipecat (https://docs.gnani.ai/livekit/introduction.md , https://docs.gnani.ai/pipecat/introduction.md , not fetched in detail; PyPI `livekit-plugins-gnani` seen in search results).
- No JavaScript/npm SDK found.

### 4. LLM (Evon / Inya)
- **No public Gnani LLM inference API found in the speech API docs.** https://docs.gnani.ai/api/introduction/introduction.md lists none.
- Inya.ai is the no-code agent builder platform. Its Platform API (base `https://api.inya.ai/platform/v1/...`, auth header `x-api-key`, needs `agents` permission) manages agents/FAQs/conversations; `GET /platform/v1/agents/config/llm_models` is only a config list of available models (Gnani "Pampa Go", "Pampa Priority", plus OpenAI/Anthropic models), not an inference endpoint. https://docs.gnani.ai/Platform/Get_LLM_Models.md
- Evon v3.3 (`gnani-evon-v3.3`, 30B-A3B MoE, 128K context, Apache-2.0): open weights by request on Hugging Face; hosted option is a FriendliAI Dedicated Endpoint (single-tenant). Self-hosting with vLLM/SGLang exposes an OpenAI-compatible `/v1` API (that compatibility comes from vLLM/SGLang, not from a Gnani-hosted service). https://friendli.ai/models/gnani/gnani-evon-v3.3-30B-A3B . Gnani's Artha page lists only STT/TTS APIs and mentions a browser playground, no Evon API: https://gnani.ai/artha-sovereign-ai

---------------------------------------------------------------------------

## UNVERIFIED / NOT FOUND

- Exact numeric rate limits (requests/min, concurrent sessions): NOT FOUND (docs defer to the account console at app.gnani.ai).
- Pricing: NOT FOUND in API docs.
- TTS latency / time-to-first-audio numbers: NOT FOUND. STT latency: only "P95 ~200 ms" (introduction page) and per-segment `latency` field in responses; no per-model benchmark. WER: "<4% WER on clean Indian audio" for v2.5 appears only in a search snippet of gnani.ai/dev (vendor marketing, not confirmed in API docs).
- Interim (partial) STT transcripts: NOT SUPPORTED per docs (final-per-segment only).
- Whether streamed TTS WAV chunks each carry a RIFF header or only the first: NOT STATED. A candidate for a pipeline is `container:"raw"`, `encoding:"linear_pcm"` (headerless 16-bit PCM, mono) with explicit `sample_rate`, but the docs' own examples use `wav`; verify with a live call before relying on it.
- TTS WebSocket: close codes, idle timeout value, max text length, concurrency: NOT FOUND. Config-in-headers vs JSON body ambiguity (see 2b) needs a live test.
- TTS SSML: introduction page claims "TTS supports SSML"; no SSML syntax page found. NOT VERIFIED.
- Whether `hi-en` works with timbre-v3.0: docs list `hi-en` as an accepted `language` value everywhere, but the voices page only lists Hinglish as a v2.5 preferred language. Ambiguous.
- Legacy hosts `api.gnani.ai/prisma/v3/transcribe`, `wss://api.gnani.ai/prisma/v3/stream`, `https://api.gnani.ai/timbre/v2/synthesize` with Bearer auth (from search snippets of gnani.ai/dev pages): NOT confirmed by official docs; do not use. Third-party catalog (callmissed.com) lists `/v1/audio/transcriptions` OpenAI-style paths: not an official source, ignored.
- OpenAI-compatible Gnani-hosted LLM API: NOT FOUND. No Evon endpoint with official API key/base URL found; only open weights + FriendliAI dedicated endpoint + Inya agent platform.
- JS/TypeScript SDK: NOT FOUND.
- GitHub repos github.com/gnani-ai/API-service and github.com/gnani-ai/text-to-speech appeared in search results (older API generation) but were not opened; official docs and PyPI supersede them.
- Doc contradictions: Python version (3.9+ vs 3.10+); STT realtime example sleeps 0.1 s per chunk vs stated 32 ms cadence; TTS WS "all config in headers" vs JSON-body examples.

## Implementation implications for dost-ai (informational, no code)
- A browser cannot connect directly to the STT WebSocket (custom headers required); a server-side proxy is required, which also keeps the key secret. Same for TTS.
- STT is VAD-segmented and final-only: the UI cannot show live partials; expected latency is end-of-speech plus per-segment `latency`.
- Telephony-friendly: 8 kHz in for STT; `mulaw`/`alaw` 8 kHz out for TTS.
