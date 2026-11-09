import { createClient } from "@/lib/supabase/client";

/** Same rule as the `profiles.username` check constraint. */
export const USERNAME_PATTERN = /^[A-Za-z0-9_]{3,20}$/;

export const USERNAME_HINT = "3–20 letters, numbers or underscores.";
export const USERNAME_TAKEN = "That username is taken — try another";

export type GuestResult = { ok: true; onboarded: boolean } | { ok: false; error: string };

/** Where Google (sign-in or linking) sends the browser back to. */
export function authCallbackUrl(next?: string): string {
  const url = new URL("/auth/callback", window.location.origin);
  if (next) url.searchParams.set("next", next);
  return url.toString();
}

/**
 * Signs in as a guest (Supabase anonymous user) and claims `username` on the profile row.
 * An existing anonymous session is reused, so retrying after "taken" keeps the same user.
 */
export async function continueAsGuest(username: string): Promise<GuestResult> {
  const supabase = createClient();

  const { data: existing } = await supabase.auth.getUser();
  let user = existing.user;
  if (!user) {
    const { data, error } = await supabase.auth.signInAnonymously();
    if (error || !data.user) {
      const disabled = error?.message.toLowerCase().includes("anonymous");
      return { ok: false, error: disabled ? "Guest sign-in isn't available right now. Please try again later." : "Couldn't start a guest session. Please try again." };
    }
    user = data.user;
  }

  // A profile row is created by a database trigger when the auth user is created.
  const { data: current } = await supabase.from("profiles").select("preferred_name").eq("id", user.id).maybeSingle();
  const { data: updated, error } = await supabase
    .from("profiles")
    .update({ username, preferred_name: current?.preferred_name ?? username, updated_at: new Date().toISOString() })
    .eq("id", user.id)
    .select("onboarded_at")
    .maybeSingle();

  if (error) return { ok: false, error: error.code === "23505" ? USERNAME_TAKEN : error.message };
  if (!updated) return { ok: false, error: "Couldn't set up your profile. Please try again." };
  return { ok: true, onboarded: Boolean(updated.onboarded_at) };
}
