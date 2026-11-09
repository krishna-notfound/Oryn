"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { LogOut, UserRound } from "lucide-react";
import { createClient } from "@/lib/supabase/client";
import { authCallbackUrl } from "@/lib/auth/guest";
import { config } from "@/lib/config";
import { GoogleIcon } from "./ui";

const CARD = "rounded-xl bg-surface-container-lowest p-space-md shadow-[0_4px_20px_-2px_rgba(31,32,36,0.04)]";

/** Account type, Google linking for guests, and sign-out with a warning that guests cannot be recovered. */
export function AccountSection({ isGuest, username, email }: { isGuest: boolean; username: string | null; email: string }) {
  const router = useRouter();
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [linkError, setLinkError] = useState<string | null>(null);

  async function linkGoogle() {
    setBusy(true);
    setLinkError(null);
    const { error } = await createClient().auth.linkIdentity({
      provider: "google",
      options: { redirectTo: authCallbackUrl("/settings") },
    });
    if (error) {
      setLinkError(
        /disabled|not enabled|manual linking/i.test(error.message)
          ? "Linking Google isn't available right now. Your guest account still works as it is."
          : error.message,
      );
      setBusy(false);
    }
  }

  async function signOut() {
    setBusy(true);
    await createClient().auth.signOut();
    router.replace("/");
    router.refresh();
  }

  return (
    <div className="mt-auto flex flex-col gap-space-sm pt-space-md">
      <div className={`${CARD} flex items-center gap-space-md`}>
        <span className="grid size-10 shrink-0 place-items-center rounded-full bg-surface-container text-on-surface-variant">
          {isGuest ? <UserRound size={20} aria-hidden="true" /> : <GoogleIcon />}
        </span>
        <div className="flex min-w-0 flex-col">
          <span className="type-label-sm text-on-surface-variant">Account</span>
          <span className="type-label-lg truncate text-on-surface">
            {isGuest ? `Guest @${username ?? "guest"}` : `Google · ${email}`}
          </span>
        </div>
      </div>

      {isGuest && config.googleAuthEnabled && (
        <div className="flex flex-col gap-space-xs">
          <button
            type="button"
            onClick={linkGoogle}
            disabled={busy}
            className={`${CARD} type-label-lg flex min-h-14 w-full items-center justify-center gap-3 text-on-surface transition-all hover:bg-surface-container active:scale-[0.99] disabled:opacity-60`}
          >
            <GoogleIcon />
            Link Google account
          </button>
          {linkError && (
            <p role="alert" className="type-body-sm px-space-sm text-error">
              {linkError}
            </p>
          )}
        </div>
      )}

      {confirming ? (
        <div role="alertdialog" aria-labelledby="signout-title" aria-describedby="signout-body" className={`${CARD} flex flex-col gap-space-sm`}>
          <p id="signout-title" className="type-label-lg text-on-surface">Sign out of this guest account?</p>
          <p id="signout-body" className="type-body-sm text-on-surface-variant">
            A guest account can&apos;t be recovered after you sign out
            {config.googleAuthEnabled ? " unless you've linked a Google account" : ""}. Your username and saved
            conversations here will be out of reach.
          </p>
          <div className="flex gap-space-sm">
            <button
              type="button"
              onClick={() => setConfirming(false)}
              className="type-label-lg min-h-12 flex-1 rounded-full bg-surface-container text-on-surface"
            >
              Stay signed in
            </button>
            <button
              type="button"
              onClick={signOut}
              disabled={busy}
              className="type-label-lg min-h-12 flex-1 rounded-full bg-error text-on-error disabled:opacity-60"
            >
              Sign out
            </button>
          </div>
        </div>
      ) : (
        <button
          type="button"
          onClick={() => (isGuest ? setConfirming(true) : void signOut())}
          disabled={busy}
          className={`${CARD} type-label-lg flex min-h-14 w-full items-center justify-center gap-2 text-on-surface transition-all hover:bg-surface-container active:scale-[0.99]`}
        >
          <LogOut size={20} className="text-outline" aria-hidden="true" />
          Sign out
        </button>
      )}

      <div className="flex flex-col items-center gap-0.5 pt-space-sm">
        <span className="type-label-sm text-outline">Oryn v1.0 · An AI companion you can actually talk to</span>
      </div>
    </div>
  );
}
