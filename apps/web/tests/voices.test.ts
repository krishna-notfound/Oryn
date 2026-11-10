import assert from "node:assert/strict";
import { describe, it } from "node:test";
import { isWav, pcmToWav } from "../lib/audio/wav";
import {
  AUTO_LANGUAGE,
  fetchCatalog,
  fetchPreview,
  findVoice,
  groupVoices,
  languageOptions,
  parseCatalog,
  previewPath,
  STATIC_CATALOG,
  voiceSummary,
} from "../lib/voices";

const response = (status: number, body: unknown = null, headers: Record<string, string> = {}) =>
  (async () =>
    new Response(body === null ? null : typeof body === "string" || body instanceof ArrayBuffer ? body : JSON.stringify(body), {
      status,
      headers,
    })) as unknown as typeof fetch;

describe("static catalogue (mirror of backend/app/voice/catalog.py)", () => {
  it("has 42 voices across the 10 shared languages, each language with a default voice", () => {
    assert.equal(STATIC_CATALOG.voices.length, 42);
    assert.equal(STATIC_CATALOG.languages.length, 10);
    for (const language of STATIC_CATALOG.languages) {
      const defaultVoice = findVoice(STATIC_CATALOG, STATIC_CATALOG.defaults[language.code]);
      assert.ok(defaultVoice, `default voice for ${language.code}`);
      assert.ok(defaultVoice.language === language.code || defaultVoice.hinglish);
    }
    assert.equal(new Set(STATIC_CATALOG.voices.map((v) => v.name)).size, 42, "voice names are unique");
  });

  it("only Poorvi is flagged Hinglish", () => {
    assert.deepEqual(STATIC_CATALOG.voices.filter((v) => v.hinglish).map((v) => v.name), ["Poorvi"]);
  });
});

describe("parseCatalog", () => {
  const payload = {
    languages: [{ code: "en-IN", name: "English" }, { code: "hi-IN", name: "Hindi" }],
    voices: [
      { name: "Devika", gender: "female", language: "en-IN", hinglish: false },
      { name: "Poorvi", gender: "female", language: "hi-IN", hinglish: true },
      { name: "Broken", gender: "robot", language: "hi-IN" },
      { gender: "male", language: "hi-IN" },
    ],
    defaults: { "en-IN": "Poorvi", "hi-IN": 7 },
  };

  it("accepts the /voices shape and skips malformed entries", () => {
    const catalog = parseCatalog(payload);
    assert.ok(catalog);
    assert.deepEqual(catalog.voices.map((v) => v.name), ["Devika", "Poorvi"]);
    assert.equal(catalog.voices[1].hinglish, true);
    assert.deepEqual(catalog.defaults, { "en-IN": "Poorvi" });
  });

  it("rejects payloads with nothing usable", () => {
    assert.equal(parseCatalog(null), null);
    assert.equal(parseCatalog({ languages: [], voices: [] }), null);
    assert.equal(parseCatalog({ languages: [{ code: "en-IN", name: "English" }] }), null);
    assert.equal(parseCatalog("nope"), null);
  });
});

describe("language and voice mapping", () => {
  it("puts Auto-detect first, then every server language in order", () => {
    const options = languageOptions(STATIC_CATALOG);
    assert.equal(options.length, 11);
    assert.deepEqual(options[0], { code: AUTO_LANGUAGE, name: "Auto-detect", auto: true });
    assert.deepEqual(options.slice(1).map((o) => o.code), STATIC_CATALOG.languages.map((l) => l.code));
    assert.ok(options.slice(1).every((o) => !o.auto));
  });

  it("groups every voice under its language, keeping female-then-male order", () => {
    const groups = groupVoices(STATIC_CATALOG);
    assert.equal(groups.length, 10);
    assert.equal(groups.reduce((n, g) => n + g.voices.length, 0), 42);
    const hindi = groups.find((g) => g.language.code === "hi-IN");
    assert.equal(hindi?.voices.length, 14);
    assert.deepEqual(hindi?.voices.slice(0, 2).map((v) => v.name), ["Poorvi", "Ambuja"]);
    assert.equal(hindi?.voices.at(-1)?.gender, "male");
    assert.ok(groups.every((g) => g.voices.every((v) => v.language === g.language.code)));
  });

  it("drops languages that have no voices and follows a server-provided language list", () => {
    const catalog = parseCatalog({
      languages: [{ code: "ta-IN", name: "Tamil" }, { code: "xx-XX", name: "Nowhere" }],
      voices: [{ name: "Asmita", gender: "female", language: "ta-IN" }],
      defaults: {},
    });
    assert.ok(catalog);
    assert.deepEqual(groupVoices(catalog).map((g) => g.language.code), ["ta-IN"]);
    assert.equal(languageOptions(catalog).length, 3);
  });

  it("summarises the selected voice, case-insensitively, or says default", () => {
    assert.equal(voiceSummary(STATIC_CATALOG, "poorvi"), "Poorvi · Female");
    assert.equal(voiceSummary(STATIC_CATALOG, "Deepak"), "Deepak · Male");
    assert.equal(voiceSummary(STATIC_CATALOG, null), "Default voice");
    assert.equal(voiceSummary(STATIC_CATALOG, "Custom"), "Custom", "a saved id the catalogue does not know is still shown");
  });

  it("builds the preview path with the voice preferred language", () => {
    const poorvi = findVoice(STATIC_CATALOG, "Poorvi");
    assert.ok(poorvi);
    assert.equal(previewPath(poorvi), "/voices/Poorvi/preview?language=hi-IN");
  });
});

