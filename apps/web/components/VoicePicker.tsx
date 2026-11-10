"use client";

import { Loader2, Play, Square } from "lucide-react";
import { useVoicePreview } from "@/hooks/useVoicePreview";
import { genderLabel, groupVoices, type VoiceCatalog, type VoiceInfo } from "@/lib/voices";

interface Props {
  catalog: VoiceCatalog;
  /** Saved voice name, or null for the automatic per-language default. */
  selected: string | null;
  onSelect: (voice: string | null) => void;
}

const ROW = "flex min-h-14 items-center gap-space-sm rounded-xl px-space-sm";
const CHOSEN = "bg-primary-fixed/30 ring-2 ring-primary-container";
const IDLE = "bg-surface-container-low";

/** Every Gnani voice grouped by language, with gender, a default marker and a play button for a sample. */
export function VoicePicker({ catalog, selected, onSelect }: Props) {
  const preview = useVoicePreview();
  const defaults = new Set(Object.values(catalog.defaults));

  const previewButton = (voice: VoiceInfo) => {
    const active = preview.active === voice.name;
    return (
      <button
        type="button"
        aria-label={active ? `Stop ${voice.name} sample` : `Play ${voice.name} sample`}
        onClick={() => void preview.play(voice)}
        className="grid size-11 shrink-0 place-items-center rounded-full bg-secondary-container/50 text-on-secondary-container active:scale-95"
      >
        {active && preview.loading ? (
          <Loader2 size={18} className="animate-spin" aria-hidden="true" />
        ) : active ? (
          <Square size={16} fill="currentColor" aria-hidden="true" />
        ) : (
          <Play size={18} fill="currentColor" aria-hidden="true" />
        )}
      </button>
    );
  };

  return (
    <div className="flex flex-col gap-space-md">
      <div role="radiogroup" aria-label="Voice" className="flex flex-col gap-space-md">
        <button
          type="button"
          role="radio"
          aria-checked={selected === null}
          onClick={() => onSelect(null)}
          className={`${ROW} text-left ${selected === null ? CHOSEN : IDLE}`}
        >
          <span className="flex flex-col">
            <span className="type-label-lg text-on-surface">Automatic</span>
            <span className="type-body-sm text-on-surface-variant">The best voice for the language being spoken</span>
          </span>
        </button>

        {groupVoices(catalog).map((group) => (
          <section key={group.language.code} aria-label={group.language.name} className="flex flex-col gap-space-xs">
            <h3 className="type-label-md px-space-xs text-on-surface-variant">{group.language.name}</h3>
            {group.voices.map((voice) => {
              const chosen = selected?.toLowerCase() === voice.name.toLowerCase();
              return (
                <div key={voice.name} className={`${ROW} ${chosen ? CHOSEN : IDLE}`}>
                  <button
                    type="button"
                    role="radio"
                    aria-checked={chosen}
                    onClick={() => onSelect(voice.name)}
                    className="flex min-h-11 min-w-0 flex-1 flex-col justify-center text-left"
                  >
                    <span className="type-label-lg truncate text-on-surface">{voice.name}</span>
                    <span className="type-body-sm truncate text-on-surface-variant">
                      {genderLabel(voice.gender)}
                      {voice.hinglish && " · Hinglish"}
                      {defaults.has(voice.name) && " · Default"}
                    </span>
                  </button>
                  {previewButton(voice)}
                </div>
              );
            })}
          </section>
        ))}
      </div>
      {preview.message && (
        <p role="status" className="type-body-sm text-on-surface-variant">
          {preview.message}
        </p>
      )}
    </div>
  );
}
