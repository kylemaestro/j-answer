import { useEffect, useRef, useState } from "react";
import type { RandomClue } from "../types";

const MAX_TILT = 16;

function formatValue(c: RandomClue): string {
  if (c.is_daily_double) return "DAILY DOUBLE";
  if (c.value_display) return c.value_display;
  if (c.value_amount != null) return `$${c.value_amount}`;
  return "";
}

type FlashcardProps = {
  clue: RandomClue;
};

export function Flashcard({ clue }: FlashcardProps) {
  const [flipped, setFlipped] = useState(false);
  const [tilt, setTilt] = useState({ x: 0, y: 0 });
  const [hovering, setHovering] = useState(false);
  const sceneRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    setFlipped(false);
  }, [clue.id]);

  const handleMove = (e: React.MouseEvent<HTMLDivElement>) => {
    const el = sceneRef.current;
    if (!el) return;
    const rect = el.getBoundingClientRect();
    const nx = ((e.clientX - rect.left) / rect.width - 0.5) * 2;
    const ny = ((e.clientY - rect.top) / rect.height - 0.5) * 2;
    setTilt({ x: ny * -MAX_TILT, y: nx * MAX_TILT });
  };

  const handleLeave = () => {
    setHovering(false);
    setTilt({ x: 0, y: 0 });
  };

  const valueLine = formatValue(clue);
  const yearLabel = clue.year != null ? String(clue.year) : clue.air_date.slice(0, 4);

  return (
    <div
      ref={sceneRef}
      className="mx-auto w-full max-w-lg select-none"
      style={{ perspective: "1100px" }}
      onMouseEnter={() => setHovering(true)}
      onMouseMove={handleMove}
      onMouseLeave={handleLeave}
    >
      <div
        className="will-change-transform"
        style={{
          transform: `rotateX(${tilt.x}deg) rotateY(${tilt.y}deg)`,
          transformStyle: "preserve-3d",
          transition: hovering ? "none" : "transform 0.45s cubic-bezier(0.22, 1, 0.36, 1)",
        }}
      >
        <div
          className="relative h-[min(52vh,380px)] w-full cursor-pointer rounded-2xl [transform-style:preserve-3d] focus:outline-none focus-visible:ring-2 focus-visible:ring-white/70"
          style={{
            transform: flipped ? "rotateY(180deg)" : "rotateY(0deg)",
            transition:
              "transform 0.38s cubic-bezier(0.34, 1.15, 0.64, 1)",
          }}
          role="button"
          tabIndex={0}
          aria-label={flipped ? "Show clue" : "Show answer"}
          onClick={() => setFlipped((f) => !f)}
          onKeyDown={(e) => {
            if (e.key === " " || e.key === "Enter") {
              e.preventDefault();
              setFlipped((f) => !f);
            }
          }}
        >
          {/* Front — clue */}
          <div
            className="backface-hidden absolute inset-0 flex flex-col overflow-hidden rounded-2xl border border-white/20 bg-gradient-to-b from-white/10 to-black/20 shadow-[0_20px_50px_rgba(0,0,0,0.35)]"
            style={{ transform: "rotateY(0deg)" }}
          >
            <div className="flex shrink-0 flex-col gap-1 border-b border-white/15 px-5 py-3 text-sm font-semibold tracking-wide text-clue">
              <div className="flex w-full items-start justify-between gap-3">
                <span className="min-w-0 flex-1 text-left uppercase leading-tight">
                  {clue.game_category}
                </span>
                <span className="shrink-0 tabular-nums opacity-95">
                  {yearLabel}
                </span>
              </div>
              {valueLine ? (
                <div className="text-right text-base font-bold">{valueLine}</div>
              ) : null}
            </div>
            <div className="flex flex-1 items-center justify-center px-6 py-6 text-center">
              <p className="text-balance text-2xl font-semibold leading-snug text-clue sm:text-3xl">
                {clue.clue_text}
              </p>
            </div>
            <p className="pb-3 text-center text-xs text-white/60 text-clue">
              Tap to reveal the answer
            </p>
          </div>

          {/* Back — answer */}
          <div
            className="backface-hidden absolute inset-0 flex flex-col items-center justify-center overflow-hidden rounded-2xl border border-white/20 bg-gradient-to-b from-black/25 to-white/5 px-6 py-8 shadow-[0_20px_50px_rgba(0,0,0,0.35)]"
            style={{ transform: "rotateY(180deg)" }}
          >
            <p className="mb-2 text-xs font-bold uppercase tracking-[0.2em] text-clue opacity-80">
              What is…
            </p>
            <p className="text-center text-2xl font-bold leading-snug text-clue sm:text-3xl">
              {clue.answer_text}
            </p>
            <p className="mt-6 text-center text-xs text-white/55 text-clue">
              Tap to see the clue again
            </p>
          </div>
        </div>
      </div>
    </div>
  );
}