describe("fetchCatalog", () => {
  it("returns the server catalogue when it answers", async () => {
    const catalog = await fetchCatalog(
      "http://api",
      "tok",
      response(200, {
        languages: [{ code: "en-IN", name: "English" }],
        voices: [{ name: "Devika", gender: "female", language: "en-IN", hinglish: false }],
        defaults: { "en-IN": "Devika" },
      }),
    );
    assert.equal(catalog.voices.length, 1);
  });

  it("sends the bearer token to /voices", async () => {
    let seen: { url: string; auth: string | null } | null = null;
    const spy = (async (url: string, init: RequestInit) => {
      seen = { url, auth: new Headers(init.headers).get("Authorization") };
      return new Response("{}", { status: 200 });
    }) as unknown as typeof fetch;
    await fetchCatalog("http://api", "tok", spy);
    assert.deepEqual(seen, { url: "http://api/voices", auth: "Bearer tok" });
  });

  it("falls back to the built-in copy on HTTP errors, bad JSON and network failure", async () => {
    assert.equal(await fetchCatalog("http://api", "t", response(500)), STATIC_CATALOG);
    assert.equal(await fetchCatalog("http://api", "t", response(200, "not json")), STATIC_CATALOG);
    assert.equal(await fetchCatalog("http://api", "t", response(200, { languages: [], voices: [] })), STATIC_CATALOG);
    const down = (async () => {
      throw new TypeError("fetch failed");
    }) as unknown as typeof fetch;
    assert.equal(await fetchCatalog("http://api", "t", down), STATIC_CATALOG);
  });
});

describe("fetchPreview", () => {
  const poorvi = STATIC_CATALOG.voices[0];

  it("returns the audio bytes and the advertised sample rate", async () => {
    const result = await fetchPreview("http://api", "t", poorvi, response(200, new ArrayBuffer(8), { "X-Sample-Rate": "24000" }));
    assert.equal(result.kind, "ok");
    assert.equal(result.kind === "ok" && result.audio.byteLength, 8);
    assert.equal(result.kind === "ok" && result.sampleRate, 24000);
  });

  it("treats 503 and 429 as unavailable (Timbre busy), not as a failure", async () => {
    assert.deepEqual(await fetchPreview("http://api", "t", poorvi, response(503)), { kind: "unavailable" });
    assert.deepEqual(await fetchPreview("http://api", "t", poorvi, response(429)), { kind: "unavailable" });
  });

  it("reports other failures as errors without throwing", async () => {
    assert.deepEqual(await fetchPreview("http://api", "t", poorvi, response(404)), { kind: "error" });
    const down = (async () => {
      throw new TypeError("fetch failed");
    }) as unknown as typeof fetch;
    assert.deepEqual(await fetchPreview("http://api", "t", poorvi, down), { kind: "error" });
  });
});

describe("wav helpers", () => {
  it("wraps raw PCM in a valid mono 16-bit header and detects WAV", () => {
    const pcm = new Int16Array([1, -2, 3, 4]).buffer;
    const wav = pcmToWav(pcm, 24000);
    assert.equal(wav.byteLength, 44 + 8);
    assert.equal(isWav(wav), true);
    assert.equal(isWav(pcm), false);
    const view = new DataView(wav);
    assert.equal(view.getUint32(4, true), 36 + 8);
    assert.equal(view.getUint32(24, true), 24000);
    assert.equal(view.getUint16(22, true), 1);
    assert.equal(view.getUint32(40, true), 8);
    assert.deepEqual([...new Int16Array(wav, 44)], [1, -2, 3, 4]);
  });

  it("does not mistake short buffers for WAV", () => {
    assert.equal(isWav(new ArrayBuffer(4)), false);
  });
});
