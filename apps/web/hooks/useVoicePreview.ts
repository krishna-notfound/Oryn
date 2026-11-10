"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { isWav, pcmToWav } from "@/lib/audio/wav";
import { config } from "@/lib/config";
import { getAccessToken } from "@/lib/supabase/client";
import { fetchPreview, PREVIEW_PCM_RATE, type VoiceInfo } from "@/lib/voices";

export interface VoicePreviewState {
  /** Voice being fetched or played. */
  active: string | null;
  loading: boolean;
  message: string | null;
}

/** Plays one voice sample at a time; tapping the playing voice again stops it. */
export function useVoicePreview() {
  const [state, setState] = useState<VoicePreviewState>({ active: null, loading: false, message: null });
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const urlRef = useRef<string | null>(null);
  /** Bumped on every request so a slow earlier response cannot start after a newer tap. */
  const requestRef = useRef(0);

  const release = useCallback(() => {
    audioRef.current?.pause();
    audioRef.current = null;
    if (urlRef.current) URL.revokeObjectURL(urlRef.current);
    urlRef.current = null;
  }, []);

  const stop = useCallback(() => {
    requestRef.current += 1;
    release();
    setState({ active: null, loading: false, message: null });
  }, [release]);

  const play = useCallback(
    async (voice: VoiceInfo) => {
      if (state.active === voice.name) return stop();
      release();
      const request = ++requestRef.current;
      setState({ active: voice.name, loading: true, message: null });
      const fail = (message: string) => {
        if (request === requestRef.current) setState({ active: null, loading: false, message });
      };

      let token: string;
      try {
        token = await getAccessToken();
      } catch {
        return fail("Sign in again to hear voice samples.");
      }
      const result = await fetchPreview(config.backendHttpUrl, token, voice);
      if (request !== requestRef.current) return;
      if (result.kind === "unavailable") return fail("Voice samples are busy right now. Try again in a moment.");
      if (result.kind === "error") return fail("Could not load that sample.");

      const wav = isWav(result.audio) ? result.audio : pcmToWav(result.audio, result.sampleRate ?? PREVIEW_PCM_RATE);
      const url = URL.createObjectURL(new Blob([wav], { type: "audio/wav" }));
      urlRef.current = url;
      const audio = new Audio(url);
      audioRef.current = audio;
      audio.onended = () => {
        if (request === requestRef.current) stop();
      };
      try {
        await audio.play();
        if (request === requestRef.current) setState({ active: voice.name, loading: false, message: null });
      } catch {
        release();
        fail("Could not play that sample.");
      }
    },
    [release, state.active, stop],
  );

  useEffect(() => release, [release]);

  return { ...state, play, stop };
}
