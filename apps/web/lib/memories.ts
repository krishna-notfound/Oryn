import { config } from "./config";
import { getAccessToken } from "./supabase/client";

/** One long-term memory as returned by the backend `/memories` REST API. */
export interface Memory {
  id: string;
  content: string;
  category?: string;
  created_at?: string;
  updated_at?: string;
}

async function request(path: string, init?: RequestInit): Promise<Response> {
  const token = await getAccessToken();
  const response = await fetch(`${config.backendHttpUrl}${path}`, {
    ...init,
    headers: { ...init?.headers, Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
  });
  if (!response.ok) throw new Error(`Request failed (${response.status})`);
  return response;
}

/** Accepts either a bare array or an object wrapping it as `memories` / `items`. */
export async function listMemories(): Promise<Memory[]> {
  const body: unknown = await (await request("/memories")).json();
  if (Array.isArray(body)) return body as Memory[];
  const wrapped = body as { memories?: Memory[]; items?: Memory[] };
  return wrapped.memories ?? wrapped.items ?? [];
}

export async function updateMemory(id: string, content: string): Promise<void> {
  await request(`/memories/${encodeURIComponent(id)}`, { method: "PATCH", body: JSON.stringify({ content }) });
}

export async function deleteMemory(id: string): Promise<void> {
  await request(`/memories/${encodeURIComponent(id)}`, { method: "DELETE" });
}
