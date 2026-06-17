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

const TAGLINE = "Don't put your future in jeopardy!";

type Pending = "lucky" | "daily" | null;

export default function App() {
  const [clue, setClue] = useState<RandomClue | null>(null);
  const [pending, setPending] = useState<Pending>(null);
  const [error, setError] = useState<string | null>(null);

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
        <h1 className="text-2xl font-black uppercase tracking-tight text-clue sm:text-3xl">
          j-answer
        </h1>
        <WaveText
          text={TAGLINE}
          className="block select-none text-balance text-lg font-black uppercase tracking-[0.12em] text-gold sm:text-xl"
        />
      </header>

      <SearchPanel
        onSelectClue={setClue}
        onLucky={() => void load("lucky", "/api/random-clue")}
        luckyLoading={pending === "lucky"}
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
      </main>

      <footer className="px-4 pb-6 pt-2 text-center">
        <p className="text-xs font-medium uppercase tracking-[0.18em] text-clue opacity-70">
          Made with <span className="not-italic">🛸</span> in Seattle
        </p>
      </footer>
    </div>
  );
}
