// NEXT_PUBLIC_* must be referenced literally so Next.js can inline them into the bundle.
export const config = {
  supabaseUrl: process.env.NEXT_PUBLIC_SUPABASE_URL ?? "",
  supabaseAnonKey: process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY ?? "",
  backendWsUrl: process.env.NEXT_PUBLIC_BACKEND_WS_URL ?? "ws://localhost:8000/ws",
  // Google sign-in / account linking stays hidden until the provider is set up in Supabase.
  googleAuthEnabled: process.env.NEXT_PUBLIC_GOOGLE_AUTH_ENABLED === "true",
  backendHttpUrl: (process.env.NEXT_PUBLIC_BACKEND_HTTP_URL ?? "http://localhost:8000").replace(/\/$/, ""),
};
