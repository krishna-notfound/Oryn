import "server-only";
import { redirect } from "next/navigation";
import type { User } from "@supabase/supabase-js";
import type { Profile } from "../profile";
import { createClient } from "./server";

/** Verified user (round-trips to Supabase Auth) plus their profile row, if any. */
export async function getUserAndProfile(): Promise<{ user: User; profile: Profile | null }> {
  const supabase = await createClient();
  const { data } = await supabase.auth.getUser();
  if (!data.user) redirect("/");

  const { data: profile } = await supabase
    .from("profiles")
    .select("*")
    .eq("id", data.user.id)
    .maybeSingle<Profile>();
  // A guest without a username (e.g. the tab closed after sign-in) finishes that step first.
  if (data.user.is_anonymous && !profile?.username) redirect("/?pick=1");
  return { user: data.user, profile };
}

/** For app screens: sends users who have not finished onboarding to /onboarding. */
export async function requireOnboardedUser(): Promise<{ user: User; profile: Profile }> {
  const { user, profile } = await getUserAndProfile();
  if (!profile?.onboarded_at) redirect("/onboarding");
  return { user, profile };
}
