import assert from "node:assert/strict";
import { describe, it } from "node:test";
import { admitEvent, GenerationGate } from "../lib/realtime/GenerationGate";
import { parseAudioFrame, parseServerEvent, type ServerEvent } from "../lib/realtime/protocol";

const base = { v: 2, session_id: "s1", seq: 1, ts: 1 };
const json = (extra: object) => JSON.stringify({ ...base, ...extra });
const parse = (extra: object) => parseServerEvent(json(extra)) as ServerEvent;

describe("parseServerEvent", () => {
  it("accepts v2 events and keeps envelope + payload", () => {
    const event = parse({ type: "text.delta", generation_id: 3, turn_id: 2, delta: "hi" });
    assert.equal(event.type, "text.delta");
    assert.equal((event as { delta: string }).delta, "hi");
    assert.equal(event.generation_id, 3);
  });

  it("rejects the wrong version, unknown types and bad JSON", () => {
    assert.equal(parseServerEvent(JSON.stringify({ ...base, v: 1, type: "state", state: "IDLE" })), null);
    assert.equal(parseServerEvent(json({ type: "assistant.text", generation_id: 1 })), null);
    assert.equal(parseServerEvent("not json"), null);
    assert.equal(parseServerEvent("42"), null);
  });

  it("rejects generation-scoped events without a generation id", () => {
    assert.equal(parseServerEvent(json({ type: "audio.started", sample_rate: 24000, encoding: "pcm_s16le" })), null);
    assert.equal(parseServerEvent(json({ type: "text.delta", generation_id: "3", delta: "x" })), null);
  });

  it("lets non-generation events through without one", () => {
    assert.ok(parseServerEvent(json({ type: "state", state: "LISTENING" })));
    assert.ok(parseServerEvent(json({ type: "heartbeat" })));
    assert.ok(parseServerEvent(json({ type: "transcript.final", text: "hello", language: "en-IN" })));
  });
});

describe("parseAudioFrame", () => {
  it("reads the >II header (generation_id, seq) and exposes PCM without copying", () => {
    const buffer = new ArrayBuffer(8 + 6);
    const view = new DataView(buffer);
    view.setUint32(0, 7, false);
    view.setUint32(4, 300, false);
    new Int16Array(buffer, 8, 3).set([1, -2, 3]);
    const frame = parseAudioFrame(buffer);
    assert.ok(frame);
    assert.equal(frame.generationId, 7);
    assert.equal(frame.seq, 300);
    assert.deepEqual([...frame.samples], [1, -2, 3]);
    assert.equal(frame.samples.buffer, buffer);
  });

  it("rejects header-only and short frames; drops a trailing odd byte", () => {
    assert.equal(parseAudioFrame(new ArrayBuffer(8)), null);
    assert.equal(parseAudioFrame(new ArrayBuffer(3)), null);
    assert.equal(parseAudioFrame(new ArrayBuffer(8 + 5))?.samples.length, 2);
  });
});

describe("GenerationGate", () => {
  it("adopts newer ids, passes the current one, drops older ones", () => {
    const gate = new GenerationGate();
    assert.equal(gate.admit(4), "new");
    assert.equal(gate.admit(4), "current");
    assert.equal(gate.admit(3), "drop");
    assert.equal(gate.admit(5), "new");
    assert.equal(gate.admit(4), "drop");
    assert.equal(gate.current, 5);
  });

  it("keeps a retired generation dead even if the server keeps streaming it", () => {
    const gate = new GenerationGate();
    gate.admit(9);
    gate.retire(9);
    assert.equal(gate.current, null);
    assert.equal(gate.admit(9), "drop");
    assert.equal(gate.admit(8), "drop");
    assert.equal(gate.admit(10), "new");
  });

  it("reset forgets retired marks so a restarted server id space is accepted", () => {
    const gate = new GenerationGate();
    gate.admit(50);
    gate.retire(50);
    gate.reset();
    assert.equal(gate.admit(1), "new");
  });
});

describe("admitEvent", () => {
  it("drops text/audio events of a stale generation and passes global events", () => {
    const gate = new GenerationGate();
    assert.equal(admitEvent(gate, parse({ type: "text.delta", generation_id: 2, delta: "a" })), true);
    assert.equal(admitEvent(gate, parse({ type: "text.delta", generation_id: 1, delta: "old" })), false);
    assert.equal(admitEvent(gate, parse({ type: "state", state: "SPEAKING" })), true);
    assert.equal(admitEvent(gate, parse({ type: "transcript.final", text: "x" })), true);
  });

  it("drops everything of a locally cancelled generation", () => {
    const gate = new GenerationGate();
    gate.admit(6);
    gate.retire(6); // client barge-in: RealtimeClient.cancelTurn
    assert.equal(admitEvent(gate, parse({ type: "text.delta", generation_id: 6, delta: "late" })), false);
    assert.equal(admitEvent(gate, parse({ type: "audio.completed", generation_id: 6 })), false);
    assert.equal(admitEvent(gate, parse({ type: "text.delta", generation_id: 7, delta: "new reply" })), true);
  });

  it("delivers turn.cancelled once and then retires its generation", () => {
    const gate = new GenerationGate();
    assert.equal(admitEvent(gate, parse({ type: "llm.started", generation_id: 3 })), true);
    assert.equal(admitEvent(gate, parse({ type: "turn.cancelled", generation_id: 3, reason: "x" })), true);
    assert.equal(admitEvent(gate, parse({ type: "text.delta", generation_id: 3, delta: "z" })), false);
  });

  it("passes turn.cancelled / turn.metrics that carry no generation id", () => {
    const gate = new GenerationGate();
    gate.admit(3);
    assert.equal(admitEvent(gate, parse({ type: "turn.cancelled", reason: "stt_failed", heard_text: "" })), true);
    assert.equal(admitEvent(gate, parse({ type: "turn.metrics", stt_ms: 120 })), true);
    assert.equal(gate.current, 3);
  });

  it("ignores a late turn.cancelled for an older generation", () => {
    const gate = new GenerationGate();
    admitEvent(gate, parse({ type: "llm.started", generation_id: 3 }));
    admitEvent(gate, parse({ type: "llm.started", generation_id: 4 }));
    assert.equal(admitEvent(gate, parse({ type: "turn.cancelled", generation_id: 3, reason: "x" })), false);
    assert.equal(gate.current, 4);
  });
});
