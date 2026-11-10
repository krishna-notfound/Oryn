import { GenerationGate } from "../realtime/GenerationGate";
import { TypedEmitter } from "../realtime/TypedEmitter";
import { AdaptiveLead, audibleAtMs, gainFor, int16ToFloat32, planChunk, RESTORE_RAMP_MS, SeqReorderBuffer } from "./schedulerCore";

export interface ChunkScheduled {
  generationId: number;
  /** `performance.now()` time the first sample of the generation is expected to be audible. */
  audibleAtMs: number;
  /** How far ahead of the audio clock the first chunk was scheduled. */
  leadMs: number;
}

export interface ChunkTiming {
  generationId: number;
  seq: number;
  /** `performance.now()` time the chunk's first sample is expected to be audible. */
  audibleAtMs: number;
  durationMs: number;
}

export interface SchedulerEvents {
  /** First chunk of a generation was put on the audio clock. */
  scheduled: ChunkScheduled;
  /** Every chunk put on the audio clock, with where it will be audible (drives synced captions). */
  chunk: ChunkTiming;
  /** First sample of the generation is (estimated to be) audible now. */
  audible: number;
  /** Generation fully played: completed by the server and every chunk ended. */
  finished: number;
}

/** Timbre's rate; replaced by the server's `output_sample_rate` once `session.ready` arrives. */
const DEFAULT_OUTPUT_RATE = 24_000;
/** Only this much audio is turned into Web Audio nodes ahead of the clock; the rest waits as raw PCM. */
const HORIZON_S = 1.5;
/** Hard ceiling on PCM waiting to be scheduled (~87 s at 24 kHz); beyond it chunks are dropped. */
const MAX_PENDING_BYTES = 4 * 1024 * 1024;
const LEVEL_GAIN = 3;

interface Chunk {
  seq: number;
  samples: Int16Array;
}

interface Graph {
  context: AudioContext;
  analyser: AnalyserNode;
  gain: GainNode;
  levelBuffer: Float32Array<ArrayBuffer>;
}

/**
 * Gapless low-latency PCM playback on one AudioContext running at the TTS sample rate (no
 * resampling). Chunks are ordered by `seq`, scheduled back to back with an adaptive lead
 * (40 ms, +20 ms per underrun, max 150 ms) and only a short horizon ahead of the clock.
 *
 * `stop()` is the barge-in primitive: synchronous, silences every source, drops all buffered
 * audio and retires the generation so late frames are rejected. It never waits for anything.
 */
export class AudioScheduler {
  readonly events = new TypedEmitter<SchedulerEvents>();

  private graph: Graph | null = null;
  private readonly gate = new GenerationGate();
  private readonly lead = new AdaptiveLead();
  private readonly reorder = new SeqReorderBuffer<Chunk>();
  private readonly sources = new Set<AudioBufferSourceNode>();
  private pending: Chunk[] = [];
  private pendingBytes = 0;

  private sampleRate = DEFAULT_OUTPUT_RATE;
  private nextStart: number | null = null;
  private active = false;
  private done = false;
  private scheduledEmitted = false;
  private audibleTimer: ReturnType<typeof setTimeout> | null = null;
  private muted = false;
  private ducked = false;

  /** Generation currently owning the speaker, or null. */
  get generationId(): number | null {
    return this.gate.current;
  }

  /** Assistant audio is in flight: from `audio.started` until the generation drained or was stopped. */
  get isActive(): boolean {
    return this.active;
  }

  /** Playback is softened because the user is talking over it. */
  get isDucked(): boolean {
    return this.ducked;
  }

  get leadMs(): number {
    return this.lead.millis;
  }

  /** Must run inside a user gesture so the browser lets the context start. */
  async init(sampleRate: number = DEFAULT_OUTPUT_RATE): Promise<void> {
    if (this.graph) return;
    const graph = this.build(sampleRate);
    this.graph = graph;
    this.sampleRate = sampleRate;
    if (graph.context.state === "suspended") await graph.context.resume();
  }

