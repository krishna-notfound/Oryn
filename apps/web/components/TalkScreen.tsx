"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { Brain, MessageCircle, Mic, MicOff, PhoneOff, Settings, Volume2, VolumeX } from "lucide-react";
import { useVoiceSession, type CallStatus } from "@/hooks/useVoiceSession";
import type { CaptionFrame } from "@/lib/captions/CaptionTrack";
import type { ServerTurnMetrics, VoiceState } from "@/lib/realtime/protocol";
import { Orb, toOrbState } from "./Orb";
import { IconLink, TopBar } from "./ui";

const STATUS_LABEL: Record<VoiceState, string> = {
  IDLE: "Getting ready…",
  LISTENING: "Listening…",
  PROCESSING: "Thinking…",
  GENERATING: "Thinking…",
  SPEAKING: "Oryn is speaking",
  INTERRUPTED: "Go ahead, I'm listening",
  RECONNECTING: "Reconnecting…",
  ERROR: "Something went wrong",
};

function greeting(name: string | null): string {
  return name ? `Hi, ${name}` : "Hi there";
}

function formatElapsed(ms: number): string {
  const total = Math.max(0, Math.floor(ms / 1000));
  return `${String(Math.floor(total / 60)).padStart(2, "0")}:${String(total % 60).padStart(2, "0")}`;
}

function CallTimer({ startedAt }: { startedAt: number | null }) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, []);
  return (
    <span className="type-label-md tabular-nums text-on-surface-variant">
      {startedAt ? formatElapsed(now - startedAt) : "00:00"}
    </span>
  );
}

/**
 * Latency readout for the last turn: what the user actually waited (speech end to first audible
 * sound, measured in the browser). Server stage timings sit in the tooltip. Debug builds only
 * (`/talk?debug=1`): a number on the call screen does not make the conversation feel human.
 */
function LatencyPill({ perceivedMs, metrics }: { perceivedMs: number | null; metrics: ServerTurnMetrics | null }) {
  const shown = perceivedMs ?? metrics?.server_first_audio_ms ?? null;
  if (shown == null) return null;
  const parts = [
    ["server", metrics?.server_first_audio_ms],
    ["stt", metrics?.stt_ms],
    ["llm", metrics?.llm_ttft_ms],
    ["tts", metrics?.tts_ttfb_ms],
  ]
    .filter(([, value]) => value != null)
    .map(([name, value]) => `${name} ${Math.round(value as number)} ms`)
    .join(" · ");
  return (
    <span
      title={perceivedMs != null ? `heard after ${perceivedMs} ms${parts ? ` · ${parts}` : ""}` : parts}
      className="type-label-sm rounded-full bg-surface-container px-2.5 py-1 tabular-nums text-on-surface-variant"
    >
      {Math.round(shown)} ms
    </span>
  );
}

function CallButton({
  label,
  onClick,
  className,
  children,
}: {
  label: string;
  onClick: () => void;
  className: string;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      aria-label={label}
      onClick={onClick}
      className={`grid size-14 place-items-center rounded-full shadow-sm transition-all active:scale-90 ${className}`}
    >
      {children}
    </button>
  );
}

export function TalkScreen({ name, language, debug = false }: { name: string | null; language: string; debug?: boolean }) {
  const session = useVoiceSession({ language });
  const inCall = session.status === "connecting" || session.status === "live";
  // The browser VAD hears the user before the server state catches up: show "listening" at once.
  // A ducked reply (user talking over it, verdict pending) looks the same: Oryn is listening.
  const orbState =
    (session.userSpeaking || session.ducked) && session.voiceState !== "RECONNECTING" && session.voiceState !== "ERROR"
      ? "listening"
      : toOrbState(session.voiceState, session.status === "live");

  return (
    <div className="flex h-dvh flex-col">
      {inCall ? (
        <TopBar
          title="Live Voice Call"
          right={
            <>
              {debug && <LatencyPill perceivedMs={session.perceivedMs} metrics={session.metrics} />}
              <span className="type-label-sm ml-1 inline-flex items-center gap-1 rounded-full bg-secondary-container/40 px-2.5 py-1 text-on-secondary-container">
                <span className="size-1.5 animate-pulse rounded-full bg-secondary" />
                <CallTimer startedAt={session.startedAt} />
              </span>
            </>
          }
        />
      ) : (
        <TopBar
          right={
            <>
              <IconLink href="/chat" label="Chat">
                <MessageCircle size={22} />
              </IconLink>
              <IconLink href="/memories" label="Memories">
                <Brain size={22} />
              </IconLink>
              <IconLink href="/settings" label="Settings">
                <Settings size={22} />
              </IconLink>
            </>
          }
        />
      )}

      <main className="page-col flex min-h-0 flex-1 flex-col items-center px-margin">
        {inCall ? (
          <CallView session={session} orbState={orbState} />
        ) : (
          <HomeView name={name} status={session.status} error={session.error} onStart={session.start} orbState={orbState} />
        )}
      </main>

      {inCall && (
        <div className="pb-safe page-col px-margin">
          <div className="mx-auto mb-space-lg flex max-w-xs items-center justify-between rounded-full border border-outline-variant/30 bg-surface-container-lowest/95 px-space-lg py-3 shadow-md backdrop-blur-xl">
            <CallButton
              label={session.muted ? "Unmute microphone" : "Mute microphone"}
              onClick={session.toggleMute}
              className={
                session.muted
                  ? "bg-error-container text-on-error-container"
                  : "bg-secondary-container/50 text-on-secondary-container hover:bg-secondary-container/70"
              }
            >
              {session.muted ? <MicOff size={24} /> : <Mic size={24} />}
            </CallButton>
            <CallButton
              label={session.speakerOn ? "Mute speaker" : "Unmute speaker"}
              onClick={session.toggleSpeaker}
              className="bg-surface-container text-on-surface hover:bg-surface-container-high"
            >
              {session.speakerOn ? <Volume2 size={24} /> : <VolumeX size={24} />}
            </CallButton>
            <CallButton
              label="End call"
              onClick={session.end}
              className="bg-error-container text-on-error-container hover:bg-error-container/80"
            >
              <PhoneOff size={24} />
            </CallButton>
          </div>
        </div>
      )}
    </div>
  );
}

