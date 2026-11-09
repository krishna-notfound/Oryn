import assert from "node:assert/strict";
import { beforeEach, describe, it } from "node:test";
import { RealtimeClient } from "../lib/realtime/RealtimeClient";

class FakeSocket {
  static instances: FakeSocket[] = [];
  binaryType = "blob";
  bufferedAmount = 0;
  sent: (string | ArrayBuffer)[] = [];
  onopen: (() => void) | null = null;
  onmessage: ((message: { data: unknown }) => void) | null = null;
  onclose: ((close: { code: number; reason: string }) => void) | null = null;
  constructor(readonly url: string) {
    FakeSocket.instances.push(this);
  }
  send(data: string | ArrayBuffer) {
    this.sent.push(data);
  }
  close() {}
  // test helpers
  open() {
    this.onopen?.();
  }
  push(event: object) {
    this.onmessage?.({ data: JSON.stringify({ v: 2, session_id: "s", seq: 1, ts: 1, ...event }) });
  }
  pushAudio(generationId: number, seq: number) {
    const buffer = new ArrayBuffer(8 + 4);
    new DataView(buffer).setUint32(0, generationId);
    new DataView(buffer).setUint32(4, seq);
    this.onmessage?.({ data: buffer });
  }
  drop(code: number) {
    this.onclose?.({ code, reason: "" });
  }
  json() {
    return this.sent.filter((frame): frame is string => typeof frame === "string").map((frame) => JSON.parse(frame));
  }
}

const READY = { type: "session.ready", conversation_id: "c1", input_sample_rate: 16000, output_sample_rate: 24000, protocol: 2 };
const tick = (ms = 0) => new Promise((resolve) => setTimeout(resolve, ms));

async function connected(mode: "voice" | "text" = "voice") {
  const client = new RealtimeClient({ url: "ws://x", getToken: async () => "tok", language: "en-IN", mode });
  const delivered: string[] = [];
  const audio: number[][] = [];
  client.events.on("event", (event) => delivered.push(`${event.type}:${event.generation_id ?? "-"}`));
  client.events.on("audio", (frame) => audio.push([frame.generationId, frame.seq]));
  client.connect();
  await tick();
  const socket = FakeSocket.instances[FakeSocket.instances.length - 1];
  socket.open();
  socket.push(READY);
  return { client, socket, delivered, audio };
}

describe("RealtimeClient (protocol v2)", () => {
  beforeEach(() => {
    FakeSocket.instances = [];
    (globalThis as unknown as { WebSocket: unknown }).WebSocket = FakeSocket;
  });

  it("opens with a v2 session.start and wraps every message in the v2 envelope", async () => {
    const { client, socket } = await connected("text");
    client.sendText("hello");
    client.speechStarted(1234.6);
    client.speechEnded(2000);
    client.playbackStarted(3);
    client.turnMetrics(2, 3, 1300.4, 40.2);
    const sent = socket.json();
    assert.deepEqual(sent[0], { v: 2, type: "session.start", token: "tok", language: "en-IN", mode: "text" });
    assert.deepEqual(sent.slice(1), [
      { v: 2, type: "user.text", text: "hello" },
      { v: 2, type: "input.speech_started", ts: 1235 },
      { v: 2, type: "input.speech_ended", ts: 2000 },
      { v: 2, type: "playback.started", generation_id: 3 },
      { v: 2, type: "turn.metrics", turn_id: 2, generation_id: 3, perceived_ms: 1300, playback_lead_ms: 40 },
    ]);
    client.end();
  });

  it("drops events and audio of a generation cancelled locally, but not the next one", async () => {
    const { client, socket, delivered, audio } = await connected();
    socket.push({ type: "audio.started", generation_id: 4, turn_id: 4, sample_rate: 24000, encoding: "pcm_s16le" });
    socket.pushAudio(4, 0);
    client.cancelTurn(4);
    assert.deepEqual(socket.json().at(-1), { v: 2, type: "turn.cancel", generation_id: 4 });
    socket.pushAudio(4, 1);
    socket.push({ type: "text.delta", generation_id: 4, delta: "late" });
    socket.push({ type: "state", state: "LISTENING" });
    socket.push({ type: "text.delta", generation_id: 5, delta: "fresh" });
    socket.pushAudio(5, 0);
    assert.deepEqual(delivered, ["session.ready:-", "audio.started:4", "state:-", "text.delta:5"]);
    assert.deepEqual(audio, [[4, 0], [5, 0]]);
    client.end();
  });

  it("drops stale-generation events once a newer generation has started", async () => {
    const { client, socket, delivered } = await connected();
    socket.push({ type: "llm.started", generation_id: 2 });
    socket.push({ type: "llm.started", generation_id: 3 });
    socket.push({ type: "text.delta", generation_id: 2, delta: "old" });
    assert.deepEqual(delivered, ["session.ready:-", "llm.started:2", "llm.started:3"]);
    client.end();
  });

  it("resets generation state on reconnect and tells listeners first", async () => {
    const { client, socket, delivered } = await connected();
    const order: string[] = [];
    client.events.on("reset", () => order.push("reset"));
    client.events.on("status", (status) => order.push(status));
    socket.push({ type: "llm.started", generation_id: 40 });
    client.cancelTurn(40);
    socket.drop(1006);
    await tick(700);
    const next = FakeSocket.instances[FakeSocket.instances.length - 1];
    assert.notEqual(next, socket);
    next.open();
    next.push(READY);
    next.push({ type: "llm.started", generation_id: 1 }); // server ids restarted
    assert.deepEqual(order.slice(0, 2), ["reconnecting", "reset"]);
    assert.equal(delivered.at(-1), "llm.started:1");
    // session.start on the new socket resumes the same conversation.
    assert.equal(next.json()[0].conversation_id, "c1");
    client.end();
  });

  for (const code of [4401, 4409, 4503]) {
    it(`close code ${code} is terminal: no reconnect`, async () => {
      const { client, socket } = await connected();
      const closed: number[] = [];
      client.events.on("closed", ({ code: c }) => closed.push(c));
      socket.drop(code);
      await tick(700);
      assert.deepEqual(closed, [code]);
      assert.equal(FakeSocket.instances.length, 1);
    });
  }

  it("refuses a server that does not speak protocol 2", async () => {
    const client = new RealtimeClient({ url: "ws://x", getToken: async () => "tok", language: "en-IN", mode: "voice" });
    const closed: number[] = [];
    client.events.on("closed", ({ code }) => closed.push(code));
    client.connect();
    await tick();
    const socket = FakeSocket.instances[0];
    socket.open();
    socket.push({ ...READY, protocol: 1 });
    assert.deepEqual(closed, [4426]);
  });

  it("ignores v1 frames and sends nothing before the session is ready", async () => {
    const client = new RealtimeClient({ url: "ws://x", getToken: async () => "tok", language: "en-IN", mode: "voice" });
    client.connect();
    await tick();
    const socket = FakeSocket.instances[0];
    socket.open();
    assert.equal(client.sendText("too early"), false);
    socket.onmessage?.({ data: JSON.stringify({ type: "session.ready", session_id: "s" }) });
    assert.equal(client.isReady, false);
    client.end();
  });
});
