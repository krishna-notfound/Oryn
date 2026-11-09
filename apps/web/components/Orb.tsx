"use client";

import { useEffect, useRef } from "react";
import type { VoiceState } from "@/lib/realtime/protocol";

export type OrbState = "idle" | "listening" | "thinking" | "speaking" | "interrupted" | "offline";

/** Maps the server VoiceState to what the orb should look like. */
export function toOrbState(voiceState: VoiceState, live: boolean): OrbState {
  if (!live) return "idle";
  switch (voiceState) {
    case "LISTENING":
      return "listening";
    case "PROCESSING":
    case "GENERATING":
      return "thinking";
    case "SPEAKING":
      return "speaking";
    case "INTERRUPTED":
      return "interrupted";
    case "RECONNECTING":
    case "ERROR":
      return "offline";
    default:
      return "idle";
  }
}

const TONES = {
  // Welcome screen (saathi_welcome): lavender sphere.
  lavender: {
    background: "radial-gradient(circle at 35% 30%, #f4f0ff 0%, #cebdff 45%, #9b84e6 85%, #8065d6 100%)",
    boxShadow:
      "inset -6px -8px 24px rgba(76,60,125,0.4), inset 6px 6px 20px rgba(255,255,255,0.8), 0 0 35px 8px rgba(196,239,103,0.28)",
    ink: "bg-on-primary-fixed",
    smile: "border-on-primary-fixed/85 text-on-primary-fixed/85",
    blob: "-right-4 -top-4 size-32 bg-secondary-container/50 blur-xl",
  },
  // Live call (saathi_live_voice_companion): pearl, lavender and sprout green.
  pearl: {
    background: "radial-gradient(circle at 35% 30%, #ffffff 0%, #e2daf8 45%, #c8ea89 75%, #9f8bd4 100%)",
    boxShadow:
      "rgba(101,84,151,0.28) 0 24px 50px -10px, rgba(75,103,0,0.18) 0 -12px 28px inset, rgba(255,255,255,0.95) 0 12px 24px inset",
    ink: "bg-on-surface",
    smile: "border-on-surface text-on-surface",
    blob: "bottom-6 right-8 h-8 w-16 bg-secondary-fixed/50 blur-[4px]",
  },
} as const;

const MOUTHS: Record<OrbState, string> = {
  idle: "h-3 w-6 rounded-b-full border-b-[3px]",
  listening: "h-2.5 w-4 rounded-full bg-current",
  thinking: "h-1 w-4 rounded-full bg-current",
  speaking: "h-4 w-7 rounded-b-full border-b-[3.5px]",
  interrupted: "h-3 w-4 rounded-full border-2",
  offline: "h-1 w-5 rounded-full bg-current opacity-60",
};

interface OrbProps {
  state: OrbState;
  tone?: keyof typeof TONES;
  /** Returns 0..1 (mic or output level); read every frame, never causes a React render. */
  getLevel?: () => number;
  /** Eyes follow the pointer (welcome screen). */
  trackPointer?: boolean;
}

