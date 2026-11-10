import type { CaptionFrame } from "@/lib/captions/CaptionTrack";

/**
 * The reply as it is spoken: the current segment large with unspoken words dimmed (so the layout
 * never reflows), the previous segment small and faded above it.
 */
export function Captions({ frame }: { frame: CaptionFrame }) {
  return (
    <div aria-live="off" className="flex flex-col items-center gap-space-xs">
      {frame.previous && <p className="type-body-sm line-clamp-2 text-on-surface-variant/50 transition-opacity">{frame.previous}</p>}
      <p className="type-body-lg text-on-surface">
        {frame.words.map((word, i) => (
          <span key={i} className={`transition-opacity duration-200 ${i < frame.revealed ? "opacity-100" : "opacity-25"}`}>
            {word}{" "}
          </span>
        ))}
      </p>
    </div>
  );
}
