"use client";

import { useState, type ReactNode } from "react";
import { useRouter } from "next/navigation";
import { ArrowLeft, ArrowRight, Brain, Check, Heart, Lock, Repeat } from "lucide-react";
import { createClient } from "@/lib/supabase/client";
import {
  DEFAULT_PROFILE,
  INTERESTS,
  LANGUAGES,
  STYLES,
  type ProfileInput,
} from "@/lib/profile";
import { AUTO_LABEL, AUTO_LANGUAGE } from "@/lib/voices";
import { Chip, Segmented, Toggle } from "./ui";

type StepId = "name" | "languages" | "interests" | "style" | "preferences" | "memory";
const STEPS: StepId[] = ["name", "languages", "interests", "style", "preferences", "memory"];
/** Steps the user may skip; the defaults in DEFAULT_PROFILE apply. */
const SKIPPABLE: ReadonlySet<StepId> = new Set(["name", "interests", "style", "preferences"]);

const QUESTIONS: Record<StepId, string> = {
  name: "Hi there! 👋 What should I call you?",
  languages: "Which language feels most natural for you? 🎙️",
  interests: "What do you enjoy talking about? ✨",
  style: "How would you like me to talk with you? ✨",
  preferences: "A few small things about how I reply 💬",
  memory: "All set! Can I remember our talks to be a better friend? 💜",
};

/** Small friendly avatar for the question bubble (from the Stitch communication-style screen). */
function Mascot() {
  return (
    <div className="grid size-16 shrink-0 place-items-center rounded-full bg-gradient-to-tr from-primary-container via-primary-fixed to-secondary-container p-1 shadow-md ring-4 ring-primary-fixed/40">
      <svg className="size-12" viewBox="0 0 48 48" fill="none" aria-hidden="true">
        <circle cx="24" cy="24" r="20" fill="#E8DDFF" />
        <ellipse cx="14" cy="27" rx="3" ry="1.8" fill="#FFDAD6" />
        <ellipse cx="34" cy="27" rx="3" ry="1.8" fill="#FFDAD6" />
        <path d="M15 21C15 19.5 17 19.5 17 21" stroke="#200B4F" strokeWidth="2.5" strokeLinecap="round" />
        <path d="M31 21C31 19.5 33 19.5 33 21" stroke="#200B4F" strokeWidth="2.5" strokeLinecap="round" />
        <path d="M20 27C21.5 29.5 26.5 29.5 28 27" stroke="#200B4F" strokeWidth="2.5" strokeLinecap="round" />
        <circle cx="24" cy="8" r="3" fill="#C4EF67" />
        <path d="M24 11V14" stroke="#655497" strokeWidth="2" strokeLinecap="round" />
      </svg>
    </div>
  );
}

function ChoiceCard({
  selected,
  onClick,
  icon,
  title,
  hint,
}: {
  selected: boolean;
  onClick: () => void;
  icon?: ReactNode;
  title: string;
  hint?: string;
}) {
  return (
    <button
      type="button"
      role="radio"
      aria-checked={selected}
      onClick={onClick}
      className={`flex min-h-16 w-full items-center justify-between gap-space-md rounded-2xl p-space-md text-left shadow-sm transition-all active:scale-[0.98] ${
        selected ? "bg-primary-fixed/30 shadow-md ring-2 ring-primary-container" : "bg-surface-container-lowest"
      }`}
    >
      <span className="flex min-w-0 items-center gap-space-md">
        {icon && (
          <span
            className={`grid size-12 shrink-0 place-items-center rounded-2xl text-2xl ${
              selected ? "bg-primary-container text-on-primary-container" : "bg-surface-container-high"
            }`}
          >
            {icon}
          </span>
        )}
        <span className="min-w-0">
          <span className="type-label-lg block text-on-surface">{title}</span>
          {hint && <span className="type-body-sm block text-on-surface-variant">{hint}</span>}
        </span>
      </span>
      <span
        className={`grid size-7 shrink-0 place-items-center rounded-full ${
          selected ? "bg-primary text-on-primary" : "bg-surface-container-highest text-transparent"
        }`}
      >
        <Check size={18} aria-hidden="true" />
      </span>
    </button>
  );
}

