/**
 * Captions that advance with the voice (spec §5). Pure: no timers, no DOM; every method that
 * depends on time takes `nowMs` on the `performance.now()` clock, so it is deterministic in tests.
 *
 * The server announces each speakable segment before its first audio chunk (`audio.segment`,
 * with the `first_seq` of that chunk) and closes it with its exact length (`audio.segment_done`).
 * The scheduler tells us when each chunk will be audible. A segment therefore starts at the
 * audible time of its `first_seq` chunk and lasts `samples / sample_rate`; until `segment_done`
 * the length is estimated from the PCM received so far, floored by a speaking-rate guess so a
 * segment whose audio is still streaming in does not read as finished early.
 */

/** Typical speech rate across the supported languages, used only to floor the unfinished estimate. */
const ESTIMATE_CHARS_PER_S = 14;

interface Segment {
  index: number;
  text: string;
  words: string[];
  firstSeq: number;
  startMs: number | null;
  receivedSamples: number;
  /** Exact length from `audio.segment_done`, in samples. */
  totalSamples: number | null;
}

export interface CaptionFrame {
  /** The segment before the current one, shown faded; null for the first segment. */
  previous: string | null;
  /** Words of the segment being spoken. */
  words: string[];
  /** How many leading words have been spoken so far. */
  revealed: number;
  /** Cancelled: the caption is stuck at what was heard. */
  frozen: boolean;
}

/**
 * Words the voice has started, weighting each word by its length (plus its trailing space).
 * A word counts as soon as the voice reaches its first character, so the first word shows the
 * moment the segment becomes audible.
 */
export function revealedWordCount(words: readonly string[], progress: number): number {
  const total = words.reduce((sum, word) => sum + word.length + 1, 0);
  let before = 0;
  let revealed = 0;
  for (const word of words) {
    if (before / total > progress) break;
    revealed += 1;
    before += word.length + 1;
  }
  return revealed;
}

export class CaptionTrack {
  private generationId: number | null = null;
  private sampleRate = 24_000;
  private segments: Segment[] = [];
  /** seq -> audible time, for chunks scheduled before their segment was announced. */
  private readonly audibleAt = new Map<number, number>();
  private frozenAt: number | null = null;

  /** True once at least one segment of the current generation is known. */
  get hasSegments(): boolean {
    return this.segments.length > 0;
  }

  /** A new generation owns the speaker: drop the previous captions. */
  begin(generationId: number, sampleRate: number): void {
    if (generationId !== this.generationId) {
      this.reset();
      this.generationId = generationId;
    }
    this.sampleRate = sampleRate;
  }

  reset(): void {
    this.generationId = null;
    this.segments = [];
    this.audibleAt.clear();
    this.frozenAt = null;
  }

  onSegment(generationId: number, index: number, text: string, firstSeq: number): void {
    if (generationId !== this.generationId || this.segments.some((segment) => segment.index === index)) return;
    this.segments.push({
      index,
      text,
      words: text.split(/\s+/).filter(Boolean),
      firstSeq,
      startMs: this.audibleAt.get(firstSeq) ?? null,
      receivedSamples: 0,
      totalSamples: null,
    });
    this.segments.sort((a, b) => a.index - b.index);
  }

  /** A PCM chunk arrived (not yet scheduled); counts toward its segment's length estimate. */
  onAudio(generationId: number, seq: number, sampleCount: number): void {
    if (generationId !== this.generationId) return;
    const owner = this.owner(seq);
    if (owner) owner.receivedSamples += sampleCount;
  }

  /** The scheduler put chunk `seq` on the audio clock; it will be audible at `audibleAtMs`. */
  onChunkScheduled(generationId: number, seq: number, audibleAtMs: number): void {
    if (generationId !== this.generationId) return;
    this.audibleAt.set(seq, audibleAtMs);
    const segment = this.segments.find((candidate) => candidate.firstSeq === seq);
    if (segment) segment.startMs = audibleAtMs;
  }

  onSegmentDone(generationId: number, index: number, samples: number): void {
    if (generationId !== this.generationId) return;
    const segment = this.segments.find((candidate) => candidate.index === index);
    if (segment) segment.totalSamples = samples;
  }

  /** Cancel: stop advancing; the frame stays at what had been heard at `nowMs`. */
  freeze(nowMs: number): void {
    this.frozenAt ??= nowMs;
  }

  /** What to show at `nowMs`, or null when nothing has started being spoken. */
  frame(nowMs: number): CaptionFrame | null {
    const at = this.frozenAt === null ? nowMs : Math.min(nowMs, this.frozenAt);
    let currentIndex = -1;
    this.segments.forEach((segment, i) => {
      if (segment.startMs !== null && segment.startMs <= at) currentIndex = i;
    });
    if (currentIndex < 0) return null;
    const current = this.segments[currentIndex];
    const durationMs = this.durationMs(current);
    const progress = durationMs > 0 ? Math.min(1, (at - (current.startMs as number)) / durationMs) : 1;
    return {
      previous: currentIndex > 0 ? this.segments[currentIndex - 1].text : null,
      words: current.words,
      revealed: progress >= 1 ? current.words.length : revealedWordCount(current.words, progress),
      frozen: this.frozenAt !== null,
    };
  }

  /** The segment a chunk belongs to: the latest one announced at or before `seq`. */
  private owner(seq: number): Segment | undefined {
    let owner: Segment | undefined;
    for (const segment of this.segments) {
      if (segment.firstSeq <= seq && (!owner || segment.firstSeq >= owner.firstSeq)) owner = segment;
    }
    return owner;
  }

  private durationMs(segment: Segment): number {
    if (segment.totalSamples !== null) return (segment.totalSamples / this.sampleRate) * 1000;
    const received = (segment.receivedSamples / this.sampleRate) * 1000;
    const spoken = (segment.text.length / ESTIMATE_CHARS_PER_S) * 1000;
    return Math.max(received, spoken);
  }
}

export function sameFrame(a: CaptionFrame | null, b: CaptionFrame | null): boolean {
  if (a === b) return true;
  if (!a || !b) return false;
  return a.previous === b.previous && a.words === b.words && a.revealed === b.revealed && a.frozen === b.frozen;
}

/**
 * Thin driver: calls `onFrame` from animation frames, but only when the visible caption changed.
 * Returns a stop function. The clock and frame scheduler are injectable so it runs under Node tests.
 */
export function runCaptionLoop(
  track: CaptionTrack,
  onFrame: (frame: CaptionFrame | null) => void,
  now: () => number = () => performance.now(),
  raf: (callback: () => void) => number = (callback) => requestAnimationFrame(callback),
  cancel: (handle: number) => void = (handle) => cancelAnimationFrame(handle),
): () => void {
  let last: CaptionFrame | null = null;
  let handle = 0;
  let stopped = false;
  const tick = () => {
    if (stopped) return;
    const next = track.frame(now());
    if (!sameFrame(last, next)) {
      last = next;
      onFrame(next);
    }
    handle = raf(tick);
  };
  handle = raf(tick);
  return () => {
    stopped = true;
    cancel(handle);
  };
}
