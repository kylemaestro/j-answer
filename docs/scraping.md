# Scraping and the command-line interface

The CLI builds and maintains the SQLite corpus by scraping
[J-Archive](https://j-archive.com). Run it as a module from the repository root
so imports resolve:

```bash
python -m janswer [global options] <command> ...
```

Global options must appear **before** the subcommand (e.g. before `season`,
`crawl`, ...).

Requirements: Python 3.10+ and network access to `j-archive.com`.

## Global options

| Option | Default | Used by | Description |
| ------ | ------- | ------- | ----------- |
| `--db` | `j-answer.db` | All | Path to the SQLite database file. |
| `--delay` | `1.5` | `season`, `crawl discover`, `crawl run` | Seconds to wait between HTTP requests in loops (polite pacing). Ignored by `game` and `crawl status`. |

## `game` — scrape one episode

Fetches a single game page by J-Archive `game_id` and inserts clues. Duplicates
are skipped (`jarchive_clue_id` is unique).

```bash
python -m janswer game 1
```

`--delay` is accepted but has no effect for this command.

## `season` — scrape every game on a season page

Season numbers match J-Archive (e.g.
[Season 1](https://j-archive.com/showseason.php?season=1)).

```bash
python -m janswer season 1
```

| Flag | Default | Description |
| ---- | ------- | ----------- |
| `--limit N` | `0` (no cap) | Process at most `N` games from the listing. |
| `--db` | `j-answer.db` | Database path. |
| `--delay` | `1.5` | Pause between game requests within the season. |

## `crawl` — resumable full-archive queue

Three subcommands. Global `--db` / `--delay` apply where noted.

### `crawl discover`

Walks `listseasons.php` and each season listing, registering `game_id`s into
`crawl_games` (`INSERT OR IGNORE`). Safe to re-run; does not wipe completed
rows.

```bash
python -m janswer crawl discover
```

| Flag | Default | Description |
| ---- | ------- | ----------- |
| `--max-seasons N` | `0` | Process only the first `N` seasons. `0` means all (useful for dry runs). |
| `--db` | `j-answer.db` | Database path. |
| `--delay` | `1.5` | Pause between season-page requests. |

### `crawl run`

Processes `crawl_games` rows with status `pending` or `failed`, one game per
transaction (resumable). If the queue is empty, run `crawl discover` first.

```bash
python -m janswer crawl run
```

| Flag | Default | Description |
| ---- | ------- | ----------- |
| `--max-games N` | `0` | Cap this run at `N` games. `0` means all `pending` + `failed`. |
| `--http-retries` | `4` | HTTP attempts per game. Non-retryable failures stop immediately; 429 and 503 trigger waits (see `--backoff-base`). |
| `--backoff-base` | `30.0` | Seconds before the first retry after a 429/503. Each retry doubles the wait (30, 60, 120, ...). |
| `--db` | `j-answer.db` | Database path. |
| `--delay` | `1.5` | Pause between games while draining the queue. |

### `crawl status`

Prints queue counts (`pending`, `failed`, `complete`, ...) and the last
discover timestamp. No HTTP scraping. Accepts `--db` only.

```bash
python -m janswer crawl status
```

## Example: full Season 1

Import every game on the Season 1 page using default pacing:

```bash
cd /path/to/j-answer
pip install -r requirements.txt
python -m janswer season 1
```

Scrape more gently, or only a sample:

```bash
python -m janswer --delay 2.5 season 1
python -m janswer season 1 --limit 5
```

Re-running a season is safe: existing clues are skipped.

Windows (PowerShell) uses the same commands if `python` is on `PATH`:

```powershell
cd C:\path\to\j-answer
pip install -r requirements.txt
python -m janswer season 1
```
