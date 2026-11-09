"use client";

import { useState, type ReactNode } from "react";
import Link from "next/link";
import { Brain, ChevronRight, Languages, Pencil, ShieldCheck, Smile, Mic2 } from "lucide-react";
import { createClient } from "@/lib/supabase/client";
import { INTERESTS, LANGUAGES, STYLES, languageLabel, type Profile } from "@/lib/profile";
import { AccountSection } from "./AccountSection";
import { Chip, Segmented, Toggle, TopBar } from "./ui";

const CARD = "rounded-xl bg-surface-container-lowest p-space-md shadow-[0_4px_20px_-2px_rgba(31,32,36,0.04)]";

function Row({ icon, label, value, tint }: { icon: ReactNode; label: string; value: string; tint: string }) {
  return (
    <span className="flex min-w-0 items-center gap-space-md">
      <span className={`grid size-10 shrink-0 place-items-center rounded-full ${tint}`}>{icon}</span>
      <span className="flex min-w-0 flex-col text-left">
        <span className="type-label-sm text-on-surface-variant">{label}</span>
        <span className="type-label-lg truncate text-on-surface">{value}</span>
      </span>
    </span>
  );
}

function Expandable({ row, children }: { row: ReactNode; children: ReactNode }) {
  const [open, setOpen] = useState(false);
  return (
    <div className={CARD}>
      <button
        type="button"
        aria-expanded={open}
        onClick={() => setOpen((value) => !value)}
        className="flex min-h-11 w-full items-center justify-between gap-space-sm"
      >
        {row}
        <ChevronRight size={20} className={`shrink-0 text-outline transition-transform ${open ? "rotate-90" : ""}`} aria-hidden="true" />
      </button>
      {open && <div className="mt-space-md flex flex-col gap-space-md border-t border-outline-variant/20 pt-space-md">{children}</div>}
    </div>
  );
}

