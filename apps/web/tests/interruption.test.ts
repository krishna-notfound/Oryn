import assert from "node:assert/strict";
import { beforeEach, describe, it } from "node:test";
import {
  HARD_INTERRUPT_MS,
  InterruptionController,
  VERDICT_TIMEOUT_MS,
  type Clock,
  type DuckablePlayback,
} from "../lib/audio/InterruptionController";

/** Manual clock: timers fire only when the test advances time. */
class FakeClock implements Clock {
  private time = 0;
  private nextId = 1;
  private readonly timers = new Map<number, { at: number; callback: () => void }>();

  now() {
    return this.time;
  }
  setTimeout(callback: () => void, ms: number) {
    const id = this.nextId++;
    this.timers.set(id, { at: this.time + ms, callback });
    return id;
  }
  clearTimeout(handle: unknown) {
    this.timers.delete(handle as number);
  }
  get pending() {
    return this.timers.size;
  }
  advance(ms: number) {
    const target = this.time + ms;
    for (;;) {
      const due = [...this.timers.entries()].filter(([, t]) => t.at <= target).sort((a, b) => a[1].at - b[1].at)[0];
      if (!due) break;
      this.timers.delete(due[0]);
      this.time = due[1].at;
      due[1].callback();
    }
    this.time = target;
  }
}

class FakePlayback implements DuckablePlayback {
  isActive = true;
  generationId: number | null = 7;
  log: string[] = [];
  duck() {
    this.log.push("duck");
    return true;
  }
  restore(rampMs?: number) {
    this.log.push(rampMs === undefined ? "restore" : `restore:${rampMs}`);
  }
  stop() {
    this.log.push("stop");
    this.isActive = false;
  }
}

