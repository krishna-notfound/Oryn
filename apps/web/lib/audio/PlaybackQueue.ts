import { TypedEmitter } from "../realtime/TypedEmitter";

export interface PlaybackEvents {
  /** First sample of a generation is (about to be) audible. */
  "playback.started": number;
  /** Generation fully played: done flag received and every scheduled chunk ended. */
  "playback.finished": number;
}

/** Cushion before the first chunk of a burst so network jitter does not cause gaps. */
const JITTER_LEAD_S = 0.06;
const LEVEL_GAIN = 3;

/**
 * Gapless PCM playback on a single AudioContext.
 *
 * Chunks are scheduled back to back on the audio clock. Only frames of the current
 * generation are accepted; `stop()` halts everything instantly and clears the generation
 * so any late frame of the cancelled reply is dropped (barge-in safety).
 */
export class PlaybackQueue {
  readonly events = new TypedEmitter<PlaybackEvents>();

  private context: AudioContext | null = null;
  private analyser: AnalyserNode | null = null;
  private gain: GainNode | null = null;
  private levelBuffer: Float32Array<ArrayBuffer> | null = null;
  private readonly sources = new Set<AudioBufferSourceNode>();

  private generationId: number | null = null;
  private sampleRate = 24_000;
  private nextStartTime = 0;
  private generationDone = false;
  private startedEmitted = false;
  private startedTimer: ReturnType<typeof setTimeout> | null = null;

  get currentGenerationId(): number | null {
    return this.generationId;
  }

  /** True while any scheduled chunk is still sounding or waiting to sound. */
  get isPlaying(): boolean {
    return this.sources.size > 0;
  }

  /** Must run inside a user gesture so the browser lets the context start. */
  async init(): Promise<void> {
    if (this.context) return;
    const context = new AudioContext({ latencyHint: "interactive" });
    const analyser = context.createAnalyser();
    analyser.fftSize = 1024;
    const gain = context.createGain();
    analyser.connect(gain).connect(context.destination);

    this.context = context;
    this.analyser = analyser;
    this.gain = gain;
    this.levelBuffer = new Float32Array(analyser.fftSize);
    if (context.state === "suspended") await context.resume();
  }

  /** Called on `assistant.audio.start`. A new generation cancels whatever was queued. */
  beginGeneration(generationId: number, sampleRate: number): void {
    if (this.generationId !== generationId) this.stop();
    this.generationId = generationId;
    this.sampleRate = sampleRate;
    this.generationDone = false;
    this.startedEmitted = false;
  }

  /** Called on `assistant.done`: finish once the already queued audio has played out. */
  markGenerationDone(generationId: number): void {
    if (generationId !== this.generationId) return;
    this.generationDone = true;
    this.finishIfDrained();
  }

  /** Queues PCM s16le mono. Returns false when the frame was dropped as stale. */
  enqueue(generationId: number, pcm: ArrayBuffer): boolean {
    const context = this.context;
    const analyser = this.analyser;
    if (!context || !analyser || generationId !== this.generationId) return false;

    const sampleCount = Math.floor(pcm.byteLength / 2);
    if (sampleCount === 0) return false;
    const samples = new Int16Array(pcm, 0, sampleCount);

    const buffer = context.createBuffer(1, sampleCount, this.sampleRate);
    const channel = buffer.getChannelData(0);
    for (let i = 0; i < sampleCount; i++) channel[i] = samples[i] / 32768;

    const source = context.createBufferSource();
    source.buffer = buffer;
    source.connect(analyser);

    const now = context.currentTime;
    const underrun = this.nextStartTime <= now;
    const startAt = underrun ? now + JITTER_LEAD_S : this.nextStartTime;
    this.nextStartTime = startAt + buffer.duration;

    source.onended = () => {
      this.sources.delete(source);
      source.disconnect();
      if (generationId === this.generationId) this.finishIfDrained();
    };
    this.sources.add(source);
    source.start(startAt);

    if (!this.startedEmitted) {
      this.startedEmitted = true;
      const latencyS = context.outputLatency || context.baseLatency || 0;
      const delayMs = Math.max(0, (startAt - now + latencyS) * 1000);
      this.startedTimer = setTimeout(() => {
        this.startedTimer = null;
        if (generationId === this.generationId) this.events.emit("playback.started", generationId);
      }, delayMs);
    }
    return true;
  }

  /** Instantly silences output and forgets the generation (barge-in / interrupted). */
  stop(): void {
    if (this.startedTimer) clearTimeout(this.startedTimer);
    this.startedTimer = null;
    for (const source of this.sources) {
      source.onended = null;
      try {
        source.stop();
      } catch {
        // already stopped
      }
      source.disconnect();
    }
    this.sources.clear();
    this.nextStartTime = 0;
    this.generationId = null;
    this.generationDone = false;
    this.startedEmitted = false;
  }

  setMuted(muted: boolean): void {
    if (this.gain) this.gain.gain.value = muted ? 0 : 1;
  }

  /** Output RMS level in 0..1, measured before the mute gain so the orb still moves. */
  get level(): number {
    if (!this.analyser || !this.levelBuffer || this.sources.size === 0) return 0;
    this.analyser.getFloatTimeDomainData(this.levelBuffer);
    let sum = 0;
    for (const sample of this.levelBuffer) sum += sample * sample;
    return Math.min(1, Math.sqrt(sum / this.levelBuffer.length) * LEVEL_GAIN);
  }

  close(): void {
    this.stop();
    this.events.clear();
    void this.context?.close().catch(() => undefined);
    this.context = null;
    this.analyser = null;
    this.gain = null;
  }

  private finishIfDrained(): void {
    const id = this.generationId;
    if (id === null || !this.generationDone || this.sources.size > 0) return;
    this.generationDone = false;
    this.events.emit("playback.finished", id);
  }
}
