/** What barge-in needs from the audio scheduler. */
export interface BargeInPlayback {
  readonly isActive: boolean;
  readonly generationId: number | null;
  stop(): void;
}

/**
 * Local-first barge-in. The VAD says the user started talking; if assistant audio is in flight we
 * silence it right here, synchronously, and only then tell the server. The order is the contract:
 * the user must hear silence without waiting for a network round trip.
 */
export class BargeInDetector {
  /** Last time from VAD detection to playback being stopped, ms (the "interrupt_stop_ms" metric). */
  lastStopMs: number | null = null;

  constructor(
    private readonly playback: BargeInPlayback,
    private readonly cancelTurn: (generationId: number) => void,
    private readonly clock: () => number = () => performance.now(),
  ) {}

  /** `detectedAt` is the clock time of the VAD event. Returns true when playback was interrupted. */
  onSpeechStarted(detectedAt: number): boolean {
    if (!this.playback.isActive) return false;
    const generationId = this.playback.generationId;
    this.playback.stop();
    this.lastStopMs = this.clock() - detectedAt;
    if (generationId !== null) this.cancelTurn(generationId);
    return true;
  }
}
