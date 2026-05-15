import { useCallback, useEffect, useState } from "react";
import type { RandomClue } from "../types";

type SearchResponse = {
  tags: string[];
  count: number;
  clues: RandomClue[];
};

async function fetchSearch(tags: string[]): Promise<SearchResponse> {
  const params = new URLSearchParams();
  for (const t of tags) {
    params.append("tag", t);
  }
  const res = await fetch(`/api/search?${params.toString()}`);
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
  return body as SearchResponse;
}

type SearchPanelProps = {
  onSelectClue: (clue: RandomClue) => void;
};

export function SearchPanel({ onSelectClue }: SearchPanelProps) {
  const [input, setInput] = useState("");
  const [tags, setTags] = useState<string[]>([]);
  const [results, setResults] = useState<RandomClue[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const addTag = useCallback((raw: string) => {
    const s = raw.trim();
    if (!s) return;
    setTags((prev) => {
      if (prev.some((p) => p.toLowerCase() === s.toLowerCase())) {
        return prev;
      }
      return [...prev, s];
    });
    setInput("");
  }, []);

  const removeTag = useCallback((index: number) => {
    setTags((prev) => prev.filter((_, i) => i !== index));
  }, []);

  useEffect(() => {
    if (tags.length === 0) {
      setResults([]);
      setError(null);
      return;
    }

    const t = window.setTimeout(() => {
      setLoading(true);
      setError(null);
      void fetchSearch(tags)
        .then((data) => {
          setResults(data.clues);
        })
        .catch((e) => {
          setResults([]);
          setError(e instanceof Error ? e.message : "Search failed");
        })
        .finally(() => setLoading(false));
    }, 280);

    return () => window.clearTimeout(t);
  }, [tags]);

  return (
    <section
      className="mx-auto w-full max-w-2xl px-4 pb-6"
      aria-label="Search clues"
    >
      <form
        className="flex flex-col gap-2 sm:flex-row sm:items-stretch"
        onSubmit={(e) => {
          e.preventDefault();
          addTag(input);
        }}
      >
        <label className="sr-only" htmlFor="search-input">
          Search term
        </label>
        <input
          id="search-input"
          type="search"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder='e.g. birds, answer:mallard, year:2019 — Enter to add'
          className="min-h-11 flex-1 rounded-xl border border-white/25 bg-white/[0.075] px-4 py-2 text-sm text-white placeholder:text-white/45 outline-none ring-white/30 focus:border-white/50 focus:ring-2"
          autoComplete="off"
        />
        <button
          type="submit"
          className="min-h-11 shrink-0 rounded-xl border-2 border-white/80 bg-white/15 px-5 text-sm font-bold uppercase tracking-wide text-clue shadow-clue-glow transition hover:bg-white/25"
        >
          Add tag
        </button>
      </form>

      {tags.length > 0 ? (
        <div
          className="mt-3 flex flex-wrap gap-2"
          role="list"
          aria-label="Active search tags"
        >
          {tags.map((tag, i) => (
            <span
              key={`${tag}-${i}`}
              role="listitem"
              className="inline-flex items-center gap-1.5 rounded-full border border-white/35 bg-black/20 py-1 pl-3 pr-1 text-sm font-medium text-clue"
            >
              <span className="max-w-[200px] truncate" title={tag}>
                {tag}
              </span>
              <button
                type="button"
                onClick={() => removeTag(i)}
                className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full text-lg leading-none text-white/90 transition hover:bg-white/20 hover:text-white"
                aria-label={`Remove tag ${tag}`}
              >
                ×
              </button>
            </span>
          ))}
        </div>
      ) : (
        <p className="mt-2 text-center text-xs text-white/55 text-clue">
          Tags use <strong className="text-white/90">AND</strong>. Plain words match
          clue, answer, or category. Narrow with{" "}
          <strong className="text-white/90">answer:</strong>,{" "}
          <strong className="text-white/90">clue:</strong>,{" "}
          <strong className="text-white/90">category:</strong>, or{" "}
          <strong className="text-white/90">year:YYYY</strong>.
        </p>
      )}

      {loading ? (
        <p className="mt-4 text-center text-sm text-clue opacity-80">
          Searching…
        </p>
      ) : null}
      {error ? (
        <p
          className="mt-4 rounded-lg border border-white/30 bg-black/25 px-3 py-2 text-center text-sm text-clue"
          role="alert"
        >
          {error}
        </p>
      ) : null}

      {!loading && tags.length > 0 && !error ? (
        <p className="mt-3 text-center text-xs text-white/70 text-clue">
          {results.length} match{results.length === 1 ? "" : "es"} — tap a row
          to load the card
        </p>
      ) : null}

      {results.length > 0 ? (
        <ul
          className="mt-3 max-h-[min(45vh,320px)] space-y-2 overflow-y-auto rounded-xl border border-white/15 bg-black/15 p-2"
          aria-label="Search results"
        >
          {results.map((c) => (
            <li key={c.id}>
              <button
                type="button"
                onClick={() => onSelectClue(c)}
                className="w-full rounded-lg border border-transparent px-3 py-2.5 text-left transition hover:border-white/25 hover:bg-white/10"
              >
                <div className="flex flex-wrap items-baseline justify-between gap-2 text-xs font-semibold uppercase tracking-wide text-clue opacity-90">
                  <span className="min-w-0 flex-1 truncate">
                    {c.game_category}
                  </span>
                  <span className="shrink-0 tabular-nums opacity-80">
                    {c.year ?? c.air_date.slice(0, 4)}
                  </span>
                </div>
                <p className="mt-1 line-clamp-2 text-sm leading-snug text-clue">
                  {c.clue_text}
                </p>
              </button>
            </li>
          ))}
        </ul>
      ) : null}
    </section>
  );
}