/** Oryn: the companion orb. Reflects voice state and drives a thin audio-level ring. */
export function Orb({ state, tone = "pearl", getLevel, trackPointer = false }: OrbProps) {
  const palette = TONES[tone];
  const bodyRef = useRef<HTMLDivElement>(null);
  const ringRef = useRef<SVGCircleElement>(null);
  const faceRef = useRef<HTMLDivElement>(null);
  const eyesRef = useRef<HTMLDivElement>(null);
  const levelSource = useRef(getLevel);
  useEffect(() => {
    levelSource.current = getLevel;
  }, [getLevel]);

  // Level-driven body scale and ring, straight on the DOM at display rate.
  useEffect(() => {
    let frame = 0;
    let smooth = 0;
    const tick = () => {
      const target = levelSource.current?.() ?? 0;
      smooth += (target - smooth) * 0.25;
      if (bodyRef.current) bodyRef.current.style.transform = `scale(${1 + smooth * 0.1})`;
      const ring = ringRef.current;
      if (ring) {
        ring.style.transform = `scale(${1 + smooth * 0.05})`;
        ring.style.strokeWidth = String(0.35 + smooth * 1.1);
        ring.style.opacity = String(0.3 + Math.min(1, smooth * 1.6) * 0.7);
      }
      frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
  }, []);

  // Blinking, plus optional eye tracking.
  useEffect(() => {
    let blinkTimer: ReturnType<typeof setTimeout>;
    const blink = () => {
      const eyes = eyesRef.current;
      if (eyes) {
        eyes.style.transform = "scaleY(0.12)";
        setTimeout(() => eyes && (eyes.style.transform = "scaleY(1)"), 150);
      }
      blinkTimer = setTimeout(blink, 2500 + Math.random() * 4000);
    };
    blinkTimer = setTimeout(blink, 2000);

    const onPointer = (event: PointerEvent) => {
      const body = bodyRef.current;
      const face = faceRef.current;
      if (!body || !face) return;
      const rect = body.getBoundingClientRect();
      const dx = (event.clientX - (rect.left + rect.width / 2)) / (window.innerWidth / 2);
      const dy = (event.clientY - (rect.top + rect.height / 2)) / (window.innerHeight / 2);
      face.style.transform = `translate(${Math.max(-10, Math.min(10, dx * 12))}px, ${Math.max(-8, Math.min(8, dy * 10))}px)`;
    };
    if (trackPointer) window.addEventListener("pointermove", onPointer, { passive: true });

    return () => {
      clearTimeout(blinkTimer);
      window.removeEventListener("pointermove", onPointer);
    };
  }, [trackPointer]);

  return (
    <div className="relative grid size-72 place-items-center" aria-hidden="true">
      {/* ambient halos */}
      <div className="absolute size-72 animate-pulse rounded-full bg-primary-container/20 blur-2xl [animation-duration:4s]" />
      <div className="absolute size-60 rounded-full bg-secondary-container/25 blur-xl" />

      {/* thin audio-level ring */}
      <svg className="absolute inset-0 size-full" viewBox="0 0 100 100">
        <circle
          ref={ringRef}
          cx="50"
          cy="50"
          r="48"
          fill="none"
          className={state === "speaking" ? "stroke-primary" : "stroke-secondary"}
          style={{ strokeWidth: 0.35, opacity: 0.3, transformOrigin: "50% 50%" }}
        />
      </svg>

      {/* listening ripples */}
      {state === "listening" && (
        <>
          <div className="absolute size-56 animate-ripple rounded-full border border-secondary/40" />
          <div className="absolute size-56 animate-ripple rounded-full border border-primary/30 [animation-delay:1.3s]" />
        </>
      )}

      <div className={state === "idle" ? "animate-breathe" : ""}>
        <div
          ref={bodyRef}
          className={`relative grid size-52 place-items-center overflow-hidden rounded-full transition-[filter,opacity] duration-500 sm:size-56 ${
            state === "offline" ? "opacity-60 saturate-50" : ""
          } ${state === "interrupted" ? "scale-95" : ""}`}
          style={{ background: palette.background, boxShadow: palette.boxShadow, willChange: "transform" }}
        >
          <div className="pointer-events-none absolute left-9 top-5 h-9 w-20 -rotate-[25deg] rounded-full bg-white/75 blur-[3px]" />
          <div className={`pointer-events-none absolute rounded-full ${palette.blob}`} />
          {state === "thinking" && (
            <div
              className="absolute inset-0 animate-shimmer opacity-70 mix-blend-overlay"
              style={{
                background: "conic-gradient(from 0deg, transparent 0 55%, rgba(255,255,255,0.9) 78%, transparent 100%)",
              }}
            />
          )}

          <div ref={faceRef} className="relative z-10 flex -translate-y-1 flex-col items-center transition-transform duration-150">
            <div ref={eyesRef} className="mb-2 flex items-center gap-9 transition-transform duration-150">
              {[0, 1].map((eye) => (
                <div key={eye} className={`relative h-6 w-5 overflow-hidden rounded-full ${palette.ink}`}>
                  <span className="absolute left-1 top-1 size-2 rounded-full bg-white" />
                  <span className="absolute bottom-1 right-1 size-1 rounded-full bg-white/80" />
                </div>
              ))}
            </div>
            <div className={`${MOUTHS[state]} ${palette.smile} transition-all duration-300`} />
          </div>
        </div>
      </div>
    </div>
  );
}
