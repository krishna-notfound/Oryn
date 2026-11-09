import { WelcomeScreen } from "@/components/WelcomeScreen";

// Signed-in users are redirected to /talk by proxy.ts before this renders, except guests who
// still need a username (/?pick=1).
export default async function Page({ searchParams }: { searchParams: Promise<{ auth_error?: string; pick?: string }> }) {
  const { auth_error, pick } = await searchParams;
  return <WelcomeScreen authError={Boolean(auth_error)} needsUsername={Boolean(pick)} />;
}
