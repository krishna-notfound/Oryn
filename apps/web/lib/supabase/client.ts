import { createBrowserClient } from "@supabase/ssr";
import { config } from "../config";

/** Browser client (anon key + RLS). Create lazily inside handlers, not during render. */
export function createClient() {
  return createBrowserClient(config.supabaseUrl, config.supabaseAnonKey);
}

/** Current access token for the backend WebSocket and REST calls. */
export async function getAccessToken(): Promise<string> {
  const { data, error } = await createClient().auth.getSession();
  if (error || !data.session) throw new Error("Not signed in");
  return data.session.access_token;
}
