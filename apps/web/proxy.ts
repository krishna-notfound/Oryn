import type { NextRequest } from "next/server";
import { updateSession } from "@/lib/supabase/proxy";

export function proxy(request: NextRequest) {
  return updateSession(request);
}

export const config = {
  // Skip static assets, the audio worklet and image files.
  matcher: ["/((?!_next/static|_next/image|favicon.ico|mic-worklet.js|.*\\.(?:svg|png|jpg|jpeg|gif|webp)$).*)"],
};
