import { SettingsScreen } from "@/components/SettingsScreen";
import { requireOnboardedUser } from "@/lib/supabase/session";

export default async function Page() {
  const { user, profile } = await requireOnboardedUser();
  return <SettingsScreen initial={profile} email={user.email ?? ""} isGuest={Boolean(user.is_anonymous)} />;
}
