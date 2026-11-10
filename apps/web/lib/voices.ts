/**
 * Languages and Gnani Timbre voices (mirror of backend/app/voice/catalog.py, served by
 * `GET /voices`). The static copy below is the fallback when the request fails, so the pickers
 * always work. No runtime imports: unit-testable in Node.
 */

/** `profiles.primary_language` value meaning "detect the language of every utterance". */
export const AUTO_LANGUAGE = "auto";
export const AUTO_LABEL = "Auto-detect";

export type Gender = "female" | "male";

export interface VoiceLanguage {
  code: string;
  name: string;
}

export interface VoiceInfo {
  name: string;
  gender: Gender;
  /** Preferred language code. */
  language: string;
  /** Also speaks Hinglish, i.e. English with Hindi mixed in. */
  hinglish: boolean;
}

export interface VoiceCatalog {
  languages: VoiceLanguage[];
  voices: VoiceInfo[];
  /** Language code -> voice used when the user has not picked one. */
  defaults: Record<string, string>;
}

function voicesOf(language: string, female: string, male = ""): VoiceInfo[] {
  const make = (names: string, gender: Gender) =>
    names.split(" ").filter(Boolean).map((name) => ({ name, gender, language, hinglish: false }));
  return [...make(female, "female"), ...make(male, "male")];
}

export const STATIC_CATALOG: VoiceCatalog = {
  languages: [
    { code: "en-IN", name: "English" },
    { code: "hi-IN", name: "Hindi" },
    { code: "bn-IN", name: "Bengali" },
    { code: "gu-IN", name: "Gujarati" },
    { code: "kn-IN", name: "Kannada" },
    { code: "ml-IN", name: "Malayalam" },
    { code: "mr-IN", name: "Marathi" },
    { code: "pa-IN", name: "Punjabi" },
    { code: "ta-IN", name: "Tamil" },
    { code: "te-IN", name: "Telugu" },
  ],
  voices: [
    { name: "Poorvi", gender: "female", language: "hi-IN", hinglish: true },
    ...voicesOf("hi-IN", "Ambuja Bhavna Chitra Jwala Nalini Urmila Yashvi", "Deepak Hemraj Jalaj Omkar Roopesh Vikrant"),
    ...voicesOf("en-IN", "Devika Kaveri Trupti", "Girish Pranav Shlok"),
    ...voicesOf("ta-IN", "Asmita Brinda Noopur Trisha Vedika"),
    ...voicesOf("te-IN", "Lavanya Lehara Suhana Varuni Yukti"),
    ...voicesOf("kn-IN", "Saanvi", "Kavin"),
    ...voicesOf("ml-IN", "Reshma", "Riyaan"),
    ...voicesOf("mr-IN", "Zahira", "Ishaan"),
    ...voicesOf("pa-IN", "Mehuli", "Zayan"),
    ...voicesOf("bn-IN", "Kirra", "Dhruva"),
    ...voicesOf("gu-IN", "Falak", "Veera"),
  ],
  defaults: {
    "hi-IN": "Poorvi",
    "en-IN": "Poorvi",
    "ta-IN": "Asmita",
    "te-IN": "Lavanya",
    "kn-IN": "Saanvi",
    "ml-IN": "Reshma",
    "mr-IN": "Zahira",
    "pa-IN": "Mehuli",
    "bn-IN": "Kirra",
    "gu-IN": "Falak",
  },
};

const isRecord = (value: unknown): value is Record<string, unknown> => typeof value === "object" && value !== null;

/**
 * Validates a `/voices` payload. Malformed entries are skipped rather than failing the whole
 * catalogue; returns null when nothing usable is left (callers then use STATIC_CATALOG).
 */
