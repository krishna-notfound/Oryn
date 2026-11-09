import {
  MAX_AUDIO_FRAME_BYTES,
  parseAudioFrame,
  parseServerEvent,
  type ClientMessage,
  type ServerEvent,
} from "./protocol";
import { TypedEmitter } from "./TypedEmitter";

export type ConnectionStatus = "idle" | "connecting" | "ready" | "reconnecting" | "closed";

export interface RealtimeEvents {
  /** Every parsed server JSON event, including `session.ready`. */
  event: ServerEvent;
  /** One server audio frame: generation id + PCM s16le (header stripped). */
  audio: { generationId: number; pcm: ArrayBuffer };
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

const PING_INTERVAL_MS = 20_000;
const RECONNECT_DELAYS_MS = [500, 1_000, 2_000, 2_000, 2_000];
/** 4401 unauthenticated (a retry cannot help); 4409 replaced by a newer socket. */
const NO_RETRY_CODES: ReadonlySet<number> = new Set([4401, 4409]);
/** Drop mic frames instead of queueing when the socket is backed up: stale audio is worthless. */
const MAX_BUFFERED_BYTES = MAX_AUDIO_FRAME_BYTES * 8;

/**
 * One WebSocket per conversation. Handshake: the first frame is `session.start` carrying the
 * Supabase access token. Reconnects with backoff and resumes the same conversation id.
 */
export class RealtimeClient {
  readonly events = new TypedEmitter<RealtimeEvents>();

  private ws: WebSocket | null = null;
  private status: ConnectionStatus = "idle";
  private conversationId: string | null = null;
  private attempt = 0;
  private pingTimer: ReturnType<typeof setInterval> | null = null;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private disposed = false;

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

  interrupt(): void {
    this.send({ type: "user.interrupt" });
  }

  audioEnd(): void {
    this.send({ type: "audio.end" });
  }

  playbackStarted(generationId: number): void {
    this.send({ type: "playback.started", generation_id: generationId });
  }

  playbackFinished(generationId: number): void {
    this.send({ type: "playback.finished", generation_id: generationId });
  }

  /** Clean shutdown initiated by the user. Safe to call repeatedly. */
  end(): void {
    if (this.disposed) return;
    this.send({ type: "session.end" });
    this.dispose(1000, "client ended", true);
  }

  private send(message: ClientMessage): boolean {
    if (!this.ws || !this.isReady) return false;
    this.ws.send(JSON.stringify(message));
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
      ws.send(JSON.stringify(start));
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
        this.conversationId = event.conversation_id;
        this.attempt = 0;
        this.startPing();
        this.setStatus("ready");
      }
      this.events.emit("event", event);
    } else if (data instanceof ArrayBuffer) {
      const frame = parseAudioFrame(data);
      if (frame) this.events.emit("audio", frame);
    }
  }

  private onClose(ws: WebSocket, code: number, reason: string): void {
    if (ws !== this.ws) return;
    this.ws = null;
    this.stopPing();
    if (this.disposed) return;

    const retriesLeft = this.attempt < RECONNECT_DELAYS_MS.length;
    if (NO_RETRY_CODES.has(code) || !retriesLeft) {
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
    this.stopPing();
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

  private startPing(): void {
    this.stopPing();
    this.pingTimer = setInterval(() => this.send({ type: "ping" }), PING_INTERVAL_MS);
  }

  private stopPing(): void {
    if (this.pingTimer) clearInterval(this.pingTimer);
    this.pingTimer = null;
  }

  private setStatus(status: ConnectionStatus): void {
    if (this.status === status) return;
    this.status = status;
    this.events.emit("status", status);
  }
}