  /**
   * Aligns the context with the server's `output_sample_rate`. The context is created before the
   * socket is ready (user gesture), so this swaps it when the rates differ and nothing is playing.
   * If the replacement cannot start, the old context stays and Web Audio resamples the buffers.
   */
  async useOutputRate(rate: number): Promise<void> {
    // The PCM rate is the server's regardless of what rate the context ends up running at.
    if (!this.active) this.sampleRate = rate;
    const current = this.graph;
    if (!current || current.context.sampleRate === rate || this.active) return;
    let next: Graph;
    try {
      next = this.build(rate);
      await next.context.resume();
    } catch {
      return;
    }
    if (next.context.state !== "running" || this.active || this.graph !== current) {
      void next.context.close().catch(() => undefined);
      return;
    }
    this.graph = next;
    void current.context.close().catch(() => undefined);
  }

  /** `audio.started`. A newer generation replaces whatever was queued; retired ids are refused. */
  beginGeneration(generationId: number, sampleRate: number): boolean {
    const admission = this.gate.admit(generationId);
    if (admission === "drop") return false;
    if (admission === "new") {
      this.clearPlayback();
      this.active = true;
    }
    this.sampleRate = sampleRate;
    return true;
  }

  /** `audio.completed`: finish once everything already queued has played out. */
  markGenerationDone(generationId: number): void {
    if (generationId !== this.gate.current) return;
    for (const chunk of this.reorder.flush()) this.hold(chunk);
    this.done = true;
    this.pump();
    this.finishIfDrained();
  }

  /** Queues one chunk. Returns false when it was dropped (stale generation, no context). */
  enqueue(generationId: number, seq: number, samples: Int16Array): boolean {
    const graph = this.graph;
    if (!graph || generationId !== this.gate.current || samples.length === 0) return false;
    if (graph.context.state !== "running") void graph.context.resume().catch(() => undefined);
    for (const chunk of this.reorder.push(seq, { seq, samples })) this.hold(chunk);
    this.pump();
    return true;
  }

  /** Barge-in / cancel: instant silence, buffers flushed, current generation marked stale. */
  stop(): void {
    const id = this.gate.current;
    if (id !== null) this.gate.retire(id);
    this.clearPlayback();
  }

  /** Reconnect: server ids restart, so forget stale marks as well as audio. */
  reset(): void {
    this.clearPlayback();
    this.gate.reset();
  }

  setMuted(muted: boolean): void {
    this.muted = muted;
    this.applyGain(0);
  }

  /**
   * Overlapping user speech: drop to `DUCK_GAIN` at the next render quantum without stopping, so a
   * "hmm" does not cut the assistant off. Returns false when nothing is playing.
   */
  duck(): boolean {
    if (!this.active) return false;
    this.ducked = true;
    this.applyGain(0);
    return true;
  }

  /** Back to full volume, ramped so the return is not a jump. */
  restore(rampMs: number = RESTORE_RAMP_MS): void {
    this.ducked = false;
    this.applyGain(rampMs);
  }

  /** Output RMS level in 0..1, measured before the mute gain so the orb still moves. */
  get level(): number {
    const graph = this.graph;
    if (!graph || this.sources.size === 0) return 0;
    graph.analyser.getFloatTimeDomainData(graph.levelBuffer);
    let sum = 0;
    for (const sample of graph.levelBuffer) sum += sample * sample;
    return Math.min(1, Math.sqrt(sum / graph.levelBuffer.length) * LEVEL_GAIN);
  }

  close(): void {
    this.clearPlayback();
    this.events.clear();
    void this.graph?.context.close().catch(() => undefined);
    this.graph = null;
  }

  private build(sampleRate: number): Graph {
    let context: AudioContext;
    try {
      context = new AudioContext({ latencyHint: "interactive", sampleRate });
    } catch {
      context = new AudioContext({ latencyHint: "interactive" });
    }
    const analyser = context.createAnalyser();
    analyser.fftSize = 1024;
    const gain = context.createGain();
    gain.gain.value = gainFor(this.muted, this.ducked);
    analyser.connect(gain).connect(context.destination);
    return { context, analyser, gain, levelBuffer: new Float32Array(analyser.fftSize) };
  }

