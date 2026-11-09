/**
 * AudioWorklet: downsamples the mic stream to 16 kHz mono PCM s16le and posts 20 ms frames
 * (320 samples = 640 bytes) to the main thread together with the frame RMS level.
 *
 * Resampling is an exact box filter (area average), which is also a sufficient anti-alias
 * low-pass for speech when going from 44.1/48 kHz down to 16 kHz.
 */
const TARGET_RATE = 16000;
const FRAME_SAMPLES = 320;

class PcmDownsampler extends AudioWorkletProcessor {
  constructor() {
    super();
    this.ratio = sampleRate / TARGET_RATE; // input samples per output sample
    this.remaining = this.ratio; // input weight still needed for the current output sample
    this.acc = 0;
    this.frame = new Int16Array(FRAME_SAMPLES);
    this.filled = 0;
    this.sumSquares = 0;
  }

  process(inputs) {
    const channel = inputs[0] && inputs[0][0];
    if (!channel) return true;

    for (let i = 0; i < channel.length; i++) {
      let weight = 1;
      const x = channel[i];
      while (weight > 1e-9) {
        const take = Math.min(weight, this.remaining);
        this.acc += x * take;
        this.remaining -= take;
        weight -= take;
        if (this.remaining <= 1e-9) {
          this.pushSample(this.acc / this.ratio);
          this.acc = 0;
          this.remaining = this.ratio;
        }
      }
    }
    return true;
  }

  pushSample(value) {
    const clamped = value > 1 ? 1 : value < -1 ? -1 : value;
    this.frame[this.filled++] = clamped < 0 ? clamped * 0x8000 : clamped * 0x7fff;
    this.sumSquares += clamped * clamped;
    if (this.filled === FRAME_SAMPLES) {
      const level = Math.sqrt(this.sumSquares / FRAME_SAMPLES);
      const pcm = this.frame.buffer;
      this.port.postMessage({ pcm, level }, [pcm]);
      this.frame = new Int16Array(FRAME_SAMPLES);
      this.filled = 0;
      this.sumSquares = 0;
    }
  }
}

registerProcessor("pcm-downsampler", PcmDownsampler);
