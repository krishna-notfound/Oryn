import { WelcomeScreen } from "@/components/WelcomeScreen";

// Signed-in users are redirected to /talk by proxy.ts before this renders.
export default async function Page({ searchParams }: { searchParams: Promise<{ auth_error?: string }> }) {
  const { auth_error } = await searchParams;
  return <WelcomeScreen authError={Boolean(auth_error)} />;
}
