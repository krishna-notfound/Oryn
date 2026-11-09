# UI context

Source of truth: Stitch project **Saathi Voice AI Experience** (`4332495550980426447`), exported to
`design/stitch/` — each screen folder has `code.html` (Tailwind) + `screen.png`; the design system
is `design/stitch/warm_tactile_voice/DESIGN.md`. Port the HTML to React components; rename
"Saathi" → "Oryn" in all copy. When the brief and Stitch disagree, **Stitch wins**.

| Screen (Stitch folder)                     | Route |
| ------------------------------------------ | ----- |
| `saathi_welcome`                           | `/` (signed out) |
| `saathi_simple_onboarding`                 | `/onboarding` step: name |
| `saathi_onboarding_language_selection`     | `/onboarding` step: languages |
| `saathi_onboarding_communication_style`    | `/onboarding` step: style |
| `saathi_onboarding_memory_consent_finish`  | `/onboarding` step: memory + finish |
| `saathi_live_voice_companion`              | `/talk` (home + live call) |
| `saathi_minimal_settings`                  | `/settings` |
| *(no Stitch screen — same visual language)*| `/chat` text fallback, `/memories` |

## Principles

Light theme, lavender/lime palette from DESIGN.md, lots of whitespace, soft rounded shapes,
subtle shadows, one primary action per screen. The **orb** is Oryn: it reflects the voice state
(idle breathing, listening ripple, thinking shimmer, speaking pulse driven by output level).

## Mobile-first

Full-height voice screen (`100dvh`), safe-area insets, thumb-reachable mic/end-call (≥ 56 px),
keyboard-aware chat composer, no horizontal overflow. Desktop: centred column, max ~480 px for
voice, ~720 px for chat.