function HomeView({
  name,
  status,
  error,
  onStart,
  orbState,
}: {
  name: string | null;
  status: CallStatus;
  error: string | null;
  onStart: () => void;
  orbState: ReturnType<typeof toOrbState>;
}) {
  return (
    <div className="pb-safe flex w-full flex-1 flex-col items-center justify-between py-space-md">
      <div className="flex flex-1 flex-col items-center justify-center">
        <Orb state={orbState} trackPointer />
        <div className="mt-space-sm max-w-xs text-center">
          <h1 className="type-headline-lg-mobile text-on-primary-fixed">{greeting(name)}</h1>
          <p className="type-body-md mt-space-xs text-outline">
            {status === "ended" ? "Call ended. Tap to talk again." : "Tap below whenever you want to talk."}
          </p>
        </div>
        {error && (
          <p role="alert" className="type-body-sm mt-space-md max-w-xs rounded-2xl bg-error-container/60 px-4 py-3 text-center text-on-error-container">
            {error}
          </p>
        )}
      </div>

      <div className="flex w-full max-w-sm flex-col items-center gap-space-sm pb-space-md">
        <button
          type="button"
          onClick={onStart}
          className="type-headline-sm flex min-h-16 w-full items-center justify-center gap-3 rounded-full bg-primary text-on-primary shadow-[0_8px_30px_-4px_rgba(184,165,239,0.6)] transition-all active:scale-[0.98]"
        >
          <Mic size={26} aria-hidden="true" />
          Start Talking
        </button>
        <Link href="/chat" className="type-label-md flex min-h-11 items-center px-4 text-on-surface-variant underline-offset-4 hover:underline">
          Prefer typing? Open chat
        </Link>
      </div>
    </div>
  );
}

function CallView({
  session,
  orbState,
}: {
  session: ReturnType<typeof useVoiceSession>;
  orbState: ReturnType<typeof toOrbState>;
}) {
  const label =
    session.status === "connecting"
      ? "Connecting…"
      : (session.userSpeaking || session.ducked) && session.voiceState !== "RECONNECTING"
        ? STATUS_LABEL.LISTENING
        : STATUS_LABEL[session.voiceState];
  return (
    <div className="flex w-full flex-1 flex-col items-center justify-center gap-space-md py-space-md">
      <Orb state={orbState} getLevel={session.getLevel} />

      <div
        role="status"
        aria-live="polite"
        className="type-label-sm flex items-center gap-2.5 rounded-full border border-secondary/20 bg-secondary-container/30 px-4 py-1.5 text-on-secondary-container shadow-sm backdrop-blur-md"
      >
        <span className={`size-2 rounded-full bg-secondary ${orbState === "listening" ? "animate-ping" : ""}`} />
        {label}
      </div>

      <div className="min-h-32 w-full max-w-sm space-y-space-sm text-center">
        {session.userText && (
          <p className="type-body-md line-clamp-2 text-on-surface-variant">“{session.userText}”</p>
        )}
        {session.captions ? (
          <Captions frame={session.captions} />
        ) : (
          // Server without caption events: fall back to the plain reply text.
          session.assistantText && <p className="type-body-lg line-clamp-3 text-on-surface">{session.assistantText}</p>
        )}
        {session.error && <p className="type-body-sm text-error">{session.error}</p>}
      </div>
    </div>
  );
}

/**
 * The reply as it is spoken: the current segment large with unspoken words dimmed (so the layout
 * never reflows), the previous segment small and faded above it.
 */
function Captions({ frame }: { frame: CaptionFrame }) {
  return (
    <div aria-live="off" className="flex flex-col items-center gap-space-xs">
      {frame.previous && <p className="type-body-sm line-clamp-2 text-on-surface-variant/50 transition-opacity">{frame.previous}</p>}
      <p className="type-body-lg text-on-surface">
        {frame.words.map((word, i) => (
          <span key={i} className={`transition-opacity duration-200 ${i < frame.revealed ? "opacity-100" : "opacity-25"}`}>
            {word}{" "}
          </span>
        ))}
      </p>
    </div>
  );
}
