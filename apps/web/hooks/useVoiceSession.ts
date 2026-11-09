"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { AudioScheduler } from "@/lib/audio/AudioScheduler";
import { BargeInDetector } from "@/lib/audio/BargeInDetector";
import { MicCapture } from "@/lib/audio/MicCapture";
import { VoiceActivityDetector } from "@/lib/audio/VoiceActivityDetector";
import { perfToEpochMs } from "@/lib/audio/vadCore";
import { config } from "@/lib/config";
import type { ServerEvent, ServerTurnMetrics, VoiceState } from "@/lib/realtime/protocol";
import { CLOSE_PROTOCOL_MISMATCH, RealtimeClient } from "@/lib/realtime/RealtimeClient";
import { getAccessToken } from "@/lib/supabase/client";

export type CallStatus = "idle" | "connecting" | "live" | "ended" | "error";

export interface VoiceSessionState {
  status: CallStatus;
  /** Server-driven state; RECONNECTING is also set locally while the socket retries. */
  voiceState: VoiceState;
  /** The browser VAD currently hears the user (drives the orb without waiting for the server). */
  userSpeaking: boolean;
  muted: boolean;
  speakerOn: boolean;
  /** Epoch ms of the first `session.ready`, for the call timer. */
  startedAt: number | null;
  userText: string;
  assistantText: string;
  /** Server marks of the last turn. */
  metrics: ServerTurnMetrics | null;
  /** Client-measured speech end -> first audible sample of the last turn, ms. */
  perceivedMs: number | null;
  error: string | null;
}

const INITIAL: VoiceSessionState = {
  status: "idle",
  voiceState: "IDLE",
  userSpeaking: false,
  muted: false,
  speakerOn: true,
  startedAt: null,
  userText: "",
  assistantText: "",
  metrics: null,
  perceivedMs: null,
  error: null,
};

/** A speech end older than this never belongs to the audio that finally plays. */
const MAX_PERCEIVED_MS = 30_000;

/** Everything one call owns; created in start(), released in teardown(). */
interface Rig {
  client: RealtimeClient;
  mic: MicCapture;
  scheduler: AudioScheduler;
  vad: VoiceActivityDetector;
  bargeIn: BargeInDetector;
  voiceState: VoiceState;
  userSpeaking: boolean;
  textGeneration: number | null;
  /** Turn id that owns the generation currently on the speaker (from `audio.started`). */
  audioTurnId: number | null;
  /** `performance.now()` of the last voiced frame (true speech end) that no audio has answered yet. */
  speechEndAt: number | null;
  muted: boolean;
  speakerOn: boolean;
}

function closeMessage(code: number): string {
  switch (code) {
    case 4401:
      return "Your session expired. Please sign in again.";
    case 4409:
      return "This call was opened somewhere else.";
    case 4503:
      return "Oryn is busy right now. Please try again in a moment.";
    case CLOSE_PROTOCOL_MISMATCH:
      return "Oryn was updated. Please refresh the page.";
    default:
      return "Connection lost. Please try again.";
  }
}

function describeMicError(error: unknown): string {
  const name = error instanceof DOMException ? error.name : "";
  if (name === "NotAllowedError" || name === "SecurityError")
    return "Microphone access is blocked. Allow it in your browser settings and try again.";
  if (name === "NotFoundError") return "No microphone was found on this device.";
  return "Could not start the microphone.";
}

/**
 * Wires RealtimeClient + MicCapture + VAD + AudioScheduler + BargeInDetector to React state.
 * All audio/network objects live in a ref; React only sees the derived UI state.
 */
