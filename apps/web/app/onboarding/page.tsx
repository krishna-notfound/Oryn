import { redirect } from "next/navigation";
import { OnboardingFlow } from "@/components/OnboardingFlow";
import { getUserAndProfile } from "@/lib/supabase/session";

export default async function Page() {
  const { user, profile } = await getUserAndProfile();
  if (profile?.onboarded_at) redirect("/talk");

  // Guests start from their username (getUserAndProfile guarantees they have one); Google
  // sign-in provides a full name whose first word is a good starting suggestion.
  const metaName = typeof user.user_metadata?.full_name === "string" ? user.user_metadata.full_name : "";
  const suggestion = user.is_anonymous ? (profile?.username ?? "") : (metaName.split(" ")[0] ?? "");
  return <OnboardingFlow userId={user.id} initialName={profile?.preferred_name ?? suggestion} />;
}
