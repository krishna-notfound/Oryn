---
name: Warm Tactile Voice
colors:
  surface: '#faf8fe'
  surface-dim: '#dad9df'
  surface-bright: '#faf8fe'
  surface-container-lowest: '#ffffff'
  surface-container-low: '#f4f3f8'
  surface-container: '#eeedf3'
  surface-container-high: '#e9e7ed'
  surface-container-highest: '#e3e2e7'
  on-surface: '#1a1b1f'
  on-surface-variant: '#494550'
  inverse-surface: '#2f3034'
  inverse-on-surface: '#f1f0f5'
  outline: '#7a7581'
  outline-variant: '#cac4d1'
  surface-tint: '#655497'
  primary: '#655497'
  on-primary: '#ffffff'
  primary-container: '#b8a5ef'
  on-primary-container: '#493879'
  inverse-primary: '#cebdff'
  secondary: '#4b6700'
  on-secondary: '#ffffff'
  secondary-container: '#c4ef67'
  on-secondary-container: '#4f6c00'
  tertiary: '#605e58'
  on-tertiary: '#ffffff'
  tertiary-container: '#b3b0a9'
  on-tertiary-container: '#44433e'
  error: '#ba1a1a'
  on-error: '#ffffff'
  error-container: '#ffdad6'
  on-error-container: '#93000a'
  primary-fixed: '#e8ddff'
  primary-fixed-dim: '#cebdff'
  on-primary-fixed: '#200b4f'
  on-primary-fixed-variant: '#4c3c7d'
  secondary-fixed: '#c7f169'
  secondary-fixed-dim: '#abd550'
  on-secondary-fixed: '#141f00'
  on-secondary-fixed-variant: '#384e00'
  tertiary-fixed: '#e6e2da'
  tertiary-fixed-dim: '#cac6bf'
  on-tertiary-fixed: '#1c1c17'
  on-tertiary-fixed-variant: '#484741'
  background: '#faf8fe'
  on-background: '#1a1b1f'
  surface-variant: '#e3e2e7'
typography:
  headline-xl:
    fontFamily: Plus Jakarta Sans
    fontSize: 40px
    fontWeight: '700'
    lineHeight: 48px
    letterSpacing: -0.02em
  headline-xl-mobile:
    fontFamily: Plus Jakarta Sans
    fontSize: 30px
    fontWeight: '700'
    lineHeight: 38px
    letterSpacing: -0.015em
  headline-lg:
    fontFamily: Plus Jakarta Sans
    fontSize: 32px
    fontWeight: '600'
    lineHeight: 40px
    letterSpacing: -0.01em
  headline-lg-mobile:
    fontFamily: Plus Jakarta Sans
    fontSize: 24px
    fontWeight: '600'
    lineHeight: 32px
    letterSpacing: -0.01em
  headline-sm:
    fontFamily: Plus Jakarta Sans
    fontSize: 20px
    fontWeight: '600'
    lineHeight: 28px
  body-lg:
    fontFamily: Inter
    fontSize: 18px
    fontWeight: '400'
    lineHeight: 28px
  body-md:
    fontFamily: Inter
    fontSize: 15px
    fontWeight: '400'
    lineHeight: 24px
  body-sm:
    fontFamily: Inter
    fontSize: 13px
    fontWeight: '400'
    lineHeight: 20px
  label-lg:
    fontFamily: Plus Jakarta Sans
    fontSize: 15px
    fontWeight: '600'
    lineHeight: 20px
    letterSpacing: 0.01em
  label-md:
    fontFamily: Plus Jakarta Sans
    fontSize: 13px
    fontWeight: '600'
    lineHeight: 18px
    letterSpacing: 0.01em
  label-sm:
    fontFamily: Plus Jakarta Sans
    fontSize: 11px
    fontWeight: '600'
    lineHeight: 16px
    letterSpacing: 0.02em
