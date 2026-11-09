/**
 * Protocol v2 mirror of backend/app/websocket/protocol.py (spec: realtime-pipeline-v2 design §7).
 * Change both files together. This module has no runtime imports so it is unit-testable in Node.
 *
 * Text frames: JSON `{"v":2,"type",...}` (client -> server) and
 *   `{"v":2,"type","session_id","turn_id"?,"generation_id"?,"seq","ts", ...payload}` (server -> client).
 * `input.speech_started.ts` is epoch ms of the VAD event. `input.speech_ended.ts` is epoch ms of the
 * LAST VOICED FRAME, i.e. when the user actually stopped talking, not of the emission that follows
 * the 250 ms end-silence hangover. Server marks and the client's perceived_ms share that origin, so
 * the hangover is part of the measured latency.
 *
 * Binary frames:
 *   client -> server : PCM s16le mono at SessionReady.input_sample_rate, <= MAX_AUDIO_FRAME_BYTES
 *   server -> client : uint32 BE generation_id + uint32 BE seq (per-generation chunk index) + PCM s16le
 *                      at AudioStarted.sample_rate
 */

export const PROTOCOL_VERSION = 2;
export const MAX_AUDIO_FRAME_BYTES = 32_768;
export const MAX_TEXT_CHARS = 2_000;
export const AUDIO_HEADER_BYTES = 8;

export const VOICE_STATES = [
  "IDLE",
  "LISTENING",
  "PROCESSING",
  "GENERATING",
  "SPEAKING",
  "INTERRUPTED",
  "RECONNECTING",
  "ERROR",
] as const;
export type VoiceState = (typeof VOICE_STATES)[number];

// ---------------------------------------------------------------- client -> server

export type ClientMessage =
  | { type: "session.start"; token: string; language: string; mode: "voice" | "text"; conversation_id?: string | null }
  | { type: "input.speech_started"; ts: number }
  | { type: "input.speech_ended"; ts: number }
  | { type: "user.text"; text: string }
  | { type: "turn.cancel"; generation_id: number }
  | { type: "playback.started"; generation_id: number; client_latency_ms?: number }
  | { type: "playback.finished"; generation_id: number }
  | { type: "turn.metrics"; turn_id: number; generation_id: number; perceived_ms: number; playback_lead_ms: number }
  | { type: "heartbeat" }
  | { type: "session.end" };

// ---------------------------------------------------------------- server -> client

export interface Envelope {
  v: 2;
  session_id: string;
  turn_id?: number;
  generation_id?: number;
  seq: number;
  ts: number;
}

/** Server marks for one turn (ms); absent/null when the stage did not run. */
export interface ServerTurnMetrics {
  stt_ms?: number | null;
  llm_ttft_ms?: number | null;
  segmentation_ms?: number | null;
  tts_slot_wait_ms?: number | null;
  tts_ttfb_ms?: number | null;
  server_first_audio_ms?: number | null;
  speech_end_to_first_audio_ms?: number | null;
  interrupt_to_stop_ms?: number | null;
}

type Scoped = { generation_id: number };

export type SessionReady = Envelope & {
  type: "session.ready";
  conversation_id: string;
  input_sample_rate: number;
  output_sample_rate: number;
  protocol: 2;
};
export type StateChanged = Envelope & { type: "state"; state: VoiceState };
export type TranscriptFinal = Envelope & { type: "transcript.final"; text: string; language?: string | null };
export type LlmStarted = Envelope & Scoped & { type: "llm.started" };
export type TextDelta = Envelope & Scoped & { type: "text.delta"; delta: string };
export type TextSegment = Envelope & Scoped & { type: "text.segment"; index: number; text: string };
export type AudioStarted = Envelope & Scoped & { type: "audio.started"; sample_rate: number; encoding: "pcm_s16le" };
export type AudioCompleted = Envelope & Scoped & { type: "audio.completed" };
export type TurnCompleted = Envelope & Scoped & { type: "turn.completed"; text: string };
export type TurnCancelled = Envelope & { type: "turn.cancelled"; reason: string; heard_text?: string | null };
export type TurnMetricsEvent = Envelope & ServerTurnMetrics & { type: "turn.metrics" };
export type PipelineError = Envelope & { type: "pipeline.error"; code: string; message: string; retryable: boolean };
export type Heartbeat = Envelope & { type: "heartbeat" };