export function SettingsScreen({ initial, email, isGuest }: { initial: Profile; email: string; isGuest: boolean }) {
  const [profile, setProfile] = useState(initial);
  const [editingName, setEditingName] = useState(false);
  const [nameDraft, setNameDraft] = useState(initial.preferred_name ?? "");
  const [voiceDraft, setVoiceDraft] = useState(initial.voice ?? "");
  const [notice, setNotice] = useState<string | null>(null);

  /** Optimistic save of profile columns; reverts and reports on failure. Consent changes are audited. */
  async function save(partial: Partial<Profile>) {
    const previous = profile;
    setProfile({ ...profile, ...partial });
    setNotice(null);

    const supabase = createClient();
    const { error } = await supabase
      .from("profiles")
      .update({ ...partial, updated_at: new Date().toISOString() })
      .eq("id", profile.id);
    if (error) {
      setProfile(previous);
      setNotice(`Couldn't save: ${error.message}`);
      return;
    }
    if (partial.memory_enabled !== undefined) {
      await supabase.from("consents").insert({ user_id: profile.id, kind: "memory", granted: partial.memory_enabled });
    }
    if (partial.save_history !== undefined) {
      await supabase.from("consents").insert({ user_id: profile.id, kind: "history", granted: partial.save_history });
    }
    setNotice("Saved");
  }

  const style = STYLES.find((entry) => entry.id === profile.style);

  return (
    <div className="flex min-h-dvh flex-col">
      <TopBar backHref="/talk" title="Settings" right={notice && <span role="status" className="type-label-sm text-on-surface-variant">{notice}</span>} />

      <main className="page-col pb-safe flex flex-1 flex-col gap-space-sm px-margin py-space-md">
        {/* Profile */}
        <div className={`${CARD} flex items-center justify-between gap-space-md`}>
          <div className="flex min-w-0 flex-1 items-center gap-space-md">
            <div className="grid size-14 shrink-0 place-items-center rounded-full bg-gradient-to-tr from-primary-fixed via-primary-container to-secondary-container text-on-primary-container">
              <span className="type-headline-sm">{(profile.preferred_name ?? profile.username ?? email ?? "?").charAt(0).toUpperCase()}</span>
            </div>
            <div className="flex min-w-0 flex-1 flex-col">
              <span className="type-label-sm text-on-surface-variant">Profile name</span>
              {editingName ? (
                <input
                  autoFocus
                  value={nameDraft}
                  maxLength={60}
                  onChange={(event) => setNameDraft(event.target.value)}
                  onKeyDown={(event) => {
                    if (event.key === "Enter") {
                      void save({ preferred_name: nameDraft.trim() || null });
                      setEditingName(false);
                    }
                  }}
                  aria-label="Your name"
                  className="type-headline-sm w-full rounded-lg border border-outline-variant/40 bg-surface px-2 py-1 focus:border-primary-container focus:outline-none"
                />
              ) : (
                <span className="type-headline-sm truncate text-on-surface">{profile.preferred_name ?? "Friend"}</span>
              )}
              <span className="type-body-sm truncate text-outline">{isGuest ? `@${profile.username ?? "guest"}` : email}</span>
            </div>
          </div>
          <button
            type="button"
            aria-label={editingName ? "Save name" : "Edit name"}
            onClick={() => {
              if (editingName) void save({ preferred_name: nameDraft.trim() || null });
              setEditingName(!editingName);
            }}
            className="grid size-11 shrink-0 place-items-center rounded-full bg-surface-container-low text-on-surface-variant active:scale-95"
          >
            <Pencil size={18} />
          </button>
        </div>

        {/* Languages */}
        <Expandable
          row={
            <Row
              icon={<Languages size={20} />}
              tint="bg-primary-fixed/50 text-primary"
              label="Language"
              value={[profile.primary_language, ...profile.extra_languages].map((code) => languageLabel(code).split(" ")[0]).join(", ")}
            />
          }
        >
          <div className="flex flex-col gap-space-sm">
            <p className="type-label-md text-on-surface-variant">Primary language</p>
            <div role="radiogroup" aria-label="Primary language" className="flex flex-wrap gap-space-sm">
              {LANGUAGES.map((language) => (
                <Chip
                  key={language.code}
                  selected={profile.primary_language === language.code}
                  onClick={() =>
                    save({
                      primary_language: language.code,
                      extra_languages: profile.extra_languages.filter((code) => code !== language.code),
                    })
                  }
                >
                  {language.name}
                </Chip>
              ))}
            </div>
          </div>
          <div className="flex flex-col gap-space-sm">
            <p className="type-label-md text-on-surface-variant">Also comfortable in</p>
            <div className="flex flex-wrap gap-space-sm">
              {LANGUAGES.filter((language) => language.code !== profile.primary_language).map((language) => {
                const selected = profile.extra_languages.includes(language.code);
                return (
                  <Chip
                    key={language.code}
                    selected={selected}
                    onClick={() =>
                      save({
                        extra_languages: selected
                          ? profile.extra_languages.filter((code) => code !== language.code)
                          : [...profile.extra_languages, language.code],
                      })
                    }
                  >
                    {language.name}
                  </Chip>
                );
              })}
            </div>
          </div>
          <div className="flex items-center justify-between gap-space-md">
            <p className="type-body-md text-on-surface">Mix languages mid-sentence</p>
            <Toggle checked={profile.code_switching} onChange={(value) => save({ code_switching: value })} label="Mix languages" />
          </div>
        </Expandable>

        {/* Voice */}
        <div className={CARD}>
          <Row icon={<Mic2 size={20} />} tint="bg-secondary-container/50 text-secondary" label="Voice" value={profile.voice || "Default voice"} />
          <label className="sr-only" htmlFor="voice">Voice id</label>
          <input
            id="voice"
            value={voiceDraft}
            maxLength={60}
            onChange={(event) => setVoiceDraft(event.target.value)}
            onBlur={() => {
              const voice = voiceDraft.trim() || null;
              if (voice !== profile.voice) void save({ voice });
            }}
            placeholder="Voice id (optional)"
            className="type-body-md mt-space-sm h-12 w-full rounded-xl border border-outline-variant/40 bg-surface px-4 placeholder:text-outline/70 focus:border-primary-container focus:outline-none focus:ring-4 focus:ring-primary-container/25"
          />
        </div>

        {/* Communication style */}
        <Expandable row={<Row icon={<Smile size={20} />} tint="bg-surface-container text-on-surface-variant" label="Communication style" value={style?.title ?? profile.style} />}>
          <div role="radiogroup" aria-label="Communication style" className="flex flex-wrap gap-space-sm">
            {STYLES.map((entry) => (
              <Chip key={entry.id} selected={profile.style === entry.id} onClick={() => save({ style: entry.id })}>
                {entry.emoji} {entry.title}
              </Chip>
            ))}
          </div>
          <Segmented
            label="Reply length"
            value={profile.response_length}
            onChange={(value) => save({ response_length: value })}
            options={[{ value: "short", label: "Short" }, { value: "detailed", label: "Detailed" }]}
          />
          <Segmented
            label="Listen or suggest"
            value={profile.listen_first ? "listen" : "solve"}
            onChange={(value) => save({ listen_first: value === "listen" })}
            options={[{ value: "listen", label: "Listen first" }, { value: "solve", label: "Suggest solutions" }]}
          />
          <Segmented
            label="Formality"
            value={profile.formality}
            onChange={(value) => save({ formality: value })}
            options={[{ value: "informal", label: "Casual" }, { value: "formal", label: "Formal" }]}
          />
          <div className="flex flex-col gap-space-sm">
            <p className="type-label-md text-on-surface-variant">Interests</p>
            <div className="flex flex-wrap gap-space-sm">
              {INTERESTS.map((interest) => {
                const selected = profile.interests.includes(interest);
                return (
                  <Chip
                    key={interest}
                    selected={selected}
                    onClick={() => save({ interests: selected ? profile.interests.filter((entry) => entry !== interest) : [...profile.interests, interest] })}
                  >
                    {interest}
                  </Chip>
                );
              })}
            </div>
          </div>
        </Expandable>

        {/* Memory */}
        <div className={`${CARD} flex flex-col gap-space-sm`}>
          <div className="flex items-start justify-between gap-space-sm">
            <div className="flex items-start gap-space-md">
              <span className="mt-0.5 grid size-10 shrink-0 place-items-center rounded-full bg-primary-fixed/40 text-primary">
                <Brain size={20} aria-hidden="true" />
              </span>
              <div>
                <p className="type-label-lg text-on-surface">Allow Oryn to remember context</p>
                <p className="type-body-sm mt-0.5 text-on-surface-variant">
                  Oryn remembers key topics to feel personal across conversations. Turning this off stops it right away.
                </p>
              </div>
            </div>
            <Toggle checked={profile.memory_enabled} onChange={(value) => save({ memory_enabled: value })} label="Allow Oryn to remember context" />
          </div>
          <Link href="/memories" className="type-label-md ml-[3.5rem] flex min-h-11 items-center gap-1 text-primary">
            Manage saved memories <ChevronRight size={16} aria-hidden="true" />
          </Link>
        </div>

        {/* Privacy */}
        <div className={`${CARD} flex items-start justify-between gap-space-sm`}>
          <div className="flex items-start gap-space-md">
            <span className="mt-0.5 grid size-10 shrink-0 place-items-center rounded-full bg-surface-container text-on-surface-variant">
              <ShieldCheck size={20} aria-hidden="true" />
            </span>
            <div>
              <p className="type-label-lg text-on-surface">Save conversation history</p>
              <p className="type-body-sm mt-0.5 text-on-surface-variant">When off, your chats are not stored after the session ends.</p>
            </div>
          </div>
          <Toggle checked={profile.save_history} onChange={(value) => save({ save_history: value })} label="Save conversation history" />
        </div>

        <AccountSection isGuest={isGuest} username={profile.username} email={email} />
      </main>
    </div>
  );
}
