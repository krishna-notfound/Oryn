import { createServerClient } from "@supabase/ssr";
import { cookies } from "next/headers";
import { config } from "../config";

/** Server client bound to the request cookies (Server Components, Route Handlers). */
export async function createClient() {
  const cookieStore = await cookies();
  return createServerClient(config.supabaseUrl, config.supabaseAnonKey, {
    cookies: {
      getAll: () => cookieStore.getAll(),
      setAll: (cookiesToSet) => {
        try {
          cookiesToSet.forEach(({ name, value, options }) => cookieStore.set(name, value, options));
        } catch {
          // Called from a Server Component: the proxy refreshes the session instead.
        }
      },
    },
  });
}
