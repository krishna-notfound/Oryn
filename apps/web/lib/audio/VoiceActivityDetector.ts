import { TypedEmitter } from "../realtime/TypedEmitter";
import { VadCore, type VadConfig, type VadEnded, type VadStarted } from "./vadCore";

export interface VadEvents {
  speechStarted: VadStarted;
  speechEnded: VadEnded;
}

/**
 * Browser VAD fed by the mic worklet's per-frame RMS. Emits `speechStarted` / `speechEnded`
 * (timestamps are `performance.now()` ms) and never touches the audio stream itself: mic frames
 * keep flowing to the server regardless.
 */
export class VoiceActivityDetector {
  readonly events = new TypedEmitter<VadEvents>();
  private readonly core: VadCore;

  constructor(config: Partial<VadConfig> = {}) {
    this.core = new VadCore(config);
  }

  get isSpeaking(): boolean {
    return this.core.isSpeaking;
  }

  process(level: number, assistantPlaying: boolean): void {
    this.emit(this.core.push(level, performance.now(), assistantPlaying));
  }

  /** Ends an open utterance now, e.g. when the user mutes mid-sentence. */
  flush(): void {
    this.emit(this.core.flush(performance.now()));
  }

  close(): void {
    this.core.reset();
    this.events.clear();
  }

  private emit(event: VadStarted | VadEnded | null): void {
    if (!event) return;
    if (event.type === "speechStarted") this.events.emit("speechStarted", event);
    else this.events.emit("speechEnded", event);
  }
}
