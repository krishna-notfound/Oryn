/** User speech this long over the assistant is a real interruption, not a "hmm" (spec §1). */
export const HARD_INTERRUPT_MS = 700;
/** How long to stay ducked after the user stops, waiting for the server's resume/cancel verdict. */
export const VERDICT_TIMEOUT_MS = 1500;
/** The user counts as still voicing at the check if a voiced frame is at most this old. */
const STILL_VOICED_MS = 80;
const RECHECK_MS = 40;

/** What interruption handling needs from the audio scheduler. */
export interface DuckablePlayback {
  readonly isActive: boolean;
  readonly generationId: number | null;
  duck(): boolean;
  restore(rampMs?: number): void;
  stop(): void;
}

export interface Clock {
  now(): number;
  setTimeout(callback: () => void, ms: number): unknown;
  clearTimeout(handle: unknown): void;
}

const SYSTEM_CLOCK: Clock = {
  now: () => performance.now(),
  setTimeout: (callback, ms) => setTimeout(callback, ms),
  clearTimeout: (handle) => clearTimeout(handle as ReturnType<typeof setTimeout>),
};

export interface InterruptionOptions {
  /** `performance.now()` of the latest voiced mic frame (the VAD's `lastVoicedAt`). */
  lastVoicedAt: () => number;
  /** Tells the server; always called after local playback was stopped. */
  cancelTurn: (generationId: number) => void;
  /** The ducked flag changed (drives the orb's "listening" look). */
  onDuckedChange?: (ducked: boolean) => void;
  clock?: Clock;
}

/**
 * Human-style overlap handling, local-first. The user starts talking over the assistant:
 *  - at once, duck playback (no stop) so a "hmm" does not cut the reply;
 *  - voiced speech reaching HARD_INTERRUPT_MS is a real interruption: stop playback, then cancel;
 *  - speech that ends sooner stays ducked until the server rules: `turn.resume` restores the
 *    volume, `turn.cancelled` stops, and silence for VERDICT_TIMEOUT_MS restores it anyway.
 * The hard-stop order is the contract: the user hears silence before any network round trip.
 */
export class InterruptionController {
  /** Last time from VAD detection to the duck being applied, ms. */
  lastDuckMs: number | null = null;

  private ducked = false;
  private generationId: number | null = null;
  private speaking = false;
  private hardTimer: unknown = null;
  private verdictTimer: unknown = null;
  private readonly clock: Clock;

  constructor(
    private readonly playback: DuckablePlayback,
    private readonly options: InterruptionOptions,
  ) {
    this.clock = options.clock ?? SYSTEM_CLOCK;
  }

  get isDucked(): boolean {
    return this.ducked;
  }

  /**
   * `detectedAt`: clock time of the VAD start event; `onsetAt`: first voiced frame of the
   * utterance. Returns true when assistant audio was (or already is) ducked for this speech.
   */
  onSpeechStarted(detectedAt: number, onsetAt: number): boolean {
    const generationId = this.playback.generationId;
    if (!this.playback.isActive || generationId === null) {
      this.reset();
      return false;
    }
    if (!this.ducked || generationId !== this.generationId) {
      this.playback.duck();
      this.lastDuckMs = this.clock.now() - detectedAt;
      this.generationId = generationId;
      this.setDucked(true);
    }
    this.speaking = true;
    this.clearTimer("verdictTimer");
    this.armHardCheck(Math.max(0, onsetAt + HARD_INTERRUPT_MS - this.clock.now()));
    return true;
  }

  /** `speechMs`: voiced length of the utterance that just ended. */
  onSpeechEnded(speechMs: number): void {
    if (!this.ducked) return;
    this.speaking = false;
    this.clearTimer("hardTimer");
    if (speechMs >= HARD_INTERRUPT_MS) {
      this.hardStop(); // the check timer was late (throttled tab)
      return;
    }
    this.verdictTimer = this.clock.setTimeout(() => {
      this.verdictTimer = null;
      this.settle(() => this.playback.restore());
    }, VERDICT_TIMEOUT_MS);
  }

  /** `turn.resume`: it was only a backchannel, carry on at full volume. */
  onResume(): void {
    if (this.ducked) this.settle(() => this.playback.restore());
  }

  /** `turn.cancelled` while ducked: the server decided it was a real interruption. */
  onCancelled(): void {
    if (this.ducked) this.settle(() => this.playback.stop());
  }

  /** The generation changed or ended: forget the duck without waiting for a verdict. */
  reset(): void {
    if (!this.ducked) return;
    this.settle(() => this.playback.restore(0));
  }

  private armHardCheck(delayMs: number): void {
    this.clearTimer("hardTimer");
    this.hardTimer = this.clock.setTimeout(() => {
      this.hardTimer = null;
      if (!this.ducked || !this.speaking) return;
      // The VAD's end event lags the voice by its silence hangover: only a user who is still
      // voicing now has spoken for the full duration. Otherwise wait for the end event.
      if (this.clock.now() - this.options.lastVoicedAt() <= STILL_VOICED_MS) this.hardStop();
      else this.armHardCheck(RECHECK_MS);
    }, delayMs);
  }

  private hardStop(): void {
    const generationId = this.generationId;
    this.settle(() => this.playback.stop());
    if (generationId !== null) this.options.cancelTurn(generationId);
  }

  private settle(apply: () => void): void {
    this.clearTimer("hardTimer");
    this.clearTimer("verdictTimer");
    this.speaking = false;
    apply();
    this.generationId = null;
    this.setDucked(false);
  }

  private clearTimer(name: "hardTimer" | "verdictTimer"): void {
    if (this[name] === null) return;
    this.clock.clearTimeout(this[name]);
    this[name] = null;
  }

  private setDucked(ducked: boolean): void {
    if (this.ducked === ducked) return;
    this.ducked = ducked;
    this.options.onDuckedChange?.(ducked);
  }
}
