/** Mirrors the `profiles` table in docs/context/data-and-memory.md. */

/** Languages supported by both Gnani Prisma (STT) and Gnani Timbre (TTS). */
export const LANGUAGES = [
  { code: "hi-IN", name: "Hindi", native: "हिन्दी" },
  { code: "en-IN", name: "English", native: "English" },
  { code: "bn-IN", name: "Bengali", native: "বাংলা" },
  { code: "gu-IN", name: "Gujarati", native: "ગુજરાતી" },
  { code: "kn-IN", name: "Kannada", native: "ಕನ್ನಡ" },
  { code: "ml-IN", name: "Malayalam", native: "മലയാളം" },
  { code: "mr-IN", name: "Marathi", native: "मराठी" },
  { code: "pa-IN", name: "Punjabi", native: "ਪੰਜਾਬੀ" },
  { code: "ta-IN", name: "Tamil", native: "தமிழ்" },
  { code: "te-IN", name: "Telugu", native: "తెలుగు" },
] as const;

export type LanguageCode = (typeof LANGUAGES)[number]["code"];

export function languageLabel(code: string): string {
  const language = LANGUAGES.find((entry) => entry.code === code);
  return language ? `${language.name} (${language.native})` : code;
}

export const STYLES = [
  { id: "friendly", emoji: "🤗", title: "Warm & Friendly", hint: "Gentle listener, supportive and caring" },
  { id: "playful", emoji: "😄", title: "Playful & Witty", hint: "Fun banter, light humor, cheerful vibes" },
  { id: "calm", emoji: "🌿", title: "Calm & Mindful", hint: "Slow-paced, grounding, and peaceful" },
  { id: "thoughtful", emoji: "💭", title: "Thoughtful & Deep", hint: "Reflective questions, room to think" },
  { id: "direct", emoji: "🎯", title: "Direct & Focused", hint: "Concise thoughts, clear and actionable" },
] as const;

export type StyleId = (typeof STYLES)[number]["id"];

export const INTERESTS = [
  "Music",
  "Movies",
  "Cricket",
  "Food",
  "Travel",
  "Books",
  "Tech",
  "Fitness",
  "Gaming",
  "Career",
  "Study",
  "Family",
  "Art",
  "Spirituality",
] as const;

export interface Profile {
  id: string;
  preferred_name: string | null;
  timezone: string;
  primary_language: string;
  extra_languages: string[];
  response_language: string;
  code_switching: boolean;
  interests: string[];
  style: StyleId;
  response_length: "short" | "detailed";
  listen_first: boolean;
  formality: "informal" | "formal";
  voice: string | null;
  memory_enabled: boolean;
  save_history: boolean;
  onboarded_at: string | null;
}

/** What the browser may write; `id` is always the signed-in user. */
export type ProfileInput = Omit<Profile, "id" | "onboarded_at">;

export const DEFAULT_PROFILE: ProfileInput = {
  preferred_name: null,
  timezone: "Asia/Kolkata",
  primary_language: "hi-IN",
  extra_languages: ["en-IN"],
  response_language: "auto",
  code_switching: true,
  interests: [],
  style: "friendly",
  response_length: "short",
  listen_first: true,
  formality: "informal",
  voice: null,
  memory_enabled: false,
  save_history: true,
};