export function parseCatalog(value: unknown): VoiceCatalog | null {
  if (!isRecord(value) || !Array.isArray(value.languages) || !Array.isArray(value.voices)) return null;
  const languages: VoiceLanguage[] = value.languages.flatMap((entry) =>
    isRecord(entry) && typeof entry.code === "string" && typeof entry.name === "string"
      ? [{ code: entry.code, name: entry.name }]
      : [],
  );
  const voices: VoiceInfo[] = value.voices.flatMap((entry) =>
    isRecord(entry) &&
    typeof entry.name === "string" &&
    typeof entry.language === "string" &&
    (entry.gender === "female" || entry.gender === "male")
      ? [{ name: entry.name, gender: entry.gender, language: entry.language, hinglish: entry.hinglish === true }]
      : [],
  );
  if (languages.length === 0 || voices.length === 0) return null;
  const defaults: Record<string, string> = {};
  if (isRecord(value.defaults)) {
    for (const [code, name] of Object.entries(value.defaults)) if (typeof name === "string") defaults[code] = name;
  }
  return { languages, voices, defaults };
}

export interface LanguageOption extends VoiceLanguage {
  auto: boolean;
}

/** Settings/onboarding language list: Auto-detect first, then every language the server speaks. */
export function languageOptions(catalog: VoiceCatalog): LanguageOption[] {
  return [
    { code: AUTO_LANGUAGE, name: AUTO_LABEL, auto: true },
    ...catalog.languages.map((language) => ({ ...language, auto: false })),
  ];
}

export interface VoiceGroup {
  language: VoiceLanguage;
  voices: VoiceInfo[];
}

/** Voices grouped under their preferred language, in the catalogue's language order. */
export function groupVoices(catalog: VoiceCatalog): VoiceGroup[] {
  return catalog.languages
    .map((language) => ({ language, voices: catalog.voices.filter((voice) => voice.language === language.code) }))
    .filter((group) => group.voices.length > 0);
}

export function genderLabel(gender: Gender): string {
  return gender === "female" ? "Female" : "Male";
}

export function findVoice(catalog: VoiceCatalog, name: string | null): VoiceInfo | undefined {
  return name ? catalog.voices.find((voice) => voice.name.toLowerCase() === name.toLowerCase()) : undefined;
}

/** "Poorvi · Female", or the default-voice label when none is picked. */
export function voiceSummary(catalog: VoiceCatalog, name: string | null): string {
  const voice = findVoice(catalog, name);
  if (voice) return `${voice.name} · ${genderLabel(voice.gender)}`;
  return name ?? "Default voice";
}

export function previewPath(voice: VoiceInfo): string {
  return `/voices/${encodeURIComponent(voice.name)}/preview?language=${encodeURIComponent(voice.language)}`;
}

export type PreviewResult =
  | { kind: "ok"; audio: ArrayBuffer; sampleRate: number | null }
  | { kind: "unavailable" }
  | { kind: "error" };

/** Default PCM rate of the preview endpoint when it answers raw PCM without a rate header. */
export const PREVIEW_PCM_RATE = 24_000;

/**
 * Fetches a voice sample. 503 (Timbre busy, rate-limited) is "unavailable" rather than an error:
 * the UI says to try again in a moment. Never throws.
 */
export async function fetchPreview(
  baseUrl: string,
  token: string,
  voice: VoiceInfo,
  fetchFn: typeof fetch = fetch,
): Promise<PreviewResult> {
  try {
    const response = await fetchFn(`${baseUrl}${previewPath(voice)}`, { headers: { Authorization: `Bearer ${token}` } });
    if (response.status === 503 || response.status === 429) return { kind: "unavailable" };
    if (!response.ok) return { kind: "error" };
    const rate = Number(response.headers.get("X-Sample-Rate"));
    return { kind: "ok", audio: await response.arrayBuffer(), sampleRate: Number.isFinite(rate) && rate > 0 ? rate : null };
  } catch {
    return { kind: "error" };
  }
}

export async function fetchCatalog(baseUrl: string, token: string, fetchFn: typeof fetch = fetch): Promise<VoiceCatalog> {
  try {
    const response = await fetchFn(`${baseUrl}/voices`, { headers: { Authorization: `Bearer ${token}` } });
    if (!response.ok) return STATIC_CATALOG;
    return parseCatalog(await response.json()) ?? STATIC_CATALOG;
  } catch {
    return STATIC_CATALOG;
  }
}
