/** Wire format the backend expects: PCM s16le mono, 20 ms frames. */
export const MIC_SAMPLE_RATE = 16_000;
export const MIC_FRAME_BYTES = 640;

const LEVEL_RELEASE = 0.85;

interface WorkletFrame {
  pcm: ArrayBuffer;
  level: number;
}

/**
 * Captures the microphone and hands out 16 kHz mono PCM s16le frames of 20 ms.
 * Echo cancellation, noise suppression and AGC are requested from the browser.
 */
export class MicCapture {
  private context: AudioContext | null = null;
  private stream: MediaStream | null = null;
  private node: AudioWorkletNode | null = null;
  private smoothedLevel = 0;
  private muted = false;

  /** `level` is the frame's RMS in 0..1, computed in the worklet before quantisation. */
  constructor(private readonly onFrame: (pcm: ArrayBuffer, level: number) => void) {}

  /** Smoothed RMS input level in 0..1 (fast attack, ~80 ms release). */
  get level(): number {
    return this.smoothedLevel;
  }

  async start(): Promise<void> {
    if (this.context) return;

    const stream = await navigator.mediaDevices.getUserMedia({
      audio: {
        channelCount: 1,
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true,
      },
    });
    this.stream = stream;

    try {
      const context = new AudioContext({ latencyHint: "interactive" });
      this.context = context;
      await context.audioWorklet.addModule("/mic-worklet.js");
      if (context.state === "suspended") await context.resume();

      const source = context.createMediaStreamSource(stream);
      const node = new AudioWorkletNode(context, "pcm-downsampler", {
        numberOfInputs: 1,
        numberOfOutputs: 1,
        outputChannelCount: [1],
      });
      node.port.onmessage = (message: MessageEvent<WorkletFrame>) => this.onWorkletFrame(message.data);

      // The graph only runs while connected to the destination; a zero-gain sink keeps it
      // alive without playing the mic back.
      const sink = context.createGain();
      sink.gain.value = 0;
      source.connect(node).connect(sink).connect(context.destination);
      this.node = node;
    } catch (error) {
      this.stop();
      throw error;
    }
  }

  /** Muting silences the track and stops frames being sent. */
  setMuted(muted: boolean): void {
    this.muted = muted;
    this.stream?.getAudioTracks().forEach((track) => (track.enabled = !muted));
    if (muted) this.smoothedLevel = 0;
  }

  /** Releases the microphone and the audio graph. Safe to call repeatedly. */
  stop(): void {
    if (this.node) this.node.port.onmessage = null;
    this.node?.disconnect();
    this.node = null;
    this.stream?.getTracks().forEach((track) => track.stop());
    this.stream = null;
    void this.context?.close().catch(() => undefined);
    this.context = null;
    this.smoothedLevel = 0;
  }

  private onWorkletFrame({ pcm, level }: WorkletFrame): void {
    if (this.muted) return;
    this.smoothedLevel = Math.max(level, this.smoothedLevel * LEVEL_RELEASE);
    this.onFrame(pcm, level);
  }
}
