"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { config } from "@/lib/config";
import { MAX_TEXT_CHARS, type ServerEvent, type VoiceState } from "@/lib/realtime/protocol";
import { RealtimeClient, type ConnectionStatus } from "@/lib/realtime/RealtimeClient";
import { getAccessToken } from "@/lib/supabase/client";

export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  text: string;
  /** Server generation id for assistant messages, used to stream deltas into one bubble. */
  generationId?: number;
  interrupted?: boolean;
}

export interface ChatSessionState {
  connection: ConnectionStatus;
  voiceState: VoiceState;
  messages: ChatMessage[];
  error: string | null;
}

/** Text fallback: the same WebSocket protocol (v2) with `mode: "text"` and no audio. */
export function useChatSession({ language }: { language: string }) {
  const [state, setState] = useState<ChatSessionState>({
    connection: "idle",
    voiceState: "IDLE",
    messages: [],
    error: null,
  });
  const clientRef = useRef<RealtimeClient | null>(null);
  const counter = useRef(0);

  useEffect(() => {
    const client = new RealtimeClient({ url: config.backendWsUrl, getToken: getAccessToken, language, mode: "text" });
    clientRef.current = client;

    const onEvent = (event: ServerEvent) => {
      switch (event.type) {
        case "state":
          setState((s) => ({ ...s, voiceState: event.state }));
          break;
        case "text.delta":
          setState((s) => ({ ...s, messages: appendDelta(s.messages, event.generation_id, event.delta, counter) }));
          break;
        case "turn.completed":
          setState((s) => ({ ...s, messages: finalize(s.messages, event.generation_id, event.text, counter) }));
          break;
        case "turn.cancelled":
          setState((s) => ({
            ...s,
            messages: s.messages.map((m) => (m.generationId === event.generation_id ? { ...m, interrupted: true } : m)),
          }));
          break;
        case "pipeline.error":
          setState((s) => ({ ...s, error: event.message }));
          break;
        default:
          break;
      }
    };

    client.events.on("event", onEvent);
    client.events.on("status", (connection) => setState((s) => ({ ...s, connection, error: connection === "ready" ? null : s.error })));
    client.events.on("closed", ({ code, byUser }) => {
      if (byUser) return;
      setState((s) => ({
        ...s,
        connection: "closed",
        error:
          code === 4401
            ? "Your session expired. Please sign in again."
            : code === 4503
              ? "Oryn is busy right now. Please try again in a moment."
              : "Connection lost.",
      }));
    });
    client.connect();

    return () => {
      client.end();
      clientRef.current = null;
    };
  }, [language]);

  const send = useCallback((raw: string): boolean => {
    const text = raw.trim().slice(0, MAX_TEXT_CHARS);
    const client = clientRef.current;
    if (!text || !client?.sendText(text)) return false;
    const id = `u${counter.current++}`;
    setState((s) => ({ ...s, messages: [...s.messages, { id, role: "user", text }] }));
    return true;
  }, []);

  const typing =
    (state.voiceState === "PROCESSING" || state.voiceState === "GENERATING") &&
    state.messages[state.messages.length - 1]?.role === "user";

  return { ...state, typing, send };
}

function appendDelta(
  messages: ChatMessage[],
  generationId: number,
  delta: string,
  counter: { current: number },
): ChatMessage[] {
  const index = messages.findIndex((m) => m.role === "assistant" && m.generationId === generationId);
  if (index === -1) {
    return [...messages, { id: `a${counter.current++}`, role: "assistant", text: delta, generationId }];
  }
  return messages.map((m, i) => (i === index ? { ...m, text: m.text + delta } : m));
}

/** `assistant.done` carries the full text, which is authoritative over streamed deltas. */
function finalize(
  messages: ChatMessage[],
  generationId: number,
  text: string,
  counter: { current: number },
): ChatMessage[] {
  const index = messages.findIndex((m) => m.role === "assistant" && m.generationId === generationId);
  if (index === -1) return [...messages, { id: `a${counter.current++}`, role: "assistant", text, generationId }];
  return messages.map((m, i) => (i === index ? { ...m, text } : m));
}