export function OnboardingFlow({
  userId,
  initialName,
}: {
  userId: string;
  initialName: string;
}) {
  const router = useRouter();
  const [step, setStep] = useState(0);
  const [draft, setDraft] = useState<ProfileInput>({ ...DEFAULT_PROFILE, preferred_name: initialName || null });
  const [name, setName] = useState(initialName);
  const [memory, setMemory] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const id = STEPS[step];
  const isLast = step === STEPS.length - 1;
  const update = (partial: Partial<ProfileInput>) => setDraft((d) => ({ ...d, ...partial }));

  async function finish(consent: boolean) {
    setSaving(true);
    setError(null);
    const supabase = createClient();
    const now = new Date().toISOString();
    const timezone = Intl.DateTimeFormat().resolvedOptions().timeZone || draft.timezone;

    const { error: profileError } = await supabase.from("profiles").upsert(
      {
        id: userId,
        ...draft,
        preferred_name: name.trim() || null,
        timezone,
        memory_enabled: consent,
        onboarded_at: now,
        updated_at: now,
      },
      { onConflict: "id" },
    );
    if (profileError) return fail(profileError.message);

    const { error: consentError } = await supabase.from("consents").insert({ user_id: userId, kind: "memory", granted: consent });
    if (consentError) return fail(consentError.message);

    router.replace("/talk");
    router.refresh();
  }

  function fail(message: string) {
    setError(message);
    setSaving(false);
  }

  function next() {
    if (isLast) void finish(memory);
    else setStep((s) => s + 1);
  }

  function skip() {
    if (id === "name") setName("");
    next();
  }

  const canContinue = id !== "languages" || Boolean(draft.primary_language);

  return (
    <div className="flex min-h-dvh flex-col">
      <header className="pt-safe sticky top-0 z-30 bg-surface/90 backdrop-blur-xl">
        <div className="page-col flex h-16 items-center gap-space-md px-margin">
          {step > 0 ? (
            <button
              type="button"
              aria-label="Back"
              onClick={() => setStep((s) => s - 1)}
              className="-ml-2 grid size-11 place-items-center rounded-full text-on-surface-variant hover:bg-surface-container-high"
            >
              <ArrowLeft size={24} />
            </button>
          ) : (
            <span className="-ml-2 size-11" aria-hidden="true" />
          )}
          <div
            role="progressbar"
            aria-valuemin={1}
            aria-valuemax={STEPS.length}
            aria-valuenow={step + 1}
            aria-label={`Step ${step + 1} of ${STEPS.length}`}
            className="h-3.5 flex-1 overflow-hidden rounded-full bg-surface-container-highest p-0.5"
          >
            <div
              className="h-full rounded-full bg-secondary transition-all duration-300"
              style={{ width: `${((step + 1) / STEPS.length) * 100}%` }}
            />
          </div>
          <span className="type-label-md w-11 text-right text-on-surface-variant">
            {step + 1}/{STEPS.length}
          </span>
        </div>
      </header>

      <main className="page-col pb-safe flex flex-1 flex-col px-margin">
        <div className="flex flex-1 flex-col gap-space-lg pt-space-md">
          <div className="flex items-start gap-3.5">
            <Mascot />
            <div className="flex-1 rounded-2xl rounded-tl-sm border-2 border-surface-container-high bg-surface-container-lowest p-space-md shadow-sm">
              <h1 className="type-headline-sm leading-snug text-on-surface">{QUESTIONS[id]}</h1>
            </div>
          </div>

          {id === "name" && (
            <div>
              <label className="sr-only" htmlFor="name">Your name</label>
              <input
                id="name"
                autoFocus
                value={name}
                maxLength={60}
                onChange={(event) => setName(event.target.value)}
                onKeyDown={(event) => event.key === "Enter" && name.trim() && next()}
                placeholder="Type your name…"
                autoComplete="given-name"
                className="type-headline-sm h-16 w-full rounded-2xl border-2 border-surface-variant bg-surface-container-lowest px-5 text-on-surface shadow-sm placeholder:text-outline/60 focus:border-secondary focus:outline-none"
              />
            </div>
          )}

          {id === "languages" && (
            <div className="flex flex-col gap-space-md">
              <div role="radiogroup" aria-label="Primary language" className="grid grid-cols-2 gap-space-sm">
                <button
                  type="button"
                  role="radio"
                  aria-checked={draft.primary_language === AUTO_LANGUAGE}
                  onClick={() => update({ primary_language: AUTO_LANGUAGE })}
                  className={`col-span-2 flex min-h-16 flex-col items-start justify-center rounded-2xl px-4 text-left shadow-sm transition-all active:scale-[0.98] ${
                    draft.primary_language === AUTO_LANGUAGE ? "bg-primary-fixed/30 ring-2 ring-primary-container" : "bg-surface-container-lowest"
                  }`}
                >
                  <span className="type-label-lg text-on-surface">{AUTO_LABEL}</span>
                  <span className="type-body-sm text-on-surface-variant">Follow whichever language I speak</span>
                </button>
                {LANGUAGES.map((language) => {
                  const selected = draft.primary_language === language.code;
                  return (
                    <button
                      key={language.code}
                      type="button"
                      role="radio"
                      aria-checked={selected}
                      onClick={() =>
                        update({
                          primary_language: language.code,
                          extra_languages: draft.extra_languages.filter((code) => code !== language.code),
                        })
                      }
                      className={`flex min-h-16 flex-col items-start justify-center rounded-2xl px-4 text-left shadow-sm transition-all active:scale-[0.98] ${
                        selected ? "bg-primary-fixed/30 ring-2 ring-primary-container" : "bg-surface-container-lowest"
                      }`}
                    >
                      <span className="type-label-lg text-on-surface">{language.native}</span>
                      <span className="type-body-sm text-on-surface-variant">{language.name}</span>
                    </button>
                  );
                })}
              </div>

              {draft.primary_language !== AUTO_LANGUAGE && (
              <div>
                <p className="type-label-md mb-space-sm text-on-surface-variant">Also comfortable in</p>
                <div className="flex flex-wrap gap-space-sm">
                  {LANGUAGES.filter((language) => language.code !== draft.primary_language).map((language) => {
                    const selected = draft.extra_languages.includes(language.code);
                    return (
                      <Chip
                        key={language.code}
                        selected={selected}
                        onClick={() =>
                          update({
                            extra_languages: selected
                              ? draft.extra_languages.filter((code) => code !== language.code)
                              : [...draft.extra_languages, language.code],
                          })
                        }
                      >
                        {language.name}
                      </Chip>
                    );
                  })}
                </div>
              </div>
              )}

              <div className="flex items-center justify-between gap-space-md rounded-2xl bg-surface-container-low p-space-md">
                <div>
                  <p className="type-label-md text-on-surface">Mix languages</p>
                  <p className="type-body-sm text-on-surface-variant">Let me switch mid-sentence, like Hinglish.</p>
                </div>
                <Toggle checked={draft.code_switching} onChange={(value) => update({ code_switching: value })} label="Mix languages" />
              </div>
            </div>
          )}

          {id === "interests" && (
            <div className="flex flex-wrap gap-space-sm">
              {INTERESTS.map((interest) => {
                const selected = draft.interests.includes(interest);
                return (
                  <Chip
                    key={interest}
                    selected={selected}
                    onClick={() =>
                      update({
                        interests: selected ? draft.interests.filter((entry) => entry !== interest) : [...draft.interests, interest],
                      })
                    }
                  >
                    {interest}
                  </Chip>
                );
              })}
            </div>
          )}

          {id === "style" && (
            <div role="radiogroup" aria-label="Communication style" className="flex flex-col gap-space-sm">
              {STYLES.map((style) => (
                <ChoiceCard
                  key={style.id}
                  selected={draft.style === style.id}
                  onClick={() => update({ style: style.id })}
                  icon={style.emoji}
                  title={style.title}
                  hint={style.hint}
                />
              ))}
            </div>
          )}

          {id === "preferences" && (
            <div className="flex flex-col gap-space-lg">
              <div className="flex flex-col gap-space-sm">
                <p className="type-label-md text-on-surface-variant">How long should my replies be?</p>
                <Segmented
                  label="Reply length"
                  value={draft.response_length}
                  onChange={(value) => update({ response_length: value })}
                  options={[
                    { value: "short", label: "Short" },
                    { value: "detailed", label: "Detailed" },
                  ]}
                />
              </div>
              <div className="flex flex-col gap-space-sm">
                <p className="type-label-md text-on-surface-variant">When you share something…</p>
                <Segmented
                  label="Listen or suggest"
                  value={draft.listen_first ? "listen" : "solve"}
                  onChange={(value) => update({ listen_first: value === "listen" })}
                  options={[
                    { value: "listen", label: "Listen first" },
                    { value: "solve", label: "Suggest solutions" },
                  ]}
                />
              </div>
              <div className="flex flex-col gap-space-sm">
                <p className="type-label-md text-on-surface-variant">How formal should I be?</p>
                <Segmented
                  label="Formality"
                  value={draft.formality}
                  onChange={(value) => update({ formality: value })}
                  options={[
                    { value: "informal", label: "Casual" },
                    { value: "formal", label: "Formal" },
                  ]}
                />
              </div>
            </div>
          )}

          {id === "memory" && (
            <div className="flex flex-col gap-space-md rounded-2xl bg-surface-container-lowest p-space-lg shadow-sm">
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-space-sm">
                  <span className="grid size-10 place-items-center rounded-full bg-secondary-container/40 text-secondary">
                    <Brain size={22} aria-hidden="true" />
                  </span>
                  <div>
                    <h2 className="type-headline-sm text-on-surface">Oryn Memory</h2>
                    <span className="type-label-sm text-on-surface-variant">Personalized conversations</span>
                  </div>
                </div>
                <Toggle checked={memory} onChange={setMemory} label="Allow Oryn to remember our conversations" />
              </div>
              {[
                { icon: Heart, text: "Remembers what matters to you (goals, friends, moments)" },
                { icon: Repeat, text: "Keeps conversations continuous without repeating yourself" },
                { icon: Lock, text: "100% private, delete or wipe anytime in settings" },
              ].map(({ icon: Icon, text }) => (
                <div key={text} className="flex items-start gap-space-sm">
                  <span className="mt-0.5 grid size-6 shrink-0 place-items-center rounded-full bg-surface-container text-primary">
                    <Icon size={14} aria-hidden="true" />
                  </span>
                  <p className="type-body-md text-on-surface">{text}</p>
                </div>
              ))}
            </div>
          )}
        </div>

        <div className="flex flex-col items-center gap-space-sm pb-space-md pt-space-lg">
          {error && (
            <p role="alert" className="type-body-sm text-center text-error">
              {error}
            </p>
          )}
          <button
            type="button"
            onClick={next}
            disabled={!canContinue || saving || (id === "name" && !name.trim())}
            className="type-label-lg flex h-14 w-full items-center justify-center gap-2 rounded-2xl bg-primary uppercase tracking-wider text-on-primary shadow-[0_4px_0_0_#493879] transition-all active:translate-y-1 active:shadow-none disabled:opacity-50"
          >
            {saving ? "Saving…" : isLast ? "Meet Oryn 🚀" : "Continue"}
            {!isLast && !saving && <ArrowRight size={20} aria-hidden="true" />}
          </button>
          {SKIPPABLE.has(id) && (
            <button type="button" onClick={skip} disabled={saving} className="type-label-md min-h-11 px-4 text-on-surface-variant underline-offset-4 hover:underline">
              {id === "name" ? "Or stay anonymous" : "Skip for now"}
            </button>
          )}
          {isLast && (
            <button
              type="button"
              disabled={saving}
              onClick={() => {
                setMemory(false);
                void finish(false);
              }}
              className="type-label-lg min-h-11 px-4 font-medium text-on-surface-variant"
            >
              Start fresh without saving memory
            </button>
          )}
        </div>
      </main>
    </div>
  );
}
