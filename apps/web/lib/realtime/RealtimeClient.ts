import { admitEvent, GenerationGate } from "./GenerationGate";
import {
  MAX_AUDIO_FRAME_BYTES,
  PROTOCOL_VERSION,
  parseAudioFrame,
  parseServerEvent,
  type AudioFrame,
  type ClientMessage,
  type ServerEvent,
} from "./protocol";
import { TypedEmitter } from "./TypedEmitter";

export type ConnectionStatus = "idle" | "connecting" | "ready" | "reconnecting" | "closed";

export interface RealtimeEvents {
  /** Every parsed server JSON event that is not tied to a stale generation. */
  event: ServerEvent;
  /** One audio frame of the current generation: `>II` header parsed, PCM as an Int16 view. */
  audio: AudioFrame;
  /** A new socket is about to open: drop anything tied to the previous connection's ids. */
  reset: undefined;
  status: ConnectionStatus;
  /** Terminal: the client will not reconnect any more. */
  closed: { code: number; reason: string; byUser: boolean };
}

export interface RealtimeClientOptions {
  url: string;
  /** Called on every (re)connect so a refreshed Supabase token is always used. */
  getToken: () => Promise<string>;
  language: string;
  mode: "voice" | "text";
}

const HEARTBEAT_INTERVAL_MS = 15_000;
const RECONNECT_DELAYS_MS = [500, 1_000, 2_000, 2_000, 2_000];
/** 4401 unauthenticated, 4409 replaced by a newer socket, 4503 server at capacity: a retry cannot help. */
export const TERMINAL_CLOSE_CODES: ReadonlySet<number> = new Set([4401, 4409, 4503]);
/** Local code for a server that does not speak protocol v2. */
export const CLOSE_PROTOCOL_MISMATCH = 4426;
/** Drop mic frames instead of queueing when the socket is backed up: stale audio is worthless. */
const MAX_BUFFERED_BYTES = MAX_AUDIO_FRAME_BYTES * 8;

/**
 * One WebSocket per conversation, protocol v2. Handshake: the first frame is `session.start`
 * carrying the Supabase access token. Reconnects with backoff and resumes the same conversation id.
 * Events and audio of a generation the app already cancelled (or that is older than the current
 * one) never reach listeners.
 */
export class RealtimeClient {
  readonly events = new TypedEmitter<RealtimeEvents>();

  private ws: WebSocket | null = null;
  private status: ConnectionStatus = "idle";
  private conversationId: string | null = null;
  private attempt = 0;
  private heartbeatTimer: ReturnType<typeof setInterval> | null = null;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private disposed = false;
  private readonly gate = new GenerationGate();

  constructor(private readonly options: RealtimeClientOptions) {}

  get isReady(): boolean {
    return this.status === "ready";
  }

  connect(): void {
    if (this.disposed || this.ws) return;
    void this.open();
  }

  sendAudio(pcm: ArrayBuffer): void {
    const ws = this.ws;
    if (!ws || !this.isReady || pcm.byteLength > MAX_AUDIO_FRAME_BYTES) return;
    if (ws.bufferedAmount > MAX_BUFFERED_BYTES) return;
    ws.send(pcm);
  }

  sendText(text: string): boolean {
    return this.send({ type: "user.text", text });
  }

  /** Client VAD edges, epoch ms. For `speechEnded` pass the last voiced frame, not the emission time. */
  speechStarted(at: number): void {
    this.send({ type: "input.speech_started", ts: Math.round(at) });
  }

  speechEnded(at: number): void {
    this.send({ type: "input.speech_ended", ts: Math.round(at) });
  }

  /**
   * Retires the generation locally, then tells the server. Nothing more of it is delivered even
   * if the server needs a few more ms to stop; the client never waits for the server.
   */
  cancelTurn(generationId: number): void {
    this.gate.retire(generationId);
    this.send({ type: "turn.cancel", generation_id: generationId });
  }

  playbackStarted(generationId: number): void {
    this.send({ type: "playback.started", generation_id: generationId });
  }

  playbackFinished(generationId: number): void {
    this.send({ type: "playback.finished", generation_id: generationId });
  }

