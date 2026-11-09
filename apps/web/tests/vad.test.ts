import assert from "node:assert/strict";
import { describe, it } from "node:test";
import { perfToEpochMs, VadCore, type VadEvent } from "../lib/audio/vadCore";

const FRAME = 20;
const SPEECH = 0.12;
const QUIET = 0.004;

/** Feeds `ms` of constant `level`, returns the events and the advanced clock. */
function feed(vad: VadCore, from: number, ms: number, level: number, playing = false) {
  const events: VadEvent[] = [];
  let t = from;
  for (let elapsed = 0; elapsed < ms; elapsed += FRAME) {
    const event = vad.push(level, t, playing);
    if (event) events.push(event);
    t += FRAME;
  }
  return { events, t };
}

describe("VadCore", () => {
  it("does not start before 120 ms of energy", () => {
    const vad = new VadCore();
    const { events } = feed(vad, 0, 100, SPEECH);
    assert.deepEqual(events, []);
    assert.equal(vad.isSpeaking, false);
  });

  it("starts once 120 ms of energy is confirmed", () => {
    const vad = new VadCore();
    const { events } = feed(vad, 0, 140, SPEECH);
    assert.equal(events.length, 1);
    assert.equal(events[0].type, "speechStarted");
    assert.ok(events[0].at <= 120);
    assert.equal((events[0] as { onsetAt: number }).onsetAt, 0);
  });

  it("ignores a click shorter than the start window", () => {
    const vad = new VadCore();
    const a = feed(vad, 0, 60, SPEECH);
    const b = feed(vad, a.t, 400, QUIET);
    assert.deepEqual([...a.events, ...b.events], []);
  });

  it("tolerates a brief dip inside the start window", () => {
    const vad = new VadCore();
    const a = feed(vad, 0, 60, SPEECH);
    const gap = feed(vad, a.t, 40, QUIET);
    const b = feed(vad, gap.t, 120, SPEECH);
    assert.equal([...a.events, ...gap.events, ...b.events].filter((e) => e.type === "speechStarted").length, 1);
  });

  it("ends after 250 ms of silence following real speech", () => {
    const vad = new VadCore();
    const talk = feed(vad, 0, 600, SPEECH);
    const early = feed(vad, talk.t, 200, QUIET);
    assert.deepEqual(early.events, []);
    const late = feed(vad, early.t, 100, QUIET);
    assert.equal(late.events.length, 1);
    const end = late.events[0];
    assert.equal(end.type, "speechEnded");
    assert.equal((end as { misfire: boolean }).misfire, false);
    assert.equal(vad.isSpeaking, false);
  });

  it("reports the last voiced frame, so the 250 ms hangover is separable from the true end", () => {
    const vad = new VadCore();
    const talk = feed(vad, 0, 600, SPEECH); // last voiced frame at t = 580
    const quiet = feed(vad, talk.t, 400, QUIET);
    const end = quiet.events[0] as { endedAt: number; lastVoicedAt: number };
    assert.equal(end.lastVoicedAt, 580);
    assert.ok(end.endedAt - end.lastVoicedAt >= 250);
    assert.ok(end.endedAt - end.lastVoicedAt < 250 + 20);
  });

  it("perceived latency counted from lastVoicedAt includes the hangover", () => {
    const vad = new VadCore();
    const talk = feed(vad, 0, 600, SPEECH);
    const quiet = feed(vad, talk.t, 400, QUIET);
    const end = quiet.events[0] as { endedAt: number; lastVoicedAt: number };
    const firstAudibleAt = end.endedAt + 900; // 900 ms of server + playback after the emission
    assert.ok(firstAudibleAt - end.lastVoicedAt >= 900 + 250);
  });

  it("flush reports the last voiced frame too", () => {
    const vad = new VadCore();
    feed(vad, 0, 400, SPEECH);
    const end = vad.flush(500);
    assert.equal(end?.lastVoicedAt, 380);
    assert.equal(end?.endedAt, 500);
  });

  it("converts perf timestamps to epoch ms", () => {
    assert.equal(perfToEpochMs(1000, 1250, 5_000_250), 5_000_000);
  });

  it("does not end on a short pause between words", () => {
    const vad = new VadCore();
    const events: VadEvent[] = [];
    let t = 0;
    for (let word = 0; word < 4; word++) {
      const talk = feed(vad, t, 300, SPEECH);
      const pause = feed(vad, talk.t, 200, QUIET);
      events.push(...talk.events, ...pause.events);
      t = pause.t;
    }
    assert.deepEqual(events.map((e) => e.type), ["speechStarted"]);
  });

  it("keeps speaking through mid-word energy dips above the end threshold", () => {
    const vad = new VadCore();
    feed(vad, 0, 300, SPEECH);
    const dip = feed(vad, 300, 600, vad.startThreshold(false) * 0.8);
    assert.deepEqual(dip.events, []);
    assert.equal(vad.isSpeaking, true);
  });

  it("flags speech shorter than 200 ms as a misfire but still closes it", () => {
    const vad = new VadCore();
    const talk = feed(vad, 0, 140, SPEECH);
    const quiet = feed(vad, talk.t, 400, QUIET);
    const end = quiet.events[0] as { type: string; misfire: boolean; speechMs: number };
    assert.equal(end.type, "speechEnded");
    assert.equal(end.misfire, true);
    assert.ok(end.speechMs < 200);
  });

  it("adapts to steady room noise: no endless utterance, no repeated starts", () => {
    const vad = new VadCore();
    const before = vad.startThreshold(false);
    const first = feed(vad, 0, 10_000, 0.02);
    assert.deepEqual(first.events.map((e) => e.type), ["speechStarted", "speechEnded"]);
    assert.equal(vad.isSpeaking, false);
    assert.ok(vad.startThreshold(false) > before);
    const later = feed(vad, first.t, 5_000, 0.02);
    assert.deepEqual(later.events, []);
    // Real speech still stands out from the raised floor.
    const speech = feed(vad, later.t, 300, 0.15);
    assert.equal(speech.events[0]?.type, "speechStarted");
  });

  it("real speech barely moves the noise floor", () => {
    const vad = new VadCore();
    feed(vad, 0, 5_000, 0.15);
    assert.ok(vad.noiseFloor < 0.01, `floor drifted to ${vad.noiseFloor}`);
  });

  it("falls back quickly when the room gets quiet again", () => {
    const vad = new VadCore();
    feed(vad, 0, 10_000, 0.02);
    const noisy = vad.noiseFloor;
    feed(vad, 10_000, 400, 0.002);
    assert.ok(vad.noiseFloor < noisy / 2);
  });

  it("does not learn the noise floor from assistant echo", () => {
    const vad = new VadCore();
    const floor = vad.noiseFloor;
    feed(vad, 0, 2000, 0.03, true);
    assert.equal(vad.noiseFloor, floor);
  });

  it("uses a higher threshold while the assistant plays", () => {
    const vad = new VadCore();
    assert.ok(vad.startThreshold(true) > vad.startThreshold(false));
    const echo = feed(vad, 0, 600, 0.03, true);
    assert.deepEqual(echo.events, [], "echo-level energy must not start speech during playback");
    const user = feed(vad, echo.t, 200, 0.2, true);
    assert.equal(user.events[0]?.type, "speechStarted");
  });

  it("flush closes an open utterance and is a no-op otherwise", () => {
    const vad = new VadCore();
    assert.equal(vad.flush(0), null);
    feed(vad, 0, 400, SPEECH);
    const end = vad.flush(400);
    assert.equal(end?.type, "speechEnded");
    assert.equal(vad.isSpeaking, false);
    assert.equal(vad.flush(420), null);
  });
});
