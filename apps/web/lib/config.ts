// NEXT_PUBLIC_* must be referenced literally so Next.js can inline them into the bundle.
export const config = {
  supabaseUrl: process.env.NEXT_PUBLIC_SUPABASE_URL ?? "",
  supabaseAnonKey: process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY ?? "",
  backendWsUrl: process.env.NEXT_PUBLIC_BACKEND_WS_URL ?? "ws://localhost:8000/ws",
  backendHttpUrl: (process.env.NEXT_PUBLIC_BACKEND_HTTP_URL ?? "http://localhost:8000").replace(/\/$/, ""),
};