  /** Client-measured speech end -> first audible sample. */
  turnMetrics(turnId: number, generationId: number, perceivedMs: number, playbackLeadMs: number): void {
    this.send({
      type: "turn.metrics",
      turn_id: turnId,
      generation_id: generationId,
      perceived_ms: Math.round(perceivedMs),
      playback_lead_ms: Math.round(playbackLeadMs),
    });
  }

  /** Clean shutdown initiated by the user. Safe to call repeatedly. */
  end(): void {
    if (this.disposed) return;
    this.send({ type: "session.end" });
    this.dispose(1000, "client ended", true);
  }

  private send(message: ClientMessage): boolean {
    if (!this.ws || !this.isReady) return false;
    this.ws.send(JSON.stringify({ v: PROTOCOL_VERSION, ...message }));
    return true;
  }

  private async open(): Promise<void> {
    this.setStatus(this.attempt === 0 ? "connecting" : "reconnecting");

    let token: string;
    try {
      token = await this.options.getToken();
    } catch {
      this.dispose(4401, "no auth session", false);
      return;
    }
    if (this.disposed) return;

    // Server ids restart per connection; a stale gate would drop or accept the wrong ones.
    this.gate.reset();
    this.events.emit("reset", undefined);

    const ws = new WebSocket(this.options.url);
    ws.binaryType = "arraybuffer";
    this.ws = ws;

    ws.onopen = () => {
      const start: ClientMessage = {
        type: "session.start",
        token,
        language: this.options.language,
        mode: this.options.mode,
        ...(this.conversationId ? { conversation_id: this.conversationId } : {}),
      };
      // Sent before `ready`, so it cannot go through send().
      ws.send(JSON.stringify({ v: PROTOCOL_VERSION, ...start }));
    };
    ws.onmessage = (message) => this.onMessage(message.data);
    ws.onclose = (close) => this.onClose(ws, close.code, close.reason);
    // `error` is always followed by `close`; reconnection is decided there.
  }

  private onMessage(data: unknown): void {
    if (typeof data === "string") {
      const event = parseServerEvent(data);
      if (!event) return;
      if (event.type === "session.ready") {
        if (event.protocol !== PROTOCOL_VERSION) {
          this.dispose(CLOSE_PROTOCOL_MISMATCH, "protocol mismatch", false);
          return;
        }
        this.conversationId = event.conversation_id;
        this.attempt = 0;
        this.startHeartbeat();
        this.setStatus("ready");
      }
      if (admitEvent(this.gate, event)) this.events.emit("event", event);
    } else if (data instanceof ArrayBuffer) {
      const frame = parseAudioFrame(data);
      if (frame && this.gate.admit(frame.generationId) !== "drop") this.events.emit("audio", frame);
    }
  }

  private onClose(ws: WebSocket, code: number, reason: string): void {
    if (ws !== this.ws) return;
    this.ws = null;
    this.stopHeartbeat();
    if (this.disposed) return;

    const retriesLeft = this.attempt < RECONNECT_DELAYS_MS.length;
    if (TERMINAL_CLOSE_CODES.has(code) || !retriesLeft) {
      this.dispose(code, reason, false);
      return;
    }
    const delay = RECONNECT_DELAYS_MS[this.attempt];
    this.attempt += 1;
    this.setStatus("reconnecting");
    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null;
      void this.open();
    }, delay);
  }

  private dispose(code: number, reason: string, byUser: boolean): void {
    if (this.disposed) return;
    this.disposed = true;
    this.stopHeartbeat();
    if (this.reconnectTimer) clearTimeout(this.reconnectTimer);
    const ws = this.ws;
    this.ws = null;
    if (ws) {
      ws.onclose = null;
      ws.onmessage = null;
      ws.close(1000, "client closing");
    }
    this.setStatus("closed");
    this.events.emit("closed", { code, reason, byUser });
    this.events.clear();
  }

  private startHeartbeat(): void {
    this.stopHeartbeat();
    this.heartbeatTimer = setInterval(() => this.send({ type: "heartbeat" }), HEARTBEAT_INTERVAL_MS);
  }

  private stopHeartbeat(): void {
    if (this.heartbeatTimer) clearInterval(this.heartbeatTimer);
    this.heartbeatTimer = null;
  }

  private setStatus(status: ConnectionStatus): void {
    if (this.status === status) return;
    this.status = status;
    this.events.emit("status", status);
  }
}
