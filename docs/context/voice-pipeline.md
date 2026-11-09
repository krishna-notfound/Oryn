# Voice pipeline

The core subsystem. Goal: minimal time from *user stops speaking* to *user hears Oryn*,
and instant silence when the user interrupts.

## Flow

```
Browser mic (AudioWorklet, 16 kHz PCM s16le mono)
  → WS binary frames ─────────────► VoiceSession (backend/app/websocket/session.py)
                                       │ on_audio
                                       ▼
                                 VoiceConversation (conversation/engine)
                                       │ re-chunk to 1024-byte frames
                                       ▼
                                 Gnani Prisma WS  ──speech_start──► barge-in if SPEAKING
                                       │ transcript (final, per VAD segment)
                                       ▼
                       ContextBuilder (system prompt + profile + recent turns + memories)
                                       ▼
                       LanguageModel.stream()  ── tool calls → ToolRunner (≤3 per turn)
                                       │ text deltas
                                       ▼
                       SentenceSegmenter  (first clause flushes early)
                                       │ sentences, in order
                                       ▼
                       Gnani Timbre WS (raw PCM, 24 kHz)  ── one synth per sentence,
                                       │ pipelined: next sentence synthesises while current plays
                                       ▼
                       EventSink.send_audio(generation_id, pcm) → browser playback queue
```

## Turn and generation ids

- `turn_id` increments for every final user utterance (voice or text).
- `generation_id` increments for every assistant reply attempt. Every audio frame carries it.
- On interrupt the server bumps the current generation to *cancelled*; the browser drops any
  frame whose id ≠ its current id. Stale audio can never play.

## State machine (`backend/app/voice/state.py`)

| From        | Event                         | To           |
| ----------- | ----------------------------- | ------------ |
| IDLE        | session started               | LISTENING    |
| LISTENING   | final transcript / user text  | PROCESSING   |
| PROCESSING  | LLM request sent              | GENERATING   |
| GENERATING  | first audio chunk sent        | SPEAKING     |
| GENERATING  | text-only reply finished      | LISTENING    |
| SPEAKING    | playback finished + done      | LISTENING    |
| PROCESSING / GENERATING / SPEAKING | user interrupt / speech_start | INTERRUPTED |
| INTERRUPTED | cancellation complete         | LISTENING    |
| any         | provider connection lost      | RECONNECTING |
| RECONNECTING| reconnected                   | LISTENING    |
| any         | unrecoverable error           | ERROR        |
| any         | session closed                | IDLE         |

Invalid transitions raise — never manage state with loose booleans.

## Barge-in

Two triggers, whichever is first:
1. **Client**: browser energy VAD detects speech while assistant audio plays → stops playback
   locally *immediately* and sends `user.interrupt`.
2. **Server**: Prisma emits `speech_start` while state is SPEAKING/GENERATING.

On interrupt: cancel LLM task, cancel TTS tasks, bump generation, send `interrupted`,
transition INTERRUPTED → LISTENING, record `interrupt_to_stop_ms`. The partial assistant
text that was actually spoken is kept in history (truncated), so context stays honest.

## Endpointing

Prisma does server-side VAD and emits one final transcript per segment. We additionally send
Prisma `{"type":"commit"}` when the client sends `audio.end` (mic released / push-to-talk).
VAD tuning is provider-side; we measure its effect via `stt_ms`.

## Latency budget (targets, not promises)

| Stage                              | Target p50 |
| ---------------------------------- | ---------- |
| speech end → Prisma final          | ~200–300 ms |
| LLM time-to-first-token            | ~200–400 ms |
| first sentence → Timbre first byte | ~150–250 ms |
| **speech end → first audio**       | **500–700 ms** (sub-400 = stretch) |
| interrupt → playback stopped       | < 100 ms client-side |

Tricks: keep Prisma and Timbre sockets warm per session; flush the first clause early
(`,` `।` `?` after ≥ 4 words); synthesise sentence N+1 while N plays; no format conversion
(Timbre `container: raw`, `encoding: linear_pcm`).

Measured stages are emitted as `metrics` events per turn and aggregated (p50/p95) by
`backend/app/voice/latency.py`.
