export interface BargeInOptions {
  /** Smoothed mic RMS in 0..1. */
  getMicLevel: () => number;
  isPlaybackActive: () => boolean;
  /** Fired once per burst of speech; the caller stops playback and tells the server. */
  onBargeIn: () => void;
  /** RMS above which the mic counts as speech (echo cancellation leaves a small residue). */
  threshold?: number;
  /** Speech must persist this long, which rejects clicks and echo residue. */
  holdMs?: number;
  pollMs?: number;
}

/**
 * Energy VAD on the mic level, armed only while assistant audio is playing.
 * Polls on a timer (not rAF) so it keeps working in a backgrounded tab.
 */
export class BargeInDetector {
  private readonly threshold: number;
  private readonly holdMs: number;
  private readonly pollMs: number;
  private timer: ReturnType<typeof setInterval> | null = null;
  private aboveSince: number | null = null;
  private fired = false;

  constructor(private readonly options: BargeInOptions) {
    this.threshold = options.threshold ?? 0.05;
    this.holdMs = options.holdMs ?? 150;
    this.pollMs = options.pollMs ?? 20;
  }

  start(): void {
    if (this.timer) return;
    this.timer = setInterval(() => this.poll(), this.pollMs);
  }

  stop(): void {
    if (this.timer) clearInterval(this.timer);
    this.timer = null;
    this.aboveSince = null;
    this.fired = false;
  }

  private poll(): void {
    if (!this.options.isPlaybackActive() || this.options.getMicLevel() < this.threshold) {
      this.aboveSince = null;
      this.fired = false;
      return;
    }
    const now = performance.now();
    this.aboveSince ??= now;
    if (!this.fired && now - this.aboveSince >= this.holdMs) {
      this.fired = true;
      this.options.onBargeIn();
    }
  }
}
