"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { BargeInDetector } from "@/lib/audio/BargeInDetector";
import { MicCapture } from "@/lib/audio/MicCapture";
import { PlaybackQueue } from "@/lib/audio/PlaybackQueue";
import { config } from "@/lib/config";
import type { ServerEvent, TurnMetrics, VoiceState } from "@/lib/realtime/protocol";
import { RealtimeClient } from "@/lib/realtime/RealtimeClient";
import { getAccessToken } from "@/lib/supabase/client";

export type CallStatus = "idle" | "connecting" | "live" | "ended" | "error";

export interface VoiceSessionState {
  status: CallStatus;
  /** Server-driven state; RECONNECTING is also set locally while the socket retries. */
  voiceState: VoiceState;
  muted: boolean;
  speakerOn: boolean;
  /** Epoch ms of the first `session.ready`, for the call timer. */
  startedAt: number | null;
  userText: string;
  assistantText: string;
  metrics: TurnMetrics | null;
  error: string | null;
}

const INITIAL: VoiceSessionState = {
  status: "idle",
  voiceState: "IDLE",
  muted: false,
  speakerOn: true,
  startedAt: null,
  userText: "",
  assistantText: "",
  metrics: null,
  error: null,
};

/** Everything one call owns; created in start(), released in teardown(). */
interface Rig {
  client: RealtimeClient;
  mic: MicCapture;
  playback: PlaybackQueue;
  detector: BargeInDetector;
  voiceState: VoiceState;
  textGeneration: number | null;
  muted: boolean;
  speakerOn: boolean;
}

function describeMicError(error: unknown): string {
  const name = error instanceof DOMException ? error.name : "";
  if (name === "NotAllowedError" || name === "SecurityError")
    return "Microphone access is blocked. Allow it in your browser settings and try again.";
  if (name === "NotFoundError") return "No microphone was found on this device.";
  return "Could not start the microphone.";
}

/**
 * Wires RealtimeClient + MicCapture + PlaybackQueue + BargeInDetector to React state.
 * All audio/network objects live in a ref; React only sees the derived UI state.
 */
export function useVoiceSession({ language }: { language: string }) {
  const [state, setState] = useState<VoiceSessionState>(INITIAL);
  const rigRef = useRef<Rig | null>(null);
  const patch = useCallback((partial: Partial<VoiceSessionState>) => setState((s) => ({ ...s, ...partial })), []);

  /** 0..1 level for the orb: assistant output while speaking, otherwise the mic. */
  const getLevel = useCallback((): number => {
    const rig = rigRef.current;
    if (!rig) return 0;
    return rig.voiceState === "SPEAKING" ? rig.playback.level : rig.mic.level;
  }, []);

  const teardown = useCallback(() => {
    const rig = rigRef.current;
    if (!rig) return;
    rigRef.current = null;
    rig.detector.stop();
    rig.client.audioEnd();
    rig.client.end();
    rig.mic.stop();
    rig.playback.close();
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
    const playback = new PlaybackQueue();
    const mic = new MicCapture((frame) => client.sendAudio(frame));
    const detector = new BargeInDetector({
      getMicLevel: () => mic.level,
      isPlaybackActive: () => playback.isPlaying,
      // Stop locally first so the user hears silence immediately, then tell the server.
      onBargeIn: () => {
        playback.stop();
        client.interrupt();
      },
    });
    const rig: Rig = { client, mic, playback, detector, voiceState: "IDLE", textGeneration: null, muted: false, speakerOn: true };
    rigRef.current = rig;

    try {
      await playback.init(); // inside the click gesture
      await mic.start();
    } catch (error) {
      teardown();
      setState({ ...INITIAL, status: "error", error: describeMicError(error) });
      return;
    }
    // teardown() may have run while we awaited permission.
    if (rigRef.current !== rig) {
      mic.stop();
      playback.close();
      return;
    }

    const setVoiceState = (voiceState: VoiceState) => {
      rig.voiceState = voiceState;
      patch({ voiceState });
    };

    const onEvent = (event: ServerEvent) => {
      switch (event.type) {
        case "session.ready":
          setState((s) => ({ ...s, status: "live", startedAt: s.startedAt ?? Date.now(), error: null }));
          break;
        case "state":
          setVoiceState(event.state);
          break;
        case "transcript.partial":
          patch({ userText: event.text });
          break;
        case "transcript.final":
          patch({ userText: event.text });
          break;
        case "assistant.text": {
          const continuing = rig.textGeneration === event.generation_id;
          rig.textGeneration = event.generation_id;
          setState((s) => ({ ...s, assistantText: continuing ? s.assistantText + event.delta : event.delta }));
          break;
        }
        case "assistant.audio.start":
          playback.beginGeneration(event.generation_id, event.sample_rate);
          break;
        case "assistant.done":
          playback.markGenerationDone(event.generation_id);
          rig.textGeneration = event.generation_id;
          patch({ assistantText: event.text });
          break;
        case "interrupted":
          playback.stop();
          break;
        case "metrics":
          patch({ metrics: event });
          break;
        case "error":
          patch({ error: event.message });
          break;
        case "pong":
          break;
      }
    };

    client.events.on("event", onEvent);
    client.events.on("audio", ({ generationId, pcm }) => playback.enqueue(generationId, pcm));
    client.events.on("status", (status) => {
      if (status === "reconnecting") setVoiceState("RECONNECTING");
      else if (status === "ready" && rig.voiceState === "RECONNECTING") setVoiceState("LISTENING");
    });
    client.events.on("closed", ({ code, byUser }) => {
      if (byUser) return;
      teardown();
      setState((s) => ({
        ...s,
        status: "error",
        voiceState: "IDLE",
        error: code === 4401 ? "Your session expired. Please sign in again." : "Connection lost. Please try again.",
      }));
    });
    playback.events.on("playback.started", (id) => client.playbackStarted(id));
    playback.events.on("playback.finished", (id) => client.playbackFinished(id));

    detector.start();
    client.connect();
  }, [language, patch, teardown]);

  const end = useCallback(() => {
    teardown();
    setState((s) => ({ ...s, status: "ended", voiceState: "IDLE", muted: false }));
  }, [teardown]);

  const toggleMute = useCallback(() => {
    const rig = rigRef.current;
    if (!rig) return;
    rig.muted = !rig.muted;
    rig.mic.setMuted(rig.muted);
    // Muting is "mic stopped": let the server flush whatever STT has buffered.
    if (rig.muted) rig.client.audioEnd();
    patch({ muted: rig.muted });
  }, [patch]);

  const toggleSpeaker = useCallback(() => {
    const rig = rigRef.current;
    if (!rig) return;
    rig.speakerOn = !rig.speakerOn;
    rig.playback.setMuted(!rig.speakerOn);
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
