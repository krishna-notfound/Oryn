import { ChatScreen } from "@/components/ChatScreen";
import { requireOnboardedUser } from "@/lib/supabase/session";

export default async function Page() {
  const { profile } = await requireOnboardedUser();
  return <ChatScreen language={profile.primary_language} />;
}
