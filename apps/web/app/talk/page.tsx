import { TalkScreen } from "@/components/TalkScreen";
import { requireOnboardedUser } from "@/lib/supabase/session";

export default async function Page({ searchParams }: { searchParams: Promise<{ [key: string]: string | string[] | undefined }> }) {
  const [{ profile }, query] = await Promise.all([requireOnboardedUser(), searchParams]);
  return <TalkScreen name={profile.preferred_name} language={profile.primary_language} debug={query.debug === "1"} />;
}
