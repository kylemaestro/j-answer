type Segment = { text: string; italic?: boolean };

type WaveTextProps = {
  /** Plain text. Ignored when `segments` is provided. */
  text?: string;
  /** Styled runs, rendered as one continuous wave (for italicizing a word). */
  segments?: Segment[];
  className?: string;
  /**
   * Play the snap animation a single time instead of looping. Re-trigger by
   * giving the element a changing `key` so it remounts.
   */
  once?: boolean;
};

/**
 * Renders text as individual letters with a staggered "snap" animation
 * (see `.wave-letter` in index.css). Spaces keep their width; the whole
 * string is exposed to assistive tech via aria-label.
 */
export function WaveText({ text, segments, className, once }: WaveTextProps) {
  const segs: Segment[] = segments ?? [{ text: text ?? "" }];
  const label = segs.map((s) => s.text).join("");
  const letterBase = once ? "wave-letter wave-once" : "wave-letter";

  let index = 0;
  return (
    <span className={className} aria-label={label}>
      {segs.flatMap((seg, si) =>
        Array.from(seg.text).map((ch, ci) => {
          const i = index++;
          if (ch === " ") {
            return (
              <span key={`${si}-${ci}`} aria-hidden className="inline-block w-[0.3em]" />
            );
          }
          return (
            <span
              key={`${si}-${ci}`}
              aria-hidden
              className={seg.italic ? `${letterBase} italic` : letterBase}
              style={{ ["--wave-i" as string]: i }}
            >
              {ch}
            </span>
          );
        }),
      )}
    </span>
  );
}
