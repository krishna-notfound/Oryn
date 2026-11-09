/**
 * Exact TypeScript mirror of backend/app/websocket/protocol.py.
 * Spec: docs/context/websocket-contract.md. Change both files together.
 *
 * Binary frames:
 *   client -> server : PCM s16le mono at SessionReady.input_sample_rate
 *   server -> client : uint32 big-endian generation_id + PCM s16le
 *                      at AssistantAudioStart.sample_rate
 */

export const MAX_AUDIO_FRAME_BYTES = 32_768;
export const MAX_TEXT_CHARS = 2_000;
export const AUDIO_HEADER_BYTES = 4;

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

export interface SessionStart {
  type: "session.start";
  token: string;
  language: string;
  mode: "voice" | "text";
  conversation_id?: string | null;
}
export interface UserText {
  type: "user.text";
  text: string;
}
export interface UserInterrupt {
  type: "user.interrupt";
}
export interface AudioEnd {
  type: "audio.end";
}
export interface PlaybackStarted {
  type: "playback.started";
  generation_id: number;
}
export interface PlaybackFinished {
  type: "playback.finished";
  generation_id: number;
}
export interface SessionEnd {
  type: "session.end";
}
export interface Ping {
  type: "ping";
}

export type ClientMessage =
  | SessionStart
  | UserText
  | UserInterrupt
  | AudioEnd
  | PlaybackStarted
  | PlaybackFinished
  | SessionEnd
  | Ping;

// ---------------------------------------------------------------- server -> client

export interface SessionReady {
  type: "session.ready";
  session_id: string;
  conversation_id: string;
  input_sample_rate: number;
  output_sample_rate: number;
}
export interface StateChanged {
  type: "state";
  state: VoiceState;
}
export interface TranscriptPartial {
  type: "transcript.partial";
  text: string;
}
export interface TranscriptFinal {
  type: "transcript.final";
  turn_id: number;
  text: string;
  language?: string | null;
}
export interface AssistantText {
  type: "assistant.text";
  generation_id: number;
  delta: string;
}
export interface AssistantAudioStart {
  type: "assistant.audio.start";
  generation_id: number;
  sample_rate: number;
  encoding: "pcm_s16le";
}
export interface AssistantDone {
  type: "assistant.done";
  generation_id: number;
  text: string;
}
export interface Interrupted {
  type: "interrupted";
  generation_id: number;
}
/** All durations in milliseconds; null/absent when the stage did not run. */
export interface TurnMetrics {
  type: "metrics";
  turn_id: number;
  generation_id: number;
  stt_ms?: number | null;
  llm_ttft_ms?: number | null;
  tts_ttfb_ms?: number | null;
  speech_end_to_first_audio_ms?: number | null;
  interrupt_to_stop_ms?: number | null;
}
export interface ErrorEvent {
  type: "error";
  code: string;
  message: string;
  retryable: boolean;
}
export interface Pong {
  type: "pong";
}

export type ServerEvent =
  | SessionReady
  | StateChanged
  | TranscriptPartial
  | TranscriptFinal
  | AssistantText
  | AssistantAudioStart
  | AssistantDone
  | Interrupted
  | TurnMetrics
  | ErrorEvent
  | Pong;

const SERVER_EVENT_TYPES: ReadonlySet<string> = new Set<ServerEvent["type"]>([
  "session.ready",
  "state",
  "transcript.partial",
  "transcript.final",
  "assistant.text",
  "assistant.audio.start",
  "assistant.done",
  "interrupted",
  "metrics",
  "error",
  "pong",
]);

/** Parses a text frame; returns null for anything that is not a known server event. */
export function parseServerEvent(raw: string): ServerEvent | null {
  let value: unknown;
  try {
    value = JSON.parse(raw);
  } catch {
    return null;
  }
  if (typeof value !== "object" || value === null) return null;
  const type = (value as { type?: unknown }).type;
  if (typeof type !== "string" || !SERVER_EVENT_TYPES.has(type)) return null;
  return value as ServerEvent;
}

export interface AudioFrame {
  generationId: number;
  /** PCM s16le mono, header stripped. */
  pcm: ArrayBuffer;
}

/** Splits a binary frame into generation id (uint32 big-endian) and PCM payload. */
export function parseAudioFrame(buffer: ArrayBuffer): AudioFrame | null {
  if (buffer.byteLength <= AUDIO_HEADER_BYTES) return null;
  const generationId = new DataView(buffer).getUint32(0, false);
  return { generationId, pcm: buffer.slice(AUDIO_HEADER_BYTES) };
}