  private applyGain(rampMs: number): void {
    const graph = this.graph;
    if (!graph) return;
    const param = graph.gain.gain;
    const now = graph.context.currentTime;
    const target = gainFor(this.muted, this.ducked);
    param.cancelScheduledValues(now);
    if (rampMs > 0) {
      param.setValueAtTime(param.value, now);
      param.linearRampToValueAtTime(target, now + rampMs / 1000);
    } else {
      param.setValueAtTime(target, now);
    }
  }

  private hold(chunk: Chunk): void {
    if (this.pendingBytes + chunk.samples.byteLength > MAX_PENDING_BYTES) return;
    this.pending.push(chunk);
    this.pendingBytes += chunk.samples.byteLength;
  }

  /** Turns waiting PCM into sources while the scheduled audio is shorter than the horizon. */
  private pump(): void {
    const graph = this.graph;
    if (!graph) return;
    while (this.pending.length > 0) {
      if (this.nextStart !== null && this.nextStart - graph.context.currentTime >= HORIZON_S) break;
      const chunk = this.pending.shift() as Chunk;
      this.pendingBytes -= chunk.samples.byteLength;
      this.schedule(graph, chunk);
    }
  }

  private schedule(graph: Graph, { seq, samples }: Chunk): void {
    const generationId = this.gate.current;
    if (generationId === null) return;
    const { context, analyser } = graph;

    const buffer = context.createBuffer(1, samples.length, this.sampleRate);
    int16ToFloat32(samples, buffer.getChannelData(0));
    const source = context.createBufferSource();
    source.buffer = buffer;
    source.connect(analyser);

    const now = context.currentTime;
    const plan = planChunk(now, this.nextStart, this.lead);
    this.nextStart = plan.startAt + buffer.duration;

    source.onended = () => {
      this.sources.delete(source);
      source.disconnect();
      if (generationId !== this.gate.current) return;
      this.pump();
      this.finishIfDrained();
    };
    this.sources.add(source);
    source.start(plan.startAt);

    const latencyS = context.outputLatency || context.baseLatency || 0;
    const audibleAt = audibleAtMs(performance.now(), now, plan.startAt, latencyS);
    this.events.emit("chunk", { generationId, seq, audibleAtMs: audibleAt, durationMs: buffer.duration * 1000 });

    if (this.scheduledEmitted) return;
    this.scheduledEmitted = true;
    this.events.emit("scheduled", { generationId, audibleAtMs: audibleAt, leadMs: (plan.startAt - now) * 1000 });
    this.audibleTimer = setTimeout(
      () => {
        this.audibleTimer = null;
        if (generationId === this.gate.current) this.events.emit("audible", generationId);
      },
      Math.max(0, audibleAt - performance.now()),
    );
  }

  private finishIfDrained(): void {
    const id = this.gate.current;
    if (id === null || !this.done || this.pending.length > 0 || this.sources.size > 0) return;
    const played = this.scheduledEmitted;
    this.active = false;
    this.done = false;
    this.scheduledEmitted = false;
    this.clearDuck();
    if (played) this.events.emit("finished", id);
  }

  /** Silences and forgets all audio without touching the generation marks. */
  private clearPlayback(): void {
    if (this.audibleTimer) clearTimeout(this.audibleTimer);
    this.audibleTimer = null;
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
    this.pending = [];
    this.pendingBytes = 0;
    this.reorder.reset();
    this.nextStart = null;
    this.active = false;
    this.done = false;
    this.scheduledEmitted = false;
    this.clearDuck();
  }

  /** Nothing left to be loud: the next generation must start at full volume. */
  private clearDuck(): void {
    if (!this.ducked) return;
    this.ducked = false;
    this.applyGain(0);
  }
}
