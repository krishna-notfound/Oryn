"use client";

import { useState, type FormEvent } from "react";
import { useRouter } from "next/navigation";
import { continueAsGuest, USERNAME_HINT, USERNAME_PATTERN } from "@/lib/auth/guest";

const inputClass =
  "type-body-md h-14 w-full rounded-full border bg-surface-container-lowest px-5 text-on-surface shadow-sm placeholder:text-outline/70 focus:outline-none focus:ring-4";

/** Username field + submit for guest sign-in. Reports server-side messages through `onMessage`. */
export function GuestForm({ disabled, onMessage }: { disabled: boolean; onMessage: (message: string | null) => void }) {
  const router = useRouter();
  const [username, setUsername] = useState("");
  const [busy, setBusy] = useState(false);

  const invalid = username.length > 0 && !USERNAME_PATTERN.test(username);
  const valid = USERNAME_PATTERN.test(username);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!valid) return;
    setBusy(true);
    onMessage(null);
    const result = await continueAsGuest(username);
    if (!result.ok) {
      onMessage(result.error);
      setBusy(false);
      return;
    }
    router.replace(result.onboarded ? "/talk" : "/onboarding");
    router.refresh();
  }

  return (
    <form onSubmit={submit} className="flex w-full flex-col gap-space-sm">
      <label className="sr-only" htmlFor="username">Username</label>
      <input
        id="username"
        autoFocus
        autoComplete="off"
        autoCapitalize="none"
        spellCheck={false}
        maxLength={20}
        placeholder="Pick a username"
        value={username}
        aria-invalid={invalid}
        aria-describedby="username-hint"
        onChange={(event) => {
          setUsername(event.target.value.trim());
          onMessage(null);
        }}
        className={`${inputClass} ${
          invalid
            ? "border-error focus:border-error focus:ring-error/20"
            : "border-outline-variant/40 focus:border-primary-container focus:ring-primary-container/25"
        }`}
      />
      <p id="username-hint" className={`type-body-sm px-space-sm text-center ${invalid ? "text-error" : "text-outline"}`}>
        {USERNAME_HINT} It&apos;s a public handle, not a password.
      </p>
      <button
        type="submit"
        disabled={!valid || busy || disabled}
        className="type-label-lg min-h-14 w-full rounded-full bg-primary text-on-primary shadow-md transition-all active:scale-[0.985] disabled:opacity-50"
      >
        {busy ? "One moment…" : "Start as guest"}
      </button>
    </form>
  );
}
