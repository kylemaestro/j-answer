import { useCallback, useState } from "react";
import { Flashcard } from "./components/Flashcard";
import { SearchPanel } from "./components/SearchPanel";
import type { RandomClue } from "./types";

async function fetchRandomClue(): Promise<RandomClue> {
  const res = await fetch("/api/random-clue");
  const body: unknown = await res.json().catch(() => ({}));
  if (!res.ok) {
    const detail =
      body &&
      typeof body === "object" &&
      "detail" in body &&
      typeof (body as { detail: unknown }).detail === "string"
        ? (body as { detail: string }).detail
        : `Request failed (${res.status})`;
    throw new Error(detail);
  }
  return body as RandomClue;
}

const TAGLINE = "Don't put your future in jeopardy!";

/** Per-letter rising wave for the retro tagline. Spaces keep their width. */
function WaveTagline({ text }: { text: string }) {
  return (
    <p
      className="select-none text-balance text-lg font-black uppercase tracking-[0.12em] text-gold sm:text-xl"
      aria-label={text}
    >
      {Array.from(text).map((ch, i) =>
        ch === " " ? (
          <span key={i} aria-hidden className="inline-block w-[0.32em]" />
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
    </p>
  );
}

export default function App() {
  const [clue, setClue] = useState<RandomClue | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const lucky = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const c = await fetchRandomClue();
      setClue(c);
    } catch (e) {
      setClue(null);
      setError(e instanceof Error ? e.message : "Something went wrong");
    } finally {
      setLoading(false);
    }
  }, []);

  return (
    <div className="flex min-h-screen flex-col bg-[#030f7d]">
      <header className="flex flex-col items-center gap-2 px-4 pb-2 pt-10 text-center sm:pt-14">
        <h1 className="text-2xl font-black uppercase tracking-tight text-clue sm:text-3xl">
          j-answer
        </h1>
        <WaveTagline text={TAGLINE} />
      </header>

      <SearchPanel
        onSelectClue={setClue}
        onLucky={() => void lucky()}
        luckyLoading={loading}
      />

      <main className="flex flex-1 flex-col items-center justify-center px-4 pb-12 pt-2">
        {clue ? <Flashcard key={clue.id} clue={clue} /> : null}
        {error ? (
          <p
            className="mt-3 max-w-lg rounded-lg border border-white/30 bg-black/20 px-4 py-2 text-center text-sm text-clue"
            role="alert"
          >
            {error}
          </p>
        ) : null}
        {!clue && !loading && !error ? (
          <p className="max-w-sm text-center text-sm text-clue opacity-80">
            Use <strong className="text-white">search</strong> (Exact or Magic)
            or <strong className="text-white">I&apos;m feeling lucky</strong> to
            load a clue.
          </p>
        ) : null}
      </main>

      <footer className="px-4 pb-6 pt-2 text-center">
        <p className="text-xs font-medium uppercase tracking-[0.18em] text-clue opacity-70">
          Made with <span className="not-italic">🛸</span> in Seattle
        </p>
      </footer>
    </div>
  );
}
