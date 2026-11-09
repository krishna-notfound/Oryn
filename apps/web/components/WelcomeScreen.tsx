"use client";

import { useState, type FormEvent } from "react";
import { useRouter } from "next/navigation";
import { Mail } from "lucide-react";
import { createClient } from "@/lib/supabase/client";
import { Orb, type OrbState } from "./Orb";
import { GoogleIcon, Wordmark } from "./ui";

type Mode = "choose" | "sign-in" | "sign-up";

const inputClass =
  "type-body-md h-14 w-full rounded-full border border-outline-variant/40 bg-surface-container-lowest px-5 text-on-surface shadow-sm placeholder:text-outline/70 focus:border-primary-container focus:outline-none focus:ring-4 focus:ring-primary-container/25";

export function WelcomeScreen({ authError }: { authError: boolean }) {
  const router = useRouter();
  const [mode, setMode] = useState<Mode>("choose");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(authError ? "Sign-in didn't complete. Please try again." : null);
  const [orbState, setOrbState] = useState<OrbState>("idle");

  async function signInWithGoogle() {
    setBusy(true);
    setMessage(null);
    const { error } = await createClient().auth.signInWithOAuth({
      provider: "google",
      options: { redirectTo: `${window.location.origin}/auth/callback` },
    });
    if (error) {
      setMessage(error.message);
      setBusy(false);
    }
  }

  async function submitEmail(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setMessage(null);
    const supabase = createClient();

    if (mode === "sign-up") {
      const { data, error } = await supabase.auth.signUp({
        email,
        password,
        options: { emailRedirectTo: `${window.location.origin}/auth/callback` },
      });
      if (error) setMessage(error.message);
      else if (data.session) return goHome();
      else setMessage("Check your inbox to confirm your email, then log in.");
    } else {
      const { error } = await supabase.auth.signInWithPassword({ email, password });
      if (error) setMessage(error.message);
      else return goHome();
    }
    setBusy(false);
  }

  function goHome() {
    router.replace("/talk");
    router.refresh();
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
        {mode === "choose" ? (
          <>
            <button
              type="button"
              onClick={signInWithGoogle}
              disabled={busy}
              className="type-label-lg flex min-h-14 w-full items-center justify-center gap-3 rounded-full bg-surface-container-lowest px-space-md text-on-surface shadow-sm transition-all hover:shadow-md active:scale-[0.985] disabled:opacity-60"
            >
              <GoogleIcon />
              Continue with Google
            </button>
            <button
              type="button"
              onClick={() => setMode("sign-up")}
              className="type-label-lg flex min-h-14 w-full items-center justify-center gap-2.5 rounded-full bg-primary-fixed/40 px-space-md text-on-primary-fixed-variant transition-all hover:bg-primary-fixed/60 active:scale-[0.985]"
            >
              <Mail size={20} className="text-primary" aria-hidden="true" />
              Continue with email
            </button>
          </>
        ) : (
          <form onSubmit={submitEmail} className="flex w-full flex-col gap-space-sm">
            <label className="sr-only" htmlFor="email">Email</label>
            <input
              id="email"
              type="email"
              autoComplete="email"
              required
              placeholder="Email"
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              className={inputClass}
            />
            <label className="sr-only" htmlFor="password">Password</label>
            <input
              id="password"
              type="password"
              autoComplete={mode === "sign-up" ? "new-password" : "current-password"}
              required
              minLength={6}
              placeholder="Password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              className={inputClass}
            />
            <button
              type="submit"
              disabled={busy}
              className="type-label-lg min-h-14 w-full rounded-full bg-primary text-on-primary shadow-md transition-all active:scale-[0.985] disabled:opacity-60"
            >
              {busy ? "One moment…" : mode === "sign-up" ? "Create account" : "Log in"}
            </button>
            <button
              type="button"
              onClick={() => {
                setMode("choose");
                setMessage(null);
              }}
              className="type-label-md min-h-11 text-on-surface-variant"
            >
              Other ways to continue
            </button>
          </form>
        )}

        {message && (
          <p role="status" className="type-body-sm text-center text-on-surface-variant">
            {message}
          </p>
        )}

        <p className="type-body-sm pt-space-xs text-center text-outline">
          {mode === "sign-up" || mode === "choose" ? "Already have an account?" : "New to Oryn?"}
          <button
            type="button"
            onClick={() => {
              setMode(mode === "sign-in" ? "sign-up" : "sign-in");
              setMessage(null);
            }}
            className="type-label-md ml-1 min-h-11 px-1 font-semibold text-primary underline-offset-4 hover:underline"
          >
            {mode === "sign-in" ? "Sign up" : "Log in"}
          </button>
        </p>
      </footer>
    </main>
  );
}
