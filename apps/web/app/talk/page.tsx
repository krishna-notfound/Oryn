import { TalkScreen } from "@/components/TalkScreen";
import { requireOnboardedUser } from "@/lib/supabase/session";

export default async function Page() {
  const { profile } = await requireOnboardedUser();
  return <TalkScreen name={profile.preferred_name} language={profile.primary_language} />;
}
