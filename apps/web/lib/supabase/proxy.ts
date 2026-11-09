import { createServerClient } from "@supabase/ssr";
import { NextResponse, type NextRequest } from "next/server";
import { config } from "../config";

const PROTECTED_PREFIXES = ["/onboarding", "/talk", "/chat", "/settings", "/memories"];

/**
 * Refreshes the Supabase session cookie on every request and applies the coarse route
 * guard: signed-out users go to the welcome screen, signed-in users skip it. Whether the
 * user has finished onboarding is checked in the pages (it needs a profile read).
 */
export async function updateSession(request: NextRequest): Promise<NextResponse> {
  let response = NextResponse.next({ request });

  const supabase = createServerClient(config.supabaseUrl, config.supabaseAnonKey, {
    cookies: {
      getAll: () => request.cookies.getAll(),
      setAll: (cookiesToSet) => {
        cookiesToSet.forEach(({ name, value }) => request.cookies.set(name, value));
        response = NextResponse.next({ request });
        cookiesToSet.forEach(({ name, value, options }) => response.cookies.set(name, value, options));
      },
    },
  });

  // Validates the JWT (and refreshes it when expired); do not put logic between client creation and this call.
  const { data } = await supabase.auth.getClaims();
  const signedIn = Boolean(data?.claims);
  const isGuest = data?.claims?.is_anonymous === true;
  const { pathname, searchParams } = request.nextUrl;

  const redirectTo = (path: string) => {
    const redirect = NextResponse.redirect(new URL(path, request.url));
    // Keep any refreshed session cookies on the redirect.
    response.cookies.getAll().forEach((cookie) => redirect.cookies.set(cookie));
    return redirect;
  };

  if (!signedIn && PROTECTED_PREFIXES.some((prefix) => pathname.startsWith(prefix))) return redirectTo("/");
  // Guests (anonymous users) count as signed in; they only see the welcome screen to pick a username.
  if (signedIn && pathname === "/" && !(isGuest && searchParams.has("pick"))) return redirectTo("/talk");
  return response;
}