rounded:
  sm: 0.25rem
  DEFAULT: 0.5rem
  md: 0.75rem
  lg: 1rem
  xl: 1.5rem
  full: 9999px
spacing:
  gutter: 1rem
  margin: 1.25rem
  space-xs: 0.25rem
  space-sm: 0.5rem
  space-md: 1rem
  space-lg: 1.5rem
  space-xl: 2.25rem
---

## Brand & Style

This design system expresses a calm, empathetic, and human-centric voice companion. It reconciles high-technology voice interaction with the warmth of a daily physical journal or thoughtful conversation partner. The visual demeanor avoids the cold, synthetic tropes of conventional virtual assistants—replacing neon waveforms and sterile monochrome with soft botanical and ambient tones.

The visual style blends **Modern Warm Minimalism** with **Soft Tactility**:
- Gentle, expansive canvases bathed in rich clotted-cream and warm porcelain tones.
- Weightless, responsive states driven by soft ambient glows and muted lavender field accents rather than harsh drop shadows.
- Tactile, rounded surfaces that invite touch and convey safety, emotional warmth, and approachable intelligence across bilingual and multilingual contexts.

## Colors

The palette is tuned around warm daylight temperatures, gentle flora, and deep legible charcoal ink:

- **Canvas & Surfaces:** `#FAF8F5` serves as the primary canvas tint. Elevated surface cards rest on `#FFFFFF` with structural boundary strokes in warm stone `#F0ECE4`.
- **Primary Voice & Action (`#B8A5EF`):** An airy, floral lavender. Used to represent listening states, AI speech ripples, focal toggles, and primary active states.
- **Secondary / Accent (`#B6E05A`):** A vibrant yet natural sunlit mint/sprout green. Used selectively for affirmations, successful voice understanding, positive feedback, and active audio energy markers.
- **Neutral & Typography (`#1F2024` and `#343740`):** Replaces harsh pure black with deep warm charcoal, delivering high-contrast legibility while preserving a gentle visual cadence. Secondary descriptive strings and subtle glyphs rely on `#6C6F78`.
- **Ambient Glows:** Soft tinted radial blurs (`rgba(184, 165, 239, 0.2)` and `rgba(182, 224, 90, 0.18)`) accompany active voice interactions without creating visual fatigue.

## Typography

The typographic hierarchy pairs the soft, friendly geometry of **Plus Jakarta Sans** for display, headings, and interactive pills with the neutral, hyper-legible matrix of **Inter** for real-time speech transcription, conversation streams, and multilingual script balancing.

- **Multilingual Script Harmony:** Line heights for body copy are deliberately relaxed (e.g., 24px line height on 15px text) to comfortably house diacritics, conjuncts, and ascenders/descenders across Indic scripts (Devanagari, Tamil, Telugu, Bengali) alongside Latin glyphs.
- **Micro-Copy & Transcript Hierarchy:** In live voice modes, current utterance streaming employs `body-lg` in `#1F2024`, while past conversational turn history transitions to `body-md` in `#343740`.

## Layout & Spacing

The layout embraces a mobile-first fluid model optimized for one-handed thumb interaction and voice triggers:

- **Vertical Rhythm:** A standard 8pt coordinate system governs all flow intervals, with 4pt half-steps (`space-xs`) reserved for internal icon-to-label offsets.
- **Safe Bounds:** Mobile portrait viewports utilize an edge margin of `1.25rem` (20px), expanding to `2rem` (32px) on tablets and desktop conversational sheets.
- **Voice Focus Area:** The bottom third of the phone viewport is dedicated to voice capture, speech status indicators, and thumb-friendly contextual suggestion pills, maintaining ample breathing room (`space-xl`) between conversational output and interactive inputs.

## Elevation & Depth

This design system avoids high-contrast drops and harsh dark shadows, relying instead on **Tonal Layers** combined with **Ambient Botanical Glows**:

