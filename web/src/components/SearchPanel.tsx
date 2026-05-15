import { useCallback, useEffect, useState } from "react";
import type { RandomClue } from "../types";

type SearchMode = "exact" | "magic";

type ExactSearchResponse = {
  mode: string;
  tags: string[];
  count: number;
  clues: RandomClue[];
};

type MagicSearchResponse = {
  mode: string;
  query: string;
  embedded_pool: number;
  count: number;
  clues: RandomClue[];
};

type EmbeddingsStatus = {
  embedded: number;
  magic_available: boolean;
  min_score: number;
};

async function parseError(res: Response): Promise<string> {
  const body: unknown = await res.json().catch(() => ({}));
  if (
    body &&
    typeof body === "object" &&
    "detail" in body &&
    typeof (body as { detail: unknown }).detail === "string"
  ) {
    return (body as { detail: string }).detail;
  }
  return `Request failed (${res.status})`;
}

async function fetchExactSearch(tags: string[]): Promise<ExactSearchResponse> {
  const params = new URLSearchParams();
  for (const t of tags) {
    params.append("tag", t);
  }
  const res = await fetch(`/api/search?${params.toString()}`);
  if (!res.ok) throw new Error(await parseError(res));
  return res.json() as Promise<ExactSearchResponse>;
}

async function fetchMagicSearch(query: string): Promise<MagicSearchResponse> {
  const params = new URLSearchParams({ q: query });
  const res = await fetch(`/api/search/magic?${params.toString()}`);
  if (!res.ok) throw new Error(await parseError(res));
  return res.json() as Promise<MagicSearchResponse>;
}

async function fetchEmbeddingsStatus(): Promise<EmbeddingsStatus> {
  const res = await fetch("/api/embeddings/status");
  if (!res.ok) throw new Error(await parseError(res));
  return res.json() as Promise<EmbeddingsStatus>;
}

type SearchPanelProps = {
  onSelectClue: (clue: RandomClue) => void;
};

