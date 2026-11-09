/** Pure scheduling logic behind AudioScheduler (spec §8); no Web Audio dependency. */

/**
 * Releases chunks in `seq` order. WebSocket delivery is ordered, so a gap means a server bug:
 * hold at most `maxHeld` early chunks, and when more arrive give up on the missing one rather
 * than stall the reply. Late or duplicate chunks are dropped.
 */
export class SeqReorderBuffer<T> {
  private expected = 0;
  private readonly held = new Map<number, T>();

  constructor(private readonly maxHeld = 1) {}

  get heldCount(): number {
    return this.held.size;
  }

  push(seq: number, item: T): T[] {
    if (seq < this.expected || this.held.has(seq)) return [];
    if (seq === this.expected) {
      const ready = [item];
      this.expected += 1;
      for (let next = this.held.get(this.expected); next !== undefined; next = this.held.get(this.expected)) {
        this.held.delete(this.expected);
        ready.push(next);
        this.expected += 1;
      }
      return ready;
    }
    this.held.set(seq, item);
    return this.held.size > this.maxHeld ? this.flush() : [];
  }

  /** Everything still held, in order; the gap before it is abandoned. */
  flush(): T[] {
    const seqs = [...this.held.keys()].sort((a, b) => a - b);
    const ready = seqs.map((seq) => this.held.get(seq) as T);
    if (seqs.length > 0) this.expected = seqs[seqs.length - 1] + 1;
    this.held.clear();
    return ready;
  }

  reset(): void {
    this.expected = 0;
    this.held.clear();
  }
}

/** Playback lead: start small for latency, grow after each underrun, never above the cap. */
export class AdaptiveLead {
  private ms: number;

  constructor(
    private readonly stepMs = 20,
    private readonly maxMs = 150,
    initialMs = 40,
  ) {
    this.ms = initialMs;
  }

  get seconds(): number {
    return this.ms / 1000;
  }

  get millis(): number {
    return this.ms;
  }

  onUnderrun(): void {
    this.ms = Math.min(this.maxMs, this.ms + this.stepMs);
  }
}

export interface ChunkPlan {
  startAt: number;
  /** The audio clock ran past the end of earlier audio before this chunk was scheduled. */
  underrun: boolean;
}

/**
 * Where on the audio clock the next chunk starts: back to back after earlier audio, otherwise
 * `lead` ahead of now. `nextStart === null` means nothing was scheduled yet (not an underrun).
 */
export function planChunk(now: number, nextStart: number | null, lead: AdaptiveLead): ChunkPlan {
  if (nextStart !== null && nextStart > now) return { startAt: nextStart, underrun: false };
  const underrun = nextStart !== null;
  if (underrun) lead.onUnderrun();
  return { startAt: now + lead.seconds, underrun };
}

/** `performance.now()` time at which the sample scheduled for `startAt` leaves the speaker. */
export function audibleAtMs(nowPerfMs: number, nowCtxS: number, startAtS: number, outputLatencyS: number): number {
  return nowPerfMs + (startAtS - nowCtxS + outputLatencyS) * 1000;
}

export function int16ToFloat32(samples: Int16Array, out: Float32Array): void {
  for (let i = 0; i < samples.length; i++) out[i] = samples[i] / 32768;
}