- **Level 0 (Canvas Base):** Plain `#FAF8F5` surface with no shadow.
- **Level 1 (Cards & Modules):** Pure white (`#FFFFFF`) surface framed by a hairline border (`1px solid #F0ECE4`) and an ultra-soft diffuse shadow: `0 4px 20px -2px rgba(31, 32, 36, 0.04)`.
- **Level 2 (Active Voice Hub & Floating Overlays):** `#FFFFFF` backing enriched with a dual ambient aura: `0 8px 30px -4px rgba(184, 165, 239, 0.28), 0 2px 8px 0 rgba(31, 32, 36, 0.04)`.
- **Level 3 (Active Listening Waveform Glow):** Concentric soft outer glows extending outward via multi-layered box-shadow or radial-gradient halos (`0 0 48px 12px rgba(184, 165, 239, 0.35)` blending into `rgba(182, 224, 90, 0.2)`).

## Shapes

The shape vocabulary is friendly, organic, and approachable:

- **Cards & Modules:** Structured with a primary radius of `16px` (`rounded-lg`), creating an inviting, non-rigid visual enclosure.
- **Voice Panels & Primary Containers:** Large focus sheets, onboarding prompt carousels, and persistent bottom trays employ a `24px` radius (`rounded-xl`).
- **Interactive Pills & Mic Triggers:** Voice response suggestions, input chips, language selector toggles, and push-to-talk mic buttons utilize full pill curvature (`9999px`), ensuring tactile comfort and instant affordance.

## Components

### 1. Primary Voice Trigger (Orb / Mic Button)
- **Geometry:** 72px to 84px circular pill.
- **Visuals:** Lavender `#B8A5EF` background with white or deep charcoal central microphone iconography.
- **Interaction States:** When active and listening, the trigger exhibits an organic fluid breathing effect using soft Mint `#B6E05A` and Lavender `#B8A5EF` ambient outer rings. Long-press triggers continuous conversation mode.

### 2. Suggestion & Context Chips
- **Geometry:** Height 36px–40px, full pill shape, horizontal auto-scrolling rail.
- **Styling:** White `#FFFFFF` fill, 1px border `#F0ECE4`, label in `label-md` (`#343740`).
- **Active / Pressed State:** Shifts to Lavender tint (`rgba(184, 165, 239, 0.16)`) with stroke `#B8A5EF` and text `#1F2024`.

### 3. Speech Turn Cards
- **Geometry:** Border radius 16px, padding `space-md` (16px).
- **Saathi Card:** Background `#FFFFFF`, 1px border `#F0ECE4`, Level 1 ambient elevation. Contains subtle speaker waveform tags tinted with secondary Mint `#B6E05A`.
- **User Utterance Bubble:** Background `rgba(184, 165, 239, 0.14)`, no outline, aligned gracefully to prompt flow.

### 4. Interactive Multilingual Language Selector
- **Structure:** Segmented pill container with `#F0ECE4` background and 4px internal padding.
- **Segment Item:** Rounded pill item displaying dual-script typography (e.g., "English / हिन्दी"). Active state features `#FFFFFF` elevated pill with `label-sm` in `#1F2024`.

### 5. Input Fields & Search Bars
- **Geometry:** Height 48px, rounded 16px or full pill.
- **Styling:** `#FFFFFF` background with 1px `#F0ECE4` outline. Leading voice shortcut icon, trailing subtle keyboard toggle. Focus outline transitions softly to `#B8A5EF` with 3px diffuse focus ring (`rgba(184, 165, 239, 0.25)`).

### 6. Onboarding & Mood Cards
- **Geometry:** Border radius 24px, generous interior padding `space-lg` (24px).
- **Visuals:** Warm gradient overlays mixing `#FAF8F5`, `#FFFFFF`, and subtle corners tinted in soft Mint `#B6E05A` and Lavender `#B8A5EF`. Clean, conversational introductory prompts welcoming users by voice.