export function SearchPanel({ onSelectClue }: SearchPanelProps) {
  const [mode, setMode] = useState<SearchMode>("exact");
  const [input, setInput] = useState("");
  const [tags, setTags] = useState<string[]>([]);
  const [magicQuery, setMagicQuery] = useState("");
  const [results, setResults] = useState<RandomClue[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [embeddedPool, setEmbeddedPool] = useState<number | null>(null);

  useEffect(() => {
    void fetchEmbeddingsStatus()
      .then((s) => setEmbeddedPool(s.embedded))
      .catch(() => setEmbeddedPool(null));
  }, []);

  const setSearchMode = useCallback((next: SearchMode) => {
    setMode(next);
    setError(null);
    setResults([]);
    if (next === "magic") {
      setTags([]);
      setInput("");
    } else {
      setMagicQuery("");
    }
  }, []);

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
    if (mode !== "exact") return;
    if (tags.length === 0) {
      setResults([]);
      setError(null);
      return;
    }

    const t = window.setTimeout(() => {
      setLoading(true);
      setError(null);
      void fetchExactSearch(tags)
        .then((data) => setResults(data.clues))
        .catch((e) => {
          setResults([]);
          setError(e instanceof Error ? e.message : "Search failed");
        })
        .finally(() => setLoading(false));
    }, 280);

    return () => window.clearTimeout(t);
  }, [mode, tags]);

  useEffect(() => {
    if (mode !== "magic") return;
    const q = magicQuery.trim();
    if (!q) {
      setResults([]);
      setError(null);
      return;
    }

    const t = window.setTimeout(() => {
      setLoading(true);
      setError(null);
      void fetchMagicSearch(q)
        .then((data) => {
          setResults(data.clues);
          setEmbeddedPool(data.embedded_pool);
        })
        .catch((e) => {
          setResults([]);
          setError(e instanceof Error ? e.message : "Search failed");
        })
        .finally(() => setLoading(false));
    }, 400);

    return () => window.clearTimeout(t);
  }, [mode, magicQuery]);

  const resultCount = results.length;
  const showResultSummary =
    !loading &&
    !error &&
    ((mode === "exact" && tags.length > 0) ||
      (mode === "magic" && magicQuery.trim().length > 0));

  return (
    <section
      className="mx-auto w-full max-w-2xl px-4 pb-6"
      aria-label="Search clues"
    >
      <div className="mb-4 flex flex-col items-center gap-2">
        <div
          className="inline-flex rounded-xl border border-white/25 bg-black/20 p-1"
          role="group"
          aria-label="Search mode"
        >
          <button
            type="button"
            aria-pressed={mode === "exact"}
            onClick={() => setSearchMode("exact")}
            className={`min-h-9 rounded-lg px-4 text-xs font-bold uppercase tracking-wide transition ${
              mode === "exact"
                ? "bg-white/20 text-clue shadow-sm"
                : "text-white/70 hover:text-white"
            }`}
          >
            Exact
          </button>
          <button
            type="button"
            aria-pressed={mode === "magic"}
            onClick={() => setSearchMode("magic")}
            className={`min-h-9 rounded-lg px-4 text-xs font-bold uppercase tracking-wide transition ${
              mode === "magic"
                ? "bg-white/20 text-clue shadow-sm"
                : "text-white/70 hover:text-white"
            }`}
          >
            Magic
          </button>
        </div>
        {mode === "magic" && embeddedPool !== null ? (
          <p className="text-center text-xs text-white/60 text-clue">
            Searching{" "}
            <strong className="text-white/90">
              {embeddedPool.toLocaleString()}
            </strong>{" "}
            embedded clue{embeddedPool === 1 ? "" : "s"}
          </p>
        ) : null}
      </div>

      {mode === "exact" ? (
        <>
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
              Tags use <strong className="text-white/90">AND</strong>. Plain
              words match clue, answer, or category. Narrow with{" "}
              <strong className="text-white/90">answer:</strong>,{" "}
              <strong className="text-white/90">clue:</strong>,{" "}
              <strong className="text-white/90">category:</strong>, or{" "}
              <strong className="text-white/90">year:YYYY</strong>.
            </p>
          )}
        </>
      ) : (
        <form
          className="flex flex-col"
          onSubmit={(e) => {
            e.preventDefault();
          }}
        >
          <label className="sr-only" htmlFor="magic-search-input">
            Magic search
          </label>
          <input
            id="magic-search-input"
            type="search"
            value={magicQuery}
            onChange={(e) => setMagicQuery(e.target.value)}
            placeholder='Search for anything (e.g. US presidents, Shakespeare, 80s pop music)'
            className="min-h-11 w-full rounded-xl border border-white/25 bg-white/[0.075] px-4 py-2 text-sm text-white placeholder:text-white/45 outline-none ring-white/30 focus:border-white/50 focus:ring-2"
            autoComplete="off"
          />
          {embeddedPool === 0 ? (
            <p className="mt-2 text-center text-xs text-amber-200/90 text-clue">
              No embeddings yet — run{" "}
              <code className="rounded bg-black/30 px-1 py-0.5 text-[0.7rem]">
                embed/vector-embed.py
              </code>{" "}
              with{" "}
              <code className="rounded bg-black/30 px-1 py-0.5 text-[0.7rem]">
                --limit
              </code>{" "}
              to try Magic search on a sample.
            </p>
          ) : (
            <p className="mt-2 text-center text-xs text-white/55 text-clue">
              Describe a topic or vibe; results use AI similarity (tags not
              available in Magic mode).
            </p>
          )}
        </form>
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

      {showResultSummary ? (
        <p className="mt-3 text-center text-xs text-white/70 text-clue">
          {resultCount} match{resultCount === 1 ? "" : "es"}
          {mode === "magic" ? " above similarity cutoff" : ""} — tap a row to
          load the card
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
                  <span className="flex shrink-0 items-center gap-2 tabular-nums opacity-80">
                    {typeof c.score === "number" ? (
                      <span
                        className="rounded bg-white/15 px-1.5 py-0.5 text-[0.65rem] font-bold normal-case"
                        title="Similarity score"
                      >
                        {c.score.toFixed(2)}
                      </span>
                    ) : null}
                    <span>{c.year ?? c.air_date.slice(0, 4)}</span>
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
