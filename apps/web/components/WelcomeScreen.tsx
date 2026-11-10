"use client";

import Link from "next/link";
import { useState } from "react";
import { UserRound } from "lucide-react";
import { createClient } from "@/lib/supabase/client";
import { authCallbackUrl } from "@/lib/auth/guest";
import { config } from "@/lib/config";
import { Orb, type OrbState } from "./Orb";
import { GoogleIcon, Wordmark } from "./ui";
import { GuestForm } from "./GuestForm";

export function WelcomeScreen({ authError, needsUsername }: { authError: boolean; needsUsername: boolean }) {
  const { googleAuthEnabled } = config;
  // Without Google, the username form is the only way in, so show it straight away.
  const [guestOpen, setGuestOpen] = useState(!googleAuthEnabled || needsUsername);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(
    authError
      ? "Sign-in didn't complete. Please try again."
      : needsUsername
        ? "Pick a username to finish setting up your guest account."
        : null,
  );
  const [orbState, setOrbState] = useState<OrbState>("idle");

  async function signInWithGoogle() {
    setBusy(true);
    setMessage(null);
    const { error } = await createClient().auth.signInWithOAuth({
      provider: "google",
      options: { redirectTo: authCallbackUrl() },
    });
    if (error) {
      setMessage(error.message);
      setBusy(false);
    }
  }

  return (
    <main className="page-col pt-safe pb-safe flex min-h-dvh flex-col items-center px-margin">
      <header className="flex w-full justify-center py-space-md">
        <Wordmark size="lg" />
      </header>

      <section className="flex w-full flex-1 flex-col items-center justify-center py-space-md">
        <button
          type="button"
          aria-label="Oryn"
          className="cursor-pointer rounded-full"
          onClick={() => {
            setOrbState("speaking");
            setTimeout(() => setOrbState("idle"), 500);
          }}
        >
          <Orb state={orbState} tone="lavender" trackPointer />
        </button>

        <div className="mt-space-sm flex max-w-xs flex-col items-center px-space-sm text-center">
          <h1 className="type-headline-lg-mobile tracking-tight text-on-primary-fixed">Someone to talk to.</h1>
          <p className="type-body-md mt-space-xs text-balance text-outline">
            A warm, voice-first companion ready whenever you want to talk.
          </p>
          <span className="type-label-sm mt-space-sm inline-flex items-center gap-1.5 rounded-full bg-secondary-container/20 px-3 py-1 text-on-secondary-fixed">
            <span className="size-2 animate-pulse rounded-full bg-secondary" />
            Always here for you
          </span>
        </div>
      </section>

      <footer className="flex w-full max-w-sm flex-col items-center gap-space-sm pb-space-md">
        {googleAuthEnabled && (
          <button
            type="button"
            onClick={signInWithGoogle}
            disabled={busy}
            className="type-label-lg flex min-h-14 w-full items-center justify-center gap-3 rounded-full bg-surface-container-lowest px-space-md text-on-surface shadow-sm transition-all hover:shadow-md active:scale-[0.985] disabled:opacity-60"
          >
            <GoogleIcon />
            Continue with Google
          </button>
        )}

        {guestOpen ? (
          <GuestForm disabled={busy} onMessage={setMessage} />
        ) : (
          <button
            type="button"
            onClick={() => setGuestOpen(true)}
            className="type-label-lg flex min-h-14 w-full items-center justify-center gap-2.5 rounded-full bg-primary-fixed/40 px-space-md text-on-primary-fixed-variant transition-all hover:bg-primary-fixed/60 active:scale-[0.985]"
          >
            <UserRound size={20} className="text-primary" aria-hidden="true" />
            Continue as guest
          </button>
        )}

        {message && (
          <p role="status" className="type-body-sm text-center text-on-surface-variant">
            {message}
          </p>
        )}

        <Link href="/privacy" className="type-label-sm text-outline underline-offset-4 hover:underline">
          Privacy Policy
        </Link>
      </footer>
    </main>
  );
}
