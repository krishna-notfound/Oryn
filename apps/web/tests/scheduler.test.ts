import assert from "node:assert/strict";
import { beforeEach, describe, it } from "node:test";
import { AudioScheduler } from "../lib/audio/AudioScheduler";
import { AdaptiveLead, audibleAtMs, DUCK_GAIN, gainFor, planChunk, RESTORE_RAMP_MS, SeqReorderBuffer } from "../lib/audio/schedulerCore";

// ---------------------------------------------------------------- pure logic

describe("SeqReorderBuffer", () => {
  it("passes in-order chunks straight through", () => {
    const buffer = new SeqReorderBuffer<string>();
    assert.deepEqual(buffer.push(0, "a"), ["a"]);
    assert.deepEqual(buffer.push(1, "b"), ["b"]);
  });

  it("holds one early chunk and releases both when the gap fills", () => {
    const buffer = new SeqReorderBuffer<string>();
    assert.deepEqual(buffer.push(1, "b"), []);
    assert.deepEqual(buffer.push(0, "a"), ["a", "b"]);
  });

  it("gives up on a missing chunk instead of stalling once more than one is held", () => {
    const buffer = new SeqReorderBuffer<string>();
    assert.deepEqual(buffer.push(2, "c"), []);
    assert.deepEqual(buffer.push(3, "d"), ["c", "d"]);
    assert.deepEqual(buffer.push(1, "late"), [], "the abandoned chunk is dropped when it finally shows up");
    assert.deepEqual(buffer.push(4, "e"), ["e"]);
  });

  it("drops duplicates and chunks older than the cursor", () => {
    const buffer = new SeqReorderBuffer<string>();
    buffer.push(0, "a");
    assert.deepEqual(buffer.push(0, "again"), []);
    buffer.push(2, "c");
    assert.deepEqual(buffer.push(2, "c2"), []);
  });

  it("flush returns held chunks in order and reset starts over", () => {
    const buffer = new SeqReorderBuffer<string>(3);
    buffer.push(5, "f");
    buffer.push(3, "d");
    assert.deepEqual(buffer.flush(), ["d", "f"]);
    buffer.reset();
    assert.deepEqual(buffer.push(0, "z"), ["z"]);
  });
});

describe("AdaptiveLead and planChunk", () => {
  it("starts at 40 ms, grows 20 ms per underrun and caps at 150 ms", () => {
    const lead = new AdaptiveLead();
    assert.equal(lead.millis, 40);
    const seen: number[] = [];
    for (let i = 0; i < 8; i++) {
      lead.onUnderrun();
      seen.push(lead.millis);
    }
    assert.deepEqual(seen, [60, 80, 100, 120, 140, 150, 150, 150]);
  });

  it("first chunk starts one lead ahead and is not an underrun", () => {
    const lead = new AdaptiveLead();
    const plan = planChunk(10, null, lead);
    assert.ok(Math.abs(plan.startAt - 10.04) < 1e-9);
    assert.equal(plan.underrun, false);
    assert.equal(lead.millis, 40);
  });

  it("schedules back to back while earlier audio is still ahead", () => {
    const lead = new AdaptiveLead();
    assert.deepEqual(planChunk(10, 10.2, lead), { startAt: 10.2, underrun: false });
  });

  it("an exhausted clock is an underrun: restart one (larger) lead ahead", () => {
    const lead = new AdaptiveLead();
    const plan = planChunk(10, 9.9, lead);
    assert.equal(plan.underrun, true);
    assert.ok(Math.abs(plan.startAt - 10.06) < 1e-9);
  });

  it("audible time = scheduled start + output latency, on the performance clock", () => {
    assert.ok(Math.abs(audibleAtMs(1000, 5, 5.04, 0.03) - 1070) < 1e-6);
  });
});

// ---------------------------------------------------------------- AudioScheduler on a fake context

class FakeSource {
  buffer: FakeBuffer | null = null;
  startedAt: number | null = null;
  stopped = false;
  onended: (() => void) | null = null;
  connect() {}
  disconnect() {}
  start(when: number) {
    this.startedAt = when;
  }
  stop() {
    this.stopped = true;
  }
}

class FakeBuffer {
  readonly data: Float32Array;
  constructor(
    readonly length: number,
    readonly rate: number,
  ) {
    this.data = new Float32Array(length);
  }
  get duration() {
    return this.length / this.rate;
  }
  getChannelData() {
    return this.data;
  }
}

