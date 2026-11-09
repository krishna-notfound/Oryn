/**
 * Energy VAD state machine (spec §1). Pure and clock-agnostic: the caller passes the frame
 * level (RMS 0..1) and a timestamp in ms, so it is deterministic under test.
 *
 * - speechStarted after `startMs` of (nearly) continuous energy above the start threshold.
 * - speechEnded after `endSilenceMs` below the end threshold (start threshold * endRatio, a
 *   hysteresis that stops mid-word dips ending the utterance). A segment shorter than
 *   `minSpeechMs` is still closed so the server never waits on an end that never comes, but is
 *   flagged `misfire` so the app does not treat it as a turn.
 * - The threshold follows an adaptive noise floor and is raised while the assistant is playing,
 *   so echo that survives the browser's AEC is not mistaken for the user.
 */
export interface VadConfig {
  startMs: number;
  endSilenceMs: number;
  minSpeechMs: number;
  /** Duration one pushed frame covers. */
  frameMs: number;
  /** Energy dips shorter than this do not reset a pending start (plosives, word gaps). */
  startGapMs: number;
  /** Start threshold = noise floor * thresholdRatio, clamped to [minThreshold, maxThreshold]. */
  thresholdRatio: number;
  minThreshold: number;
  maxThreshold: number;
  /** While the assistant plays: at least this, and at least idle threshold * playingBoost. */
  playingMinThreshold: number;
  playingBoost: number;
  endRatio: number;
  initialFloor: number;
  maxFloor: number;
}

export const DEFAULT_VAD_CONFIG: VadConfig = {
  startMs: 120,
  endSilenceMs: 250,
  minSpeechMs: 200,
  frameMs: 20,
  startGapMs: 60,
  thresholdRatio: 3,
  minThreshold: 0.015,
  maxThreshold: 0.2,
  playingMinThreshold: 0.045,
  playingBoost: 2,
  endRatio: 0.7,
  initialFloor: 0.005,
  maxFloor: 0.05,
};

export interface VadStarted {
  type: "speechStarted";
  /** Time of the frame that confirmed the start. */
  at: number;
  /** Time of the first frame of the utterance. */
  onsetAt: number;
}

export interface VadEnded {
  type: "speechEnded";
  /** Time of the frame that confirmed the end (silence has lasted `endSilenceMs`). */
  at: number;
  speechMs: number;
  /** Shorter than `minSpeechMs`: noise, cough, echo blip. */
  misfire: boolean;
}

export type VadEvent = VadStarted | VadEnded;

const FLOOR_FALL = 0.3;
const FLOOR_RISE = 0.02;
/**
 * While a candidate/utterance is open the floor still creeps up, but only from frames that are
 * not clearly speech and only slowly. Without it, steady room noise just above the threshold
 * would read as one endless utterance; with it the noise is absorbed within a few seconds while
 * real speech (far above the cutoff) barely moves the floor.
 */
const FLOOR_RISE_ACTIVE = 0.004;
const ACTIVE_LEARN_CUTOFF_RATIO = 4;
const ACTIVE_LEARN_CUTOFF_MIN = 0.01;

export class VadCore {
  private readonly config: VadConfig;
  private floor: number;
  private speaking = false;
  private candidateAt: number | null = null;
  private lastAboveAt = 0;
  private onsetAt = 0;
  private lastVoiceAt = 0;

  constructor(config: Partial<VadConfig> = {}) {
    this.config = { ...DEFAULT_VAD_CONFIG, ...config };
    this.floor = this.config.initialFloor;
  }

  get isSpeaking(): boolean {
    return this.speaking;
  }

  get noiseFloor(): number {
    return this.floor;
  }

  /** Level needed to start speech right now. */
  startThreshold(assistantPlaying: boolean): number {
    const c = this.config;
    const idle = Math.min(c.maxThreshold, Math.max(c.minThreshold, this.floor * c.thresholdRatio));
    if (!assistantPlaying) return idle;
    return Math.min(c.maxThreshold, Math.max(c.playingMinThreshold, idle * c.playingBoost));
  }

  push(level: number, now: number, assistantPlaying: boolean): VadEvent | null {
    const c = this.config;
    const start = this.startThreshold(assistantPlaying);

    if (!this.speaking) {
      if (level >= start) {
        if (!assistantPlaying) this.learnFloor(level, true);
        this.candidateAt ??= now;
        this.lastAboveAt = now;
        if (now - this.candidateAt + c.frameMs >= c.startMs) {
          this.speaking = true;
          this.onsetAt = this.candidateAt;
          this.lastVoiceAt = now;
          this.candidateAt = null;
          return { type: "speechStarted", at: now, onsetAt: this.onsetAt };
        }
        return null;
      }
      if (this.candidateAt !== null && now - this.lastAboveAt > c.startGapMs) this.candidateAt = null;
      // Echo must not pull the floor up, so nothing is learned while the assistant plays.
      if (!assistantPlaying) this.learnFloor(level, this.candidateAt !== null);
      return null;
    }

    if (!assistantPlaying) this.learnFloor(level, true);
    if (level >= start * c.endRatio) {
      this.lastVoiceAt = now;
      return null;
    }
    if (now - this.lastVoiceAt >= c.endSilenceMs) return this.finish(now);
    return null;
  }

  /** Closes an open utterance immediately (mute, teardown). */
  flush(now: number): VadEnded | null {
    return this.speaking ? this.finish(now) : null;
  }

  reset(): void {
    this.speaking = false;
    this.candidateAt = null;
  }

  private finish(now: number): VadEnded {
    this.speaking = false;
    const speechMs = this.lastVoiceAt - this.onsetAt + this.config.frameMs;
    return { type: "speechEnded", at: now, speechMs, misfire: speechMs < this.config.minSpeechMs };
  }

  private learnFloor(level: number, active: boolean): void {
    let alpha = FLOOR_FALL;
    if (level >= this.floor) {
      if (!active) alpha = FLOOR_RISE;
      else if (level < this.floor * ACTIVE_LEARN_CUTOFF_RATIO + ACTIVE_LEARN_CUTOFF_MIN) alpha = FLOOR_RISE_ACTIVE;
      else return;
    }
    this.floor = Math.min(this.config.maxFloor, this.floor + (level - this.floor) * alpha);
  }
}