describe("InterruptionController", () => {
  let clock: FakeClock;
  let playback: FakePlayback;
  let sent: string[];
  /** The VAD's latest voiced frame; tests move it to simulate the user talking or pausing. */
  let lastVoicedAt: number;
  let ducked: boolean[];
  let controller: InterruptionController;

  beforeEach(() => {
    clock = new FakeClock();
    playback = new FakePlayback();
    sent = [];
    ducked = [];
    lastVoicedAt = 0;
    controller = new InterruptionController(playback, {
      clock,
      lastVoicedAt: () => lastVoicedAt,
      cancelTurn: (generationId) => sent.push(`cancel:${generationId}:log=${playback.log.join(",")}`),
      onDuckedChange: (value) => ducked.push(value),
    });
  });

  /** The user keeps voicing until `untilMs` (frames every 20 ms). */
  function talk(untilMs: number) {
    while (clock.now() < untilMs) {
      clock.advance(20);
      lastVoicedAt = clock.now();
    }
  }

  it("ducks on the very first speech edge, without stopping or cancelling", () => {
    clock.advance(1000);
    assert.equal(controller.onSpeechStarted(1000, 880), true);
    assert.deepEqual(playback.log, ["duck"]);
    assert.deepEqual(sent, []);
    assert.deepEqual(ducked, [true]);
    assert.equal(controller.lastDuckMs, 0);
  });

  it("does nothing when no assistant audio is playing", () => {
    playback.isActive = false;
    assert.equal(controller.onSpeechStarted(0, 0), false);
    assert.deepEqual(playback.log, []);
    assert.equal(clock.pending, 0);
    playback.isActive = true;
    playback.generationId = null;
    assert.equal(controller.onSpeechStarted(0, 0), false);
    assert.deepEqual(playback.log, []);
  });

  it("speech that holds for 700 ms from onset: stop locally FIRST, then turn.cancel", () => {
    clock.advance(120);
    controller.onSpeechStarted(120, 0); // VAD confirms 120 ms after the onset
    talk(HARD_INTERRUPT_MS - 20);
    assert.deepEqual(sent, [], "not yet 700 ms");
    talk(HARD_INTERRUPT_MS + 20);
    assert.deepEqual(playback.log, ["duck", "stop"]);
    assert.deepEqual(sent, ["cancel:7:log=duck,stop"], "silence already happened when the cancel went out");
    assert.equal(controller.isDucked, false);
    assert.deepEqual(ducked, [true, false]);
  });

  it("hard-stops at exactly onset + 700 ms for a user who is still voicing", () => {
    clock.advance(120);
    controller.onSpeechStarted(120, 0);
    lastVoicedAt = 120;
    clock.advance(579);
    lastVoicedAt = clock.now();
    assert.deepEqual(sent, []);
    clock.advance(1);
    lastVoicedAt = 700;
    assert.deepEqual(playback.log, ["duck", "stop"]);
  });

  it("a 450 ms 'haan' whose VAD end lags by the hangover is NOT a hard interrupt", () => {
    clock.advance(120);
    controller.onSpeechStarted(120, 0);
    talk(450); // voice stops at 450 ms; VAD still waits out its 250 ms hangover
    clock.advance(250); // -> 700 ms: the timer fires but the user stopped voicing
    assert.deepEqual(sent, []);
    assert.deepEqual(playback.log, ["duck"]);
    controller.onSpeechEnded(450);
    clock.advance(10_000);
    assert.deepEqual(sent, []);
  });

  it("short speech stays ducked until the server rules: turn.resume restores", () => {
    controller.onSpeechStarted(0, 0);
    talk(300);
    controller.onSpeechEnded(300);
    clock.advance(800);
    assert.deepEqual(playback.log, ["duck"], "still ducked while waiting");
    controller.onResume();
    assert.deepEqual(playback.log, ["duck", "restore"]);
    assert.equal(controller.isDucked, false);
    clock.advance(VERDICT_TIMEOUT_MS * 2);
    assert.deepEqual(playback.log, ["duck", "restore"], "the verdict timer was cleared");
    assert.deepEqual(sent, []);
  });

  it("turn.cancelled while ducked stops playback and clears the timers", () => {
    controller.onSpeechStarted(0, 0);
    talk(300);
    controller.onSpeechEnded(300);
    controller.onCancelled();
    assert.deepEqual(playback.log, ["duck", "stop"]);
    assert.equal(clock.pending, 0);
    assert.deepEqual(sent, [], "the server already cancelled; the client must not cancel again");
  });

  it("no verdict within 1500 ms of the speech end: restore the volume", () => {
    controller.onSpeechStarted(0, 0);
    talk(300);
    controller.onSpeechEnded(300);
    clock.advance(VERDICT_TIMEOUT_MS - 1);
    assert.deepEqual(playback.log, ["duck"]);
    clock.advance(1);
    assert.deepEqual(playback.log, ["duck", "restore"]);
    assert.equal(controller.isDucked, false);
  });

  it("speaking again before the verdict cancels the wait and re-arms the 700 ms check", () => {
    controller.onSpeechStarted(0, 0);
    talk(300);
    controller.onSpeechEnded(300);
    clock.advance(1000);
    controller.onSpeechStarted(clock.now(), clock.now()); // second burst
    assert.deepEqual(playback.log, ["duck"], "already ducked: no second duck");
    talk(clock.now() + HARD_INTERRUPT_MS + 40);
    assert.deepEqual(playback.log, ["duck", "stop"]);
    assert.equal(sent.length, 1);
  });

  it("a late timer is backed up by the VAD end event reporting >= 700 ms", () => {
    controller.onSpeechStarted(0, 0);
    controller.onSpeechEnded(900);
    assert.deepEqual(playback.log, ["duck", "stop"]);
    assert.equal(sent.length, 1);
  });

  it("reset() (new generation or playback finished) restores instantly and cancels nothing", () => {
    controller.onSpeechStarted(0, 0);
    controller.onSpeechEnded(300);
    controller.reset();
    assert.deepEqual(playback.log, ["duck", "restore:0"]);
    assert.equal(clock.pending, 0);
    assert.deepEqual(sent, []);
    controller.reset();
    assert.deepEqual(playback.log, ["duck", "restore:0"], "idempotent");
  });

  it("ignores resume and cancel when nothing is ducked", () => {
    controller.onResume();
    controller.onCancelled();
    controller.onSpeechEnded(100);
    assert.deepEqual(playback.log, []);
  });

  it("speech that began when the generation changed ducks the new one", () => {
    controller.onSpeechStarted(0, 0);
    playback.generationId = 8;
    controller.onSpeechStarted(0, 0);
    assert.deepEqual(playback.log, ["duck", "duck"]);
  });
});