/** AudioParam that records automation so tests can assert how the gain was changed. */
class FakeParam {
  value: number;
  readonly calls: string[] = [];
  constructor(initial: number) {
    this.value = initial;
  }
  cancelScheduledValues(at: number) {
    this.calls.push(`cancel@${at}`);
  }
  setValueAtTime(value: number, at: number) {
    this.calls.push(`set:${value}@${at}`);
    this.value = value;
  }
  linearRampToValueAtTime(value: number, at: number) {
    this.calls.push(`ramp:${value}@${at}`);
  }
}

class FakeContext {
  static last: FakeContext;
  currentTime = 0;
  outputLatency = 0.02;
  baseLatency = 0.01;
  state = "running";
  destination = {};
  readonly sources: FakeSource[] = [];
  closed = false;
  constructor(readonly options: { sampleRate?: number } = {}) {
    FakeContext.last = this;
  }
  get sampleRate() {
    return this.options.sampleRate ?? 48000;
  }
  createBuffer(_channels: number, length: number, rate: number) {
    return new FakeBuffer(length, rate);
  }
  createBufferSource() {
    const source = new FakeSource();
    this.sources.push(source);
    return source;
  }
  readonly gainParam = new FakeParam(1);
  createGain() {
    return { gain: this.gainParam, connect: (node: unknown) => node };
  }
  createAnalyser() {
    return { fftSize: 1024, connect: (node: unknown) => node, getFloatTimeDomainData() {} };
  }
  resume() {
    return Promise.resolve();
  }
  close() {
    this.closed = true;
    return Promise.resolve();
  }
}

const RATE = 24_000;
/** 100 ms of audio whose first sample carries `tag`, so tests can tell chunks apart. */
function chunk(tag: number, samples = 2400): Int16Array {
  const pcm = new Int16Array(samples);
  pcm[0] = tag;
  return pcm;
}
const tagOf = (source: FakeSource) => Math.round((source.buffer?.data[0] ?? 0) * 32768);

async function makeScheduler() {
  (globalThis as unknown as { AudioContext: unknown }).AudioContext = FakeContext;
  const scheduler = new AudioScheduler();
  await scheduler.init(RATE);
  return { scheduler, ctx: FakeContext.last };
}

