type WaveTextProps = {
  text: string;
  className?: string;
};

/**
 * Renders text as individual letters with a staggered "snap" animation
 * (see `.wave-letter` in index.css). Spaces keep their width; the whole
 * string is exposed to assistive tech via aria-label.
 */
export function WaveText({ text, className }: WaveTextProps) {
  return (
    <span className={className} aria-label={text}>
      {Array.from(text).map((ch, i) =>
        ch === " " ? (
          <span key={i} aria-hidden className="inline-block w-[0.3em]" />
        ) : (
          <span
            key={i}
            aria-hidden
            className="wave-letter"
            style={{ ["--wave-i" as string]: i }}
          >
            {ch}
          </span>
        ),
      )}
    </span>
  );
}
