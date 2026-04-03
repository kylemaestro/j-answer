import { useCallback, useState } from "react";
import { Flashcard } from "./components/Flashcard";
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
      <header className="flex flex-col items-center gap-2 px-4 pb-4 pt-10 text-center sm:pt-14">
        <h1 className="text-2xl font-black uppercase tracking-tight text-clue sm:text-3xl">
          j-answer
        </h1>
        <p className="max-w-md text-sm text-clue opacity-90">
          Jeopardy flashcards from your archive — random clue, flip for the
          response.
        </p>
        <button
          type="button"
          onClick={() => void lucky()}
          disabled={loading}
          className="mt-2 rounded-full border-2 border-white/90 bg-white/10 px-8 py-3 text-sm font-bold uppercase tracking-widest text-clue shadow-clue-glow backdrop-blur-sm transition hover:bg-white/20 active:scale-[0.98] disabled:cursor-wait disabled:opacity-70"
        >
          {loading ? "Drawing…" : "I'm feeling lucky"}
        </button>
        {error ? (
          <p
            className="mt-3 max-w-lg rounded-lg border border-white/30 bg-black/20 px-4 py-2 text-sm text-clue"
            role="alert"
          >
            {error}
          </p>
        ) : null}
      </header>

      <main className="flex flex-1 flex-col items-center justify-center px-4 pb-16 pt-4">
        {clue ? <Flashcard clue={clue} /> : null}
        {!clue && !loading && !error ? (
          <p className="max-w-sm text-center text-sm text-clue opacity-80">
            Press <strong className="text-white">I&apos;m feeling lucky</strong>{" "}
            to pull a random clue from your database.
          </p>
        ) : null}
      </main>
    </div>
  );
}