describe("AudioScheduler", () => {
  let scheduler: AudioScheduler;
  let ctx: FakeContext;

  beforeEach(async () => {
    ({ scheduler, ctx } = await makeScheduler());
  });

  it("creates the context at the requested output sample rate", () => {
    assert.equal(ctx.sampleRate, RATE);
  });

  it("schedules the first chunk one lead ahead and the rest back to back", () => {
    scheduler.beginGeneration(1, RATE);
    scheduler.enqueue(1, 0, chunk(1));
    scheduler.enqueue(1, 1, chunk(2));
    const [a, b] = ctx.sources;
    assert.ok(Math.abs((a.startedAt ?? 0) - 0.04) < 1e-9);
    assert.ok(Math.abs((b.startedAt ?? 0) - 0.14) < 1e-9);
  });

  it("plays out-of-order chunks in seq order", () => {
    scheduler.beginGeneration(1, RATE);
    scheduler.enqueue(1, 1, chunk(11));
    assert.equal(ctx.sources.length, 0, "an early chunk is held, not played");
    scheduler.enqueue(1, 0, chunk(10));
    assert.deepEqual(ctx.sources.map(tagOf), [10, 11]);
    assert.ok((ctx.sources[0].startedAt ?? 0) < (ctx.sources[1].startedAt ?? 0));
  });

  it("reports the first audible time including output latency, once per generation", () => {
    const scheduled: { leadMs: number; audibleAtMs: number }[] = [];
    scheduler.events.on("scheduled", (event) => scheduled.push(event));
    scheduler.beginGeneration(1, RATE);
    const before = performance.now();
    scheduler.enqueue(1, 0, chunk(1));
    scheduler.enqueue(1, 1, chunk(2));
    assert.equal(scheduled.length, 1);
    assert.ok(Math.abs(scheduled[0].leadMs - 40) < 1e-6);
    // lead 40 ms + outputLatency 20 ms
    assert.ok(scheduled[0].audibleAtMs - before >= 59 && scheduled[0].audibleAtMs - before < 100);
    scheduler.stop();
  });

  it("drops chunks of a generation that was never begun or is not current", () => {
    assert.equal(scheduler.enqueue(1, 0, chunk(1)), false);
    scheduler.beginGeneration(2, RATE);
    assert.equal(scheduler.enqueue(1, 0, chunk(1)), false);
    assert.equal(ctx.sources.length, 0);
  });

  it("stop() is synchronous: sources silenced, buffers flushed, generation stale", () => {
    scheduler.beginGeneration(1, RATE);
    for (let seq = 0; seq < 5; seq++) scheduler.enqueue(1, seq, chunk(seq));
    scheduler.stop();
    assert.equal(scheduler.isActive, false);
    assert.equal(scheduler.generationId, null);
    assert.ok(ctx.sources.every((source) => source.stopped && source.onended === null));
    const scheduledBefore = ctx.sources.length;
    assert.equal(scheduler.enqueue(1, 5, chunk(5)), false, "late frame of the cancelled generation");
    assert.equal(scheduler.beginGeneration(1, RATE), false, "the server re-announcing it does not revive it");
    assert.equal(ctx.sources.length, scheduledBefore);
    assert.equal(scheduler.beginGeneration(2, RATE), true);
    assert.equal(scheduler.enqueue(2, 0, chunk(9)), true);
  });

  it("a stopped generation never emits finished or audible", () => {
    const events: string[] = [];
    scheduler.events.on("finished", () => events.push("finished"));
    scheduler.events.on("audible", () => events.push("audible"));
    scheduler.beginGeneration(1, RATE);
    scheduler.enqueue(1, 0, chunk(1));
    scheduler.markGenerationDone(1);
    const source = ctx.sources[0];
    scheduler.stop();
    source.onended?.();
    assert.deepEqual(events, []);
  });

  it("emits finished once, only after done AND every chunk ended", () => {
    const finished: number[] = [];
    scheduler.events.on("finished", (id) => finished.push(id));
    scheduler.beginGeneration(1, RATE);
    scheduler.enqueue(1, 0, chunk(1));
    scheduler.enqueue(1, 1, chunk(2));
    ctx.sources[0].onended?.();
    assert.deepEqual(finished, [], "not done yet");
    scheduler.markGenerationDone(1);
    assert.deepEqual(finished, [], "chunk 2 still sounding");
    ctx.sources[1].onended?.();
    assert.deepEqual(finished, [1]);
    assert.equal(scheduler.isActive, false);
  });

  it("a generation that produced no audio finishes silently", () => {
    const finished: number[] = [];
    scheduler.events.on("finished", (id) => finished.push(id));
    scheduler.beginGeneration(1, RATE);
    scheduler.markGenerationDone(1);
    assert.deepEqual(finished, []);
    assert.equal(scheduler.isActive, false);
  });

  it("grows the lead by 20 ms after an underrun", () => {
    scheduler.beginGeneration(1, RATE);
    scheduler.enqueue(1, 0, chunk(1));
    ctx.currentTime = 1; // the clock ran past the end of chunk 0
    scheduler.enqueue(1, 1, chunk(2));
    assert.equal(scheduler.leadMs, 60);
    assert.ok(Math.abs((ctx.sources[1].startedAt ?? 0) - 1.06) < 1e-9);
  });

  it("keeps the number of live nodes bounded and pumps the rest as audio plays", () => {
    scheduler.beginGeneration(1, RATE);
    for (let seq = 0; seq < 100; seq++) scheduler.enqueue(1, seq, chunk(seq));
    const live = () => ctx.sources.filter((source) => !source.stopped).length;
    assert.ok(ctx.sources.length < 25, `scheduled ${ctx.sources.length} nodes up front`);
    const first = ctx.sources.length;
    ctx.currentTime = 1;
    ctx.sources[0].onended?.();
    assert.ok(ctx.sources.length > first, "ending a source schedules more");
    assert.ok(live() > 0);
    let guard = 0;
    while (ctx.sources.some((source) => source.onended) && guard++ < 500) {
      ctx.currentTime += 0.1;
      ctx.sources.find((source) => source.onended && source.startedAt !== null && source.startedAt <= ctx.currentTime)?.onended?.();
      ctx.sources.forEach((source) => {
        if (source.startedAt !== null && source.startedAt + 0.1 <= ctx.currentTime) source.onended = null;
      });
    }
    assert.equal(ctx.sources.length, 100, "every queued chunk was eventually scheduled exactly once");
  });

  it("reconnect resets the scheduler: audio gone and stale marks forgotten", () => {
    scheduler.beginGeneration(5, RATE);
    scheduler.enqueue(5, 0, chunk(1));
    scheduler.stop(); // generation 5 is now retired
    assert.equal(scheduler.beginGeneration(3, RATE), false);
    scheduler.beginGeneration(6, RATE);
    scheduler.enqueue(6, 0, chunk(2));
    scheduler.reset(); // new socket
    assert.equal(scheduler.isActive, false);
    assert.ok(ctx.sources.every((source) => source.stopped));
    assert.equal(scheduler.beginGeneration(3, RATE), true, "ids restart on a new connection");
    assert.equal(scheduler.enqueue(3, 0, chunk(3)), true);
  });

  it("close releases the context", () => {
    scheduler.beginGeneration(1, RATE);
    scheduler.enqueue(1, 0, chunk(1));
    scheduler.close();
    assert.equal(ctx.closed, true);
    assert.equal(scheduler.enqueue(1, 1, chunk(2)), false);
  });
});

