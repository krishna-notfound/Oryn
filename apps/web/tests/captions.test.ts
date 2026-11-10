import assert from "node:assert/strict";
import { beforeEach, describe, it } from "node:test";
import { CaptionTrack, revealedWordCount, runCaptionLoop, type CaptionFrame } from "../lib/captions/CaptionTrack";

const RATE = 24_000;
/** Samples for `ms` of audio. */
const samples = (ms: number) => Math.round((ms / 1000) * RATE);

describe("revealedWordCount", () => {
  it("shows the first word the moment the voice starts and all of them at the end", () => {
    assert.equal(revealedWordCount(["a", "bb", "ccc"], 0), 1);
    assert.equal(revealedWordCount(["a", "bb", "ccc"], 1), 3);
    assert.equal(revealedWordCount([], 0.5), 0);
  });

  it("weights words by character length, not by count", () => {
    // weights (len+1): hi=3, extraordinarily=16 -> the long word starts at 3/19 of the segment
    const words = ["hi", "extraordinarily"];
    assert.equal(revealedWordCount(words, 0.1), 1);
    assert.equal(revealedWordCount(words, 0.2), 2);
    // equal-length words split evenly
    assert.equal(revealedWordCount(["aaaa", "bbbb", "cccc", "dddd"], 0.49), 2);
    assert.equal(revealedWordCount(["aaaa", "bbbb", "cccc", "dddd"], 0.5), 3);
  });
});