export type ServerEvent =
  | SessionReady
  | StateChanged
  | TranscriptFinal
  | LlmStarted
  | TextDelta
  | TextSegment
  | AudioStarted
  | AudioCompleted
  | TurnCompleted
  | TurnCancelled
  | TurnMetricsEvent
  | PipelineError
  | Heartbeat;

/**
 * Events tied to one assistant generation. The first six always carry `generation_id`;
 * `turn.cancelled` / `turn.metrics` may not (e.g. a turn that failed before generating) and are
 * then delivered ungated.
 */
export type GenerationEvent =
  | LlmStarted
  | TextDelta
  | TextSegment
  | AudioStarted
  | AudioCompleted
  | TurnCompleted
  | TurnCancelled
  | TurnMetricsEvent;

const GENERATION_REQUIRED_TYPES: ReadonlySet<string> = new Set<GenerationEvent["type"]>([
  "llm.started",
  "text.delta",
  "text.segment",
  "audio.started",
  "audio.completed",
  "turn.completed",
]);
const GENERATION_EVENT_TYPES: ReadonlySet<string> = new Set<GenerationEvent["type"]>([
  ...GENERATION_REQUIRED_TYPES,
  "turn.cancelled",
  "turn.metrics",
] as GenerationEvent["type"][]);

const SERVER_EVENT_TYPES: ReadonlySet<string> = new Set<ServerEvent["type"]>([
  "session.ready",
  "state",
  "transcript.final",
  "pipeline.error",
  "heartbeat",
  ...GENERATION_EVENT_TYPES,
] as ServerEvent["type"][]);

/** The generation id when `event` is gated by generation, otherwise undefined. */
export function generationOf(event: ServerEvent): number | undefined {
  return GENERATION_EVENT_TYPES.has(event.type) ? event.generation_id : undefined;
}

/**
 * Parses a text frame; returns null for anything that is not a well-formed v2 server event
 * (bad JSON, wrong version, unknown type, or a generation-scoped event without its id).
 */
export function parseServerEvent(raw: string): ServerEvent | null {
  let value: unknown;
  try {
    value = JSON.parse(raw);
  } catch {
    return null;
  }
  if (typeof value !== "object" || value === null) return null;
  const record = value as { v?: unknown; type?: unknown; generation_id?: unknown };
  if (record.v !== PROTOCOL_VERSION) return null;
  if (typeof record.type !== "string" || !SERVER_EVENT_TYPES.has(record.type)) return null;
  if (GENERATION_REQUIRED_TYPES.has(record.type) && !Number.isInteger(record.generation_id)) return null;
  if (record.generation_id !== undefined && !Number.isInteger(record.generation_id)) return null;
  return value as ServerEvent;
}

export interface AudioFrame {
  generationId: number;
  /** Per-generation chunk index. */
  seq: number;
  /** PCM s16le mono view over the received buffer (header skipped, no copy). */
  samples: Int16Array;
}

/** Splits a binary frame into `>II` (generation_id, seq) and the PCM payload. */
export function parseAudioFrame(buffer: ArrayBuffer): AudioFrame | null {
  if (buffer.byteLength <= AUDIO_HEADER_BYTES) return null;
  const view = new DataView(buffer);
  const sampleCount = Math.floor((buffer.byteLength - AUDIO_HEADER_BYTES) / 2);
  return {
    generationId: view.getUint32(0, false),
    seq: view.getUint32(4, false),
    samples: new Int16Array(buffer, AUDIO_HEADER_BYTES, sampleCount),
  };
}
