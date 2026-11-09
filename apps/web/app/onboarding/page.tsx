import { redirect } from "next/navigation";
import { OnboardingFlow } from "@/components/OnboardingFlow";
import { getUserAndProfile } from "@/lib/supabase/session";

export default async function Page() {
  const { user, profile } = await getUserAndProfile();
  if (profile?.onboarded_at) redirect("/talk");

  // Google sign-in provides a full name; use its first word as a starting suggestion.
  const metaName = typeof user.user_metadata?.full_name === "string" ? user.user_metadata.full_name : "";
  return <OnboardingFlow userId={user.id} initialName={profile?.preferred_name ?? metaName.split(" ")[0] ?? ""} />;
}