export function useVoiceSession({ language }: { language: string }) {
  const [state, setState] = useState<VoiceSessionState>(INITIAL);
  const rigRef = useRef<Rig | null>(null);
  const patch = useCallback((partial: Partial<VoiceSessionState>) => setState((s) => ({ ...s, ...partial })), []);

  /** 0..1 level for the orb: assistant output while it speaks, otherwise the mic. */
  const getLevel = useCallback((): number => {
    const rig = rigRef.current;
    if (!rig) return 0;
    return rig.voiceState === "SPEAKING" && !rig.userSpeaking ? rig.scheduler.level : rig.mic.level;
  }, []);

  const teardown = useCallback(() => {
    const rig = rigRef.current;
    if (!rig) return;
    rigRef.current = null;
    rig.vad.close();
    rig.client.end();
    rig.mic.stop();
    rig.scheduler.close();
  }, []);

  const start = useCallback(async () => {
    if (rigRef.current) return;
    setState({ ...INITIAL, status: "connecting" });

    const client = new RealtimeClient({
      url: config.backendWsUrl,
      getToken: getAccessToken,
      language,
      mode: "voice",
    });
    const scheduler = new AudioScheduler();
    const vad = new VoiceActivityDetector();
    // Stop locally first so the user hears silence immediately, then tell the server.
    const bargeIn = new BargeInDetector(scheduler, (generationId) => client.cancelTurn(generationId));
    // Mic frames always stream; the VAD only decides what to announce.
    const mic = new MicCapture((frame, level) => {
      client.sendAudio(frame);
      vad.process(level, scheduler.isActive);
    });
    const rig: Rig = {
      client,
      mic,
      scheduler,
      vad,
      bargeIn,
      voiceState: "IDLE",
      userSpeaking: false,
      textGeneration: null,
      audioTurnId: null,
      speechEndAt: null,
      muted: false,
      speakerOn: true,
    };
    rigRef.current = rig;

    try {
      await scheduler.init(); // inside the click gesture
      await mic.start();
    } catch (error) {
      teardown();
      setState({ ...INITIAL, status: "error", error: describeMicError(error) });
      return;
    }
    // teardown() may have run while we awaited permission.
    if (rigRef.current !== rig) {
      mic.stop();
      scheduler.close();
      return;
    }

    const setVoiceState = (voiceState: VoiceState) => {
      rig.voiceState = voiceState;
      patch({ voiceState });
    };
    const setUserSpeaking = (userSpeaking: boolean) => {
      rig.userSpeaking = userSpeaking;
      setState((s) => (s.userSpeaking === userSpeaking ? s : { ...s, userSpeaking }));
    };
    /** Returns true when `generationId` already owns the assistant bubble. */
    const enterTextGeneration = (generationId: number): boolean => {
      const continuing = rig.textGeneration === generationId;
      rig.textGeneration = generationId;
      return continuing;
    };

    const onEvent = (event: ServerEvent) => {
      switch (event.type) {
        case "session.ready":
          void scheduler.useOutputRate(event.output_sample_rate);
          setState((s) => ({ ...s, status: "live", startedAt: s.startedAt ?? Date.now(), error: null }));
          break;
        case "state":
          setVoiceState(event.state);
          break;
        case "transcript.final":
          patch({ userText: event.text });
          break;
        case "llm.started":
          if (!enterTextGeneration(event.generation_id)) patch({ assistantText: "" });
          break;
        case "text.delta": {
          const continuing = enterTextGeneration(event.generation_id);
          setState((s) => ({ ...s, assistantText: continuing ? s.assistantText + event.delta : event.delta }));
          break;
        }
        case "text.segment":
          break; // deltas already carry the text
        case "audio.started":
          rig.audioTurnId = event.turn_id ?? null;
          scheduler.beginGeneration(event.generation_id, event.sample_rate);
          break;
        case "audio.completed":
          scheduler.markGenerationDone(event.generation_id);
          break;
        case "turn.completed":
          enterTextGeneration(event.generation_id);
          patch({ assistantText: event.text });
          break;
        case "turn.cancelled":
          // Server-side cancel (error, dropped speculative turn): silence whatever was playing.
          if (event.generation_id === undefined || scheduler.generationId === event.generation_id) scheduler.stop();
          if (event.heard_text) patch({ assistantText: event.heard_text });
          break;
        case "turn.metrics":
          patch({ metrics: event });
          break;
        case "pipeline.error":
          patch({ error: event.message });
          break;
        case "heartbeat":
          break;
      }
    };

    client.events.on("event", onEvent);
    client.events.on("audio", (frame) => scheduler.enqueue(frame.generationId, frame.seq, frame.samples));
    // New socket (first connect or reconnect): server ids restart, nothing old may survive.
    client.events.on("reset", () => {
      scheduler.reset();
      rig.textGeneration = null;
      rig.audioTurnId = null;
      rig.speechEndAt = null;
    });
    client.events.on("status", (status) => {
      if (status === "reconnecting") setVoiceState("RECONNECTING");
      else if (status === "ready" && rig.voiceState === "RECONNECTING") setVoiceState("LISTENING");
    });
    client.events.on("closed", ({ code, byUser }) => {
      if (byUser) return;
      teardown();
      setState((s) => ({ ...s, status: "error", voiceState: "IDLE", userSpeaking: false, error: closeMessage(code) }));
    });

    scheduler.events.on("scheduled", ({ generationId, audibleAtMs, leadMs }) => {
      if (rig.speechEndAt === null) return;
      const perceivedMs = audibleAtMs - rig.speechEndAt;
      rig.speechEndAt = null;
      if (perceivedMs < 0 || perceivedMs > MAX_PERCEIVED_MS) return;
      patch({ perceivedMs: Math.round(perceivedMs) });
      if (rig.audioTurnId !== null) client.turnMetrics(rig.audioTurnId, generationId, perceivedMs, leadMs);
    });
    scheduler.events.on("audible", (id) => client.playbackStarted(id));
    scheduler.events.on("finished", (id) => client.playbackFinished(id));

    vad.events.on("speechStarted", ({ at }) => {
      rig.speechEndAt = null; // the user resumed: the earlier end is not the one being answered
      bargeIn.onSpeechStarted(at); // local stop, then turn.cancel; both go out before the speech edge
      client.speechStarted(Date.now());
      setUserSpeaking(true);
    });
    vad.events.on("speechEnded", ({ lastVoicedAt, misfire }) => {
      // The user stopped at lastVoicedAt, not when the 250 ms hangover elapsed: both the wire
      // timestamp and the perceived-latency origin use the true end.
      client.speechEnded(perfToEpochMs(lastVoicedAt, performance.now(), Date.now()));
      setUserSpeaking(false);
      if (!misfire) rig.speechEndAt = lastVoicedAt;
    });

    client.connect();
  }, [language, patch, teardown]);

  const end = useCallback(() => {
    teardown();
    setState((s) => ({ ...s, status: "ended", voiceState: "IDLE", userSpeaking: false, muted: false }));
  }, [teardown]);

  const toggleMute = useCallback(() => {
    const rig = rigRef.current;
    if (!rig) return;
    rig.muted = !rig.muted;
    rig.mic.setMuted(rig.muted);
    // Muted frames stop reaching the VAD, so close any open utterance now (the server commits).
    if (rig.muted) rig.vad.flush();
    patch({ muted: rig.muted });
  }, [patch]);

  const toggleSpeaker = useCallback(() => {
    const rig = rigRef.current;
    if (!rig) return;
    rig.speakerOn = !rig.speakerOn;
    rig.scheduler.setMuted(!rig.speakerOn);
    patch({ speakerOn: rig.speakerOn });
  }, [patch]);

  useEffect(() => {
    const release = () => teardown();
    window.addEventListener("pagehide", release);
    return () => {
      window.removeEventListener("pagehide", release);
      release();
    };
  }, [teardown]);

  return { ...state, getLevel, start, end, toggleMute, toggleSpeaker };
}
