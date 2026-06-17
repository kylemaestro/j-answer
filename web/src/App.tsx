import { useCallback, useEffect, useState } from "react";
import { Flashcard } from "./components/Flashcard";
import { SearchPanel } from "./components/SearchPanel";
import { WaveText } from "./components/WaveText";
import type { RandomClue } from "./types";

async function fetchClue(path: string): Promise<RandomClue> {
  const res = await fetch(path);
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

// Tagline split so "your" can be italicized while the wave stays continuous.
const TAGLINE_SEGMENTS = [
  { text: "Don't put " },
  { text: "your", italic: true },
  { text: " future in jeopardy!" },
];

type Pending = "lucky" | "daily" | null;

export default function App() {
  const [clue, setClue] = useState<RandomClue | null>(null);
  const [pending, setPending] = useState<Pending>(null);
  const [error, setError] = useState<string | null>(null);
  // Bumping this remounts the tagline, replaying its one-shot animation.
  const [taglineReplay, setTaglineReplay] = useState(0);
  const replayTagline = useCallback(() => setTaglineReplay((n) => n + 1), []);

  const load = useCallback(async (kind: Exclude<Pending, null>, path: string) => {
    setPending(kind);
    setError(null);
    try {
      setClue(await fetchClue(path));
    } catch (e) {
      setClue(null);
      setError(e instanceof Error ? e.message : "Something went wrong");
    } finally {
      setPending(null);
    }
  }, []);

  // Greet visitors with the Daily Double of the day.
  useEffect(() => {
    void load("daily", "/api/daily-double");
  }, [load]);

  return (
    <div className="flex min-h-screen flex-col bg-[#030f7d]">
      <header className="flex flex-col items-center gap-2 px-4 pb-2 pt-10 text-center sm:pt-14">
        <h1 className="text-3xl font-black uppercase tracking-tight text-clue sm:text-4xl">
          j-answer
        </h1>
        <WaveText
          key={taglineReplay}
          once
          segments={TAGLINE_SEGMENTS}
          className="block select-none text-balance text-xl font-black uppercase tracking-[0.12em] text-gold sm:text-2xl"
        />
      </header>

      <SearchPanel
        onSelectClue={setClue}
        onLucky={() => void load("lucky", "/api/random-clue")}
        luckyLoading={pending === "lucky"}
        onResults={replayTagline}
      />

      <main className="flex flex-1 flex-col items-center justify-center px-4 pb-12 pt-2">
        {clue ? <Flashcard key={clue.id} clue={clue} /> : null}
        {error ? (
          <p
            className="mt-3 max-w-lg rounded-lg border border-white/30 bg-black/20 px-4 py-2 text-center text-base text-clue"
            role="alert"
          >
            {error}
          </p>
        ) : null}
      </main>

      <footer className="px-4 pb-6 pt-2 text-center">
        <p className="text-sm font-medium uppercase tracking-[0.18em] text-clue opacity-70">
          Made with <span className="not-italic">🛸</span> in Seattle
        </p>
      </footer>
    </div>
  );
}