describe("CaptionTrack", () => {
  let track: CaptionTrack;

  beforeEach(() => {
    track = new CaptionTrack();
    track.begin(1, RATE);
  });

  /** Segment of ten 4-letter words = 50 chars, announced and scheduled, lasting `ms` once done. */
  function speak(index: number, firstSeq: number, startMs: number, ms: number, text = "aaaa bbbb cccc dddd eeee ffff gggg hhhh iiii jjjj") {
    track.onSegment(1, index, text, firstSeq);
    track.onChunkScheduled(1, firstSeq, startMs);
    track.onSegmentDone(1, index, samples(ms));
  }

  it("shows nothing until the first chunk of a segment is audible", () => {
    track.onSegment(1, 0, "hello world", 0);
    assert.equal(track.frame(5000), null, "announced but not scheduled");
    track.onChunkScheduled(1, 0, 1000);
    assert.equal(track.frame(999), null, "scheduled but not yet audible");
    assert.deepEqual(track.frame(1000), { previous: null, words: ["hello", "world"], revealed: 1, frozen: false });
  });

  it("advances word by word across samples / sample_rate", () => {
    speak(0, 0, 1000, 2000);
    const at = (ms: number) => track.frame(ms)?.revealed;
    assert.equal(at(1000), 1);
    assert.equal(at(1190), 1);
    assert.equal(at(1210), 2, "10 equal words: one every 10 % of 2 s");
    assert.equal(at(2010), 6);
    assert.equal(at(2990), 10);
    assert.equal(at(3000), 10);
    assert.equal(at(9000), 10, "fully revealed segment stays until the next one starts");
  });

  it("uses the scheduled start of first_seq even when the chunk was scheduled before the segment arrived", () => {
    track.onChunkScheduled(1, 3, 4000);
    track.onSegment(1, 1, "late announce here", 3);
    assert.equal(track.frame(3999), null);
    assert.equal(track.frame(4000)?.words.length, 3);
  });

  it("makes the current segment the latest started one and the one before it the faded previous", () => {
    speak(0, 0, 1000, 1000, "first sentence here");
    speak(1, 5, 2000, 1000, "second one");
    speak(2, 9, 3000, 1000, "third");
    assert.equal(track.frame(1500)?.previous, null);
    const second = track.frame(2100) as CaptionFrame;
    assert.equal(second.previous, "first sentence here");
    assert.deepEqual(second.words, ["second", "one"]);
    const third = track.frame(3500) as CaptionFrame;
    assert.equal(third.previous, "second one");
    assert.equal(third.revealed, 1);
    assert.equal(track.frame(2100)?.previous, "first sentence here", "time can be asked in any order");
  });

  it("estimates the length from received bytes until segment_done arrives", () => {
    track.onSegment(1, 0, "aaaa bbbb cccc dddd", 0); // 19 chars
    track.onChunkScheduled(1, 0, 0);
    // 1 s of audio received so far, floor from text is 19/14 = 1.36 s
    track.onAudio(1, 0, samples(500));
    track.onAudio(1, 1, samples(500));
    const midway = track.frame(1000) as CaptionFrame;
    assert.ok(midway.revealed < 4, "text-rate floor keeps an unfinished segment from reading as done early");
    // more audio arrives: 3 s received -> estimate follows the audio
    track.onAudio(1, 2, samples(2000));
    assert.ok((track.frame(1400) as CaptionFrame).revealed <= 2);
    // the exact length arrives and wins
    track.onSegmentDone(1, 0, samples(1000));
    assert.equal((track.frame(1000) as CaptionFrame).revealed, 4);
  });

  it("counts chunks toward the segment whose first_seq is the latest one at or before the chunk", () => {
    track.onSegment(1, 0, "x".repeat(70), 0); // floor 5 s
    track.onSegment(1, 1, "y".repeat(70), 4);
    track.onChunkScheduled(1, 0, 0);
    track.onAudio(1, 0, samples(9000));
    track.onAudio(1, 4, samples(1000)); // belongs to segment 1
    track.onChunkScheduled(1, 4, 100_000);
    // segment 0 estimate = max(9 s received, 5 s text): progress at 4.5 s is exactly half
    const frame = track.frame(4500) as CaptionFrame;
    assert.equal(frame.words.length, 1);
  });

  it("freezes at the heard position on cancel and stays there", () => {
    speak(0, 0, 1000, 2000);
    assert.equal(track.frame(1650)?.revealed, 4);
    track.freeze(1650);
    const frozen = track.frame(1650) as CaptionFrame;
    assert.equal(frozen.frozen, true);
    assert.equal(frozen.revealed, 4);
    assert.equal((track.frame(2500) as CaptionFrame).revealed, 4, "later time does not advance it");
    assert.equal((track.frame(60_000) as CaptionFrame).revealed, 4);
    track.freeze(2500);
    assert.equal((track.frame(60_000) as CaptionFrame).revealed, 4, "a second freeze does not move it");
  });

  it("does not show a segment that would only have started after the freeze", () => {
    speak(0, 0, 1000, 1000, "one two");
    speak(1, 4, 2000, 1000, "three four");
    track.freeze(1500);
    assert.deepEqual((track.frame(5000) as CaptionFrame).words, ["one", "two"]);
  });

  it("keeps advancing while ducked and after a resume (nothing pauses the voice)", () => {
    speak(0, 0, 0, 1000);
    const seen = [100, 300, 500, 700, 900].map((ms) => (track.frame(ms) as CaptionFrame).revealed);
    assert.deepEqual([...seen].sort((a, b) => a - b), seen, "monotonic");
    assert.equal(track.frame(900)?.frozen, false);
  });

  it("a new generation clears the captions; stale generations are ignored", () => {
    speak(0, 0, 0, 1000);
    track.begin(2, RATE);
    assert.equal(track.hasSegments, false);
    assert.equal(track.frame(500), null);
    track.onSegment(1, 0, "stale words", 0);
    track.onChunkScheduled(1, 0, 0);
    assert.equal(track.hasSegments, false);
    track.onSegment(2, 0, "fresh", 0);
    track.onChunkScheduled(2, 0, 0);
    assert.deepEqual(track.frame(10)?.words, ["fresh"]);
  });

  it("begin() of the same generation keeps its captions; reset() drops them and unfreezes", () => {
    speak(0, 0, 0, 1000);
    track.freeze(500);
    track.begin(1, RATE);
    assert.equal(track.frame(900)?.frozen, true);
    track.reset();
    assert.equal(track.hasSegments, false);
    track.begin(3, RATE);
    track.onSegment(3, 0, "a b", 0);
    track.onChunkScheduled(3, 0, 0);
    assert.equal(track.frame(10)?.frozen, false);
  });

  it("ignores a duplicate segment index", () => {
    track.onSegment(1, 0, "one", 0);
    track.onSegment(1, 0, "other", 0);
    track.onChunkScheduled(1, 0, 0);
    assert.deepEqual(track.frame(1)?.words, ["one"]);
  });
});

describe("runCaptionLoop", () => {
  it("emits a frame only when what is on screen changes, and stops when asked", () => {
    const track = new CaptionTrack();
    track.begin(1, RATE);
    track.onSegment(1, 0, "aaaa bbbb cccc dddd", 0);
    track.onChunkScheduled(1, 0, 0);
    track.onSegmentDone(1, 0, samples(1000));

    let time = 0;
    let queued: (() => void) | null = null;
    const frames: (number | null)[] = [];
    const stop = runCaptionLoop(
      track,
      (frame) => frames.push(frame ? frame.revealed : null),
      () => time,
      (callback) => {
        queued = callback;
        return 1;
      },
      () => {
        queued = null;
      },
    );
    const step = (ms: number) => {
      time = ms;
      (queued as (() => void) | null)?.();
    };
    step(0);
    step(10);
    step(20); // nothing new
    step(300);
    step(310);
    step(1000);
    assert.deepEqual(frames, [1, 2, 4]);
    stop();
    assert.equal(queued, null);
    step(1500);
    assert.deepEqual(frames, [1, 2, 4]);
  });
});
