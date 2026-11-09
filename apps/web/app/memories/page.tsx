import { MemoriesScreen } from "@/components/MemoriesScreen";
import { requireOnboardedUser } from "@/lib/supabase/session";

export default async function Page() {
  const { profile } = await requireOnboardedUser();
  return <MemoriesScreen memoryEnabled={profile.memory_enabled} />;
}
