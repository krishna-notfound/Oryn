import Link from "next/link";
import { ArrowLeft, Leaf } from "lucide-react";
import type { ReactNode } from "react";

/** Oryn mark + wordmark (replaces the Stitch logo image). */
export function Wordmark({ size = "md" }: { size?: "md" | "lg" }) {
  return (
    <span className="inline-flex items-center gap-2">
      <span
        className={`grid place-items-center rounded-full bg-primary-fixed text-primary ${size === "lg" ? "size-10" : "size-8"}`}
      >
        <Leaf size={size === "lg" ? 22 : 18} aria-hidden="true" />
      </span>
      <span className={`type-headline-sm tracking-tight text-on-surface ${size === "lg" ? "text-[26px]" : ""}`}>Oryn</span>
    </span>
  );
}

interface TopBarProps {
  title?: string;
  /** Href for a back arrow; omit for the brand wordmark. */
  backHref?: string;
  right?: ReactNode;
  wide?: boolean;
}

/** Sticky, blurred header used by every in-app screen. */
export function TopBar({ title, backHref, right, wide = false }: TopBarProps) {
  return (
    <header className="pt-safe sticky top-0 z-30 bg-surface/85 shadow-[0_1px_8px_rgba(0,0,0,0.04)] backdrop-blur-xl">
      <div className={`${wide ? "page-col-wide" : "page-col"} flex h-16 items-center justify-between gap-space-sm px-margin`}>
        <div className="flex min-w-0 items-center gap-space-xs">
          {backHref && (
            <Link
              href={backHref}
              aria-label="Back"
              className="-ml-2 grid size-11 place-items-center rounded-full text-on-surface hover:bg-surface-container active:scale-95"
            >
              <ArrowLeft size={22} />
            </Link>
          )}
          {title ? <h1 className="type-headline-sm truncate text-on-surface">{title}</h1> : <Wordmark />}
        </div>
        <div className="flex items-center gap-1">{right}</div>
      </div>
    </header>
  );
}

/** 44px round icon link for header actions. */
export function IconLink({ href, label, children }: { href: string; label: string; children: ReactNode }) {
  return (
    <Link
      href={href}
      aria-label={label}
      className="grid size-11 place-items-center rounded-full text-on-surface-variant hover:bg-surface-container active:scale-95"
    >
      {children}
    </Link>
  );
}

interface ToggleProps {
  checked: boolean;
  onChange: (checked: boolean) => void;
  label: string;
  disabled?: boolean;
}

/** Accessible switch in the Stitch settings style (lavender when on). */
export function Toggle({ checked, onChange, label, disabled }: ToggleProps) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      disabled={disabled}
      onClick={() => onChange(!checked)}
      className={`relative h-7 w-12 shrink-0 rounded-full transition-colors disabled:opacity-50 ${
        checked ? "bg-primary" : "bg-surface-container-highest"
      }`}
    >
      <span
        className={`absolute left-[3px] top-[3px] size-5 rounded-full bg-surface-container-lowest shadow-sm transition-transform ${
          checked ? "translate-x-5" : ""
        }`}
      />
    </button>
  );
}

/** Small selectable pill (interests, extra languages). */
export function Chip({ selected, onClick, children }: { selected: boolean; onClick: () => void; children: ReactNode }) {
  return (
    <button
      type="button"
      aria-pressed={selected}
      onClick={onClick}
      className={`type-label-md min-h-10 rounded-full border px-4 transition-colors active:scale-95 ${
        selected
          ? "border-primary-container bg-primary-fixed text-on-primary-fixed"
          : "border-outline-variant/50 bg-surface-container-lowest text-on-surface-variant"
      }`}
    >
      {children}
    </button>
  );
}

/** Two- or three-option segmented control (response length, formality...). */
export function Segmented<T extends string>({
  value,
  options,
  onChange,
  label,
}: {
  value: T;
  options: readonly { value: T; label: string }[];
  onChange: (value: T) => void;
  label: string;
}) {
  return (
    <div role="radiogroup" aria-label={label} className="flex rounded-full bg-surface-container p-1">
      {options.map((option) => (
        <button
          key={option.value}
          type="button"
          role="radio"
          aria-checked={option.value === value}
          onClick={() => onChange(option.value)}
          className={`type-label-md min-h-10 flex-1 rounded-full px-3 transition-all ${
            option.value === value
              ? "bg-surface-container-lowest text-on-surface shadow-sm"
              : "text-on-surface-variant"
          }`}
        >
          {option.label}
        </button>
      ))}
    </div>
  );
}

export function GoogleIcon() {
  return (
    <svg className="size-5 shrink-0" viewBox="0 0 24 24" aria-hidden="true">
      <path d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z" fill="#4285F4" />
      <path d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z" fill="#34A853" />
      <path d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.06H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.94l2.85-2.22.81-.63z" fill="#FBBC05" />
      <path d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.06l3.66 2.84c.87-2.6 3.3-4.52 6.16-4.52z" fill="#EA4335" />
    </svg>
  );
}