describe("gainFor", () => {
  it("mute wins over duck; duck is 20 %", () => {
    assert.equal(gainFor(false, false), 1);
    assert.equal(gainFor(false, true), DUCK_GAIN);
    assert.equal(DUCK_GAIN, 0.2);
    assert.equal(gainFor(true, true), 0);
    assert.equal(gainFor(true, false), 0);
  });
});

describe("AudioScheduler ducking", () => {
  let scheduler: AudioScheduler;
  let ctx: FakeContext;

  beforeEach(async () => {
    ({ scheduler, ctx } = await makeScheduler());
  });

  it("duck() steps the gain to 20 % at the current audio time, without stopping anything", () => {
    scheduler.beginGeneration(1, RATE);
    scheduler.enqueue(1, 0, chunk(1));
    ctx.currentTime = 0.5;
    ctx.gainParam.calls.length = 0;
    assert.equal(scheduler.duck(), true);
    assert.equal(scheduler.isDucked, true);
    assert.equal(ctx.gainParam.value, DUCK_GAIN);
    assert.deepEqual(ctx.gainParam.calls, ["cancel@0.5", "set:0.2@0.5"], "one immediate step, no ramp");
    assert.ok(ctx.sources.every((source) => !source.stopped));
    assert.equal(scheduler.isActive, true);
  });

  it("does nothing when no assistant audio is playing", () => {
    assert.equal(scheduler.duck(), false);
    assert.equal(scheduler.isDucked, false);
    assert.equal(ctx.gainParam.value, 1);
  });

  it("restore() ramps back to full volume over 150 ms", () => {
    scheduler.beginGeneration(1, RATE);
    scheduler.enqueue(1, 0, chunk(1));
    scheduler.duck();
    ctx.currentTime = 2;
    ctx.gainParam.calls.length = 0;
    scheduler.restore();
    assert.equal(scheduler.isDucked, false);
    assert.equal(RESTORE_RAMP_MS, 150);
    assert.deepEqual(ctx.gainParam.calls, ["cancel@2", "set:0.2@2", "ramp:1@2.15"]);
  });

  it("stays silent while the speaker is muted, even when ducking and restoring", () => {
    scheduler.beginGeneration(1, RATE);
    scheduler.enqueue(1, 0, chunk(1));
    scheduler.setMuted(true);
    scheduler.duck();
    assert.equal(ctx.gainParam.value, 0);
    scheduler.restore(0);
    assert.equal(ctx.gainParam.value, 0);
    scheduler.setMuted(false);
    assert.equal(ctx.gainParam.value, 1);
  });

  it("stop() and a new generation start at full volume again", () => {
    scheduler.beginGeneration(1, RATE);
    scheduler.enqueue(1, 0, chunk(1));
    scheduler.duck();
    scheduler.stop();
    assert.equal(scheduler.isDucked, false);
    assert.equal(ctx.gainParam.value, 1);

    scheduler.beginGeneration(2, RATE);
    scheduler.enqueue(2, 0, chunk(2));
    scheduler.duck();
    scheduler.beginGeneration(3, RATE);
    assert.equal(ctx.gainParam.value, 1, "a newer generation replaces the ducked one");
  });

  it("a generation that finished while ducked releases the duck", () => {
    scheduler.beginGeneration(1, RATE);
    scheduler.enqueue(1, 0, chunk(1));
    scheduler.duck();
    scheduler.markGenerationDone(1);
    ctx.sources[0].onended?.();
    assert.equal(scheduler.isDucked, false);
    assert.equal(ctx.gainParam.value, 1);
  });
});

describe("AudioScheduler chunk timing events", () => {
  it("reports every chunk's seq, audible time and duration as it is scheduled", async () => {
    const { scheduler } = await makeScheduler();
    const chunks: { seq: number; audibleAtMs: number; durationMs: number }[] = [];
    scheduler.events.on("chunk", (event) => chunks.push(event));
    scheduler.beginGeneration(1, RATE);
    scheduler.enqueue(1, 0, chunk(1));
    scheduler.enqueue(1, 1, chunk(2));
    assert.deepEqual(chunks.map((c) => c.seq), [0, 1]);
    assert.ok(chunks.every((c) => Math.abs(c.durationMs - 100) < 1e-6));
    assert.ok(Math.abs(chunks[1].audibleAtMs - chunks[0].audibleAtMs - 100) < 5, "back to back");
  });
});
