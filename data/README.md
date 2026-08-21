# data/

Scraped fantacalcio datasets for `leghe.fantacalcio.it` decision logic (see
`../CLAUDE.md` → "Stats source"). Each CSV is produced by the matching
script in `../scripts/`:

| CSV | Scraper |
|---|---|
| `quotazioni_classic.csv`, `quotazioni_mantra.csv` | `scripts/scrape_quotazioni.py` |
| `statistiche_classic.csv`, `statistiche_mantra.csv` | `scripts/scrape_statistiche.py` |
| `voti.csv`, `bonus_malus.csv` | `scripts/scrape_voti.py` |
| `player_sentiment_2026-27.csv` | `fantabot news-fetch` (see `../SPEC.md`) |

`classic` = 3-role scoring (D/C/A), `mantra` = multi-role scoring (same
match data, finer-grained role tags). Rows join across files on `id`
(player id) + `stagione`; `voti.csv`/`bonus_malus.csv` also share
`giornata` (matchday) + `squadra` + `avversario` for a given match.

## File summaries

### `quotazioni_classic.csv` — 3,201 rows × 9 cols, 152 KB
Preseason market quotations (quotazioni), one row per player per season.
Seasons: `2022/23`..`2026/27` (5). No nulls.

| col | dtype | notes |
|---|---|---|
| `stagione` | str | e.g. `2022/23` |
| `id` | int | player id, 3–7568, 1414 unique players |
| `nome` | str | 1440 unique |
| `squadra` | str | 3-letter club code, 27 unique (promoted/relegated clubs across seasons) |
| `ruolo_codice` | str | single-letter role: `p/d/c/a` |
| `ruolo` | str | Portiere/Difensore/Centrocampista/Attaccante |
| `qi` | int | quotazione iniziale (season-open credit price), 0–43 |
| `qa` | int | quotazione attuale (current price), 0–44 |
| `fvm` | int | fantavalore di mercato (market value index), 0–500 |

### `quotazioni_mantra.csv` — 3,201 rows × 9 cols, 174 KB
Same rows/season coverage as `quotazioni_classic.csv`, but with mantra's
multi-role tagging instead of `ruolo_codice`/`ruolo`.

| col | dtype | notes |
|---|---|---|
| `ruoli_codice` | str | `;`-joined multi-role codes, e.g. `C;T`, 32 unique combos |
| `ruoli` | str | `;`-joined full role names, e.g. `Cen.centrale;Trequartista` |
| *(rest same as classic: `stagione`, `id`, `nome`, `squadra`, `qi`, `qa`, `fvm`)* | | |

### `statistiche_classic.csv` — 8,034 rows × 18 cols, 662 KB
Season-aggregated per-player performance stats. Seasons: `2022/23`..`2025/26`
(4 — `2026/27` not started yet). No nulls.

| col | dtype | notes |
|---|---|---|
| `stagione`, `id`, `nome`, `squadra`, `ruolo_codice`, `ruolo` | | same semantics as quotazioni |
| `fonte` | str | rating source, 3 unique (`fantacalcio` most common) |
| `partite_giocate` | int | games played, 0–38 |
| `media_voto` | str | avg raw rating, **comma-decimal** (e.g. `"6,25"`) — cast with `.str.replace(',', '.').astype(float)` |
| `media_fantavoto` | str | avg fantasy rating, same comma-decimal format |
| `gol` | int | goals scored, 0–26 |
| `gol_subiti` | int | goals conceded (goalkeepers), 0–68 |
| `rigori_segnati` / `rigori_tirati` / `rigori_parati` | int | penalties scored/taken/saved |
| `assist` | int | 0–17 |
| `ammonizioni` / `espulsioni` | int | yellow/red cards |

### `statistiche_mantra.csv` — 8,034 rows × 18 cols, 716 KB
Same rows/stats as `statistiche_classic.csv`, with `ruoli_codice`/`ruoli`
(multi-role, 32 unique combos) instead of `ruolo_codice`/`ruolo`.

### `voti.csv` — 50,634 rows × 18 cols, 5.0 MB
Per-player, per-matchday match ratings across 3 rating sources. Seasons:
`2022/23`..`2025/26` (4). **`id` has 3,039 nulls** — these are `Allenatore`
(coach) rows, which have no player id.

| col | dtype | notes |
|---|---|---|
| `stagione`, `giornata`, `data`, `ora` | | matchday identity; `data` is `dd/mm/yyyy` str |
| `squadra`, `avversario` | str | team and opponent for that match |
| `gol_squadra`, `gol_avversario` | int | final score for that match |
| `id`, `nome`, `ruolo_codice`, `ruolo` | | player identity (id null for coach rows) |
| `voto_fc` / `fantavoto_fc` | str | rating/fantasy-rating from fantacalcio.it source, comma-decimal, may be empty string for DNP |
| `voto_stat` / `fantavoto_stat` | str | rating from stats-provider source |
| `voto_italia` / `fantavoto_italia` | str | rating from Italia source |

### `bonus_malus.csv` — 50,634 rows × 19 cols, 4.3 MB
Same grain as `voti.csv` (one row per player per match) but with raw
bonus/malus event counts instead of ratings. Same `id` null count (3,039,
coach rows) and season coverage (`2022/23`..`2025/26`).

| col | dtype | notes |
|---|---|---|
| `stagione`, `giornata`, `data`, `squadra`, `avversario` | | same as voti.csv |
| `id`, `nome`, `ruolo_codice`, `ruolo` | | player identity |
| `ammonizione`, `espulsione` | int | 0/1 flags |
| `gol_segnati`, `gol_subiti`, `autoreti` | int | goals for/against/own-goals, per match |
| `rigori_segnati`, `rigori_sbagliati`, `rigori_parati` | int | penalty scored/missed/saved |
| `assist` | int | |
| `mvp` | int | 0/1 man-of-the-match flag |

### `player_sentiment_2026-27.csv` — one row per player per run, appended weekly
Agent-collected news sentiment. **Not a scraper output**: `fantabot news-fetch`
runs one Claude Agent SDK query per player over WebSearch/WebFetch and validates
the reply against a pydantic schema. Runs every Wednesday in-season; each run
appends 523 rows dated `data_run`, so a player accumulates a time-series.

This file is **tracked by git** (unlike the rest of `data/`). A past Wednesday
cannot be regenerated — the news has moved on — so it is the historical record.

| col | dtype | notes |
|---|---|---|
| `data_run` | str | `yyyy-mm-dd`, the run that produced the row |
| `giorni_lookback` | int | days of news the query covered (default 14) |
| `stagione`, `id`, `nome`, `squadra`, `ruolo` | | joins to every other file on `id` + `stagione`; `ruolo` is the **Classic** role |
| `ruoli_mantra` | str | the frozen late-July Mantra tag, copied from `quotazioni_mantra.csv` |
| `ruolo_campo` | str | Mantra codes he is **actually** being played in, per this run's coverage. Uppercase, `;`-joined, sorted. Empty = the sources said nothing about his position |
| `deriva_ruolo` | float | 0..1. `0.0` if `ruolo_campo` ⊆ `ruoli_mantra` or empty, else `confidenza`. **>0 means the platform's tag is stale** |
| `sentiment`, `mercato`, `forma` | float | −1..+1 |
| `disponibilita`, `titolarita`, `rigorista`, `piazzati`, `confidenza` | float | 0..1 |
| `riassunto` | str | Italian, ≤600 chars, facts with dates |
| `n_fonti`, `fonti` | int / str | count and `;`-joined URLs actually read |
| `modello` | str | model id, so a mid-season model change is visible in the data |

**Why `ruolo_campo` exists.** `rules/sistema-mantra.md`: fantacalcio.it assigns
Mantra roles in late July and does not revisit them for the rest of the season,
and admits a player's tactical role can evolve without the tag following. So
`quotazioni_mantra.csv` drifts by design and never self-corrects — this is the
only column that can tell you it has.

## Gotchas

- **Comma-decimal columns**: `media_voto`, `media_fantavoto`,
  `voto_*`/`fantavoto_*` are strings using Italian `,` decimal separators
  — not numeric dtype. Convert before math.
- **`id` nulls**: only in `voti.csv`/`bonus_malus.csv`, always
  `ruolo == "Allenatore"` (coach) rows — filter these out for player-level
  analysis (`ruolo_codice != "all"`).
- **classic vs mantra**: identical row counts/keys per pair; differ only in
  role granularity. Pick one per analysis, don't merge both (duplicate
  data).
- **Season coverage differs by file**: `quotazioni_*` goes through
  `2026/27` (preseason prices already published); `statistiche_*`/`voti.csv`/
  `bonus_malus.csv` stop at `2025/26` (season not played yet);
  `player_sentiment_2026-27.csv` is `2026/27`-only.
- **`confidenza == 0` is not a neutral player.** It means no coverage was found.
  Exclude those rows before averaging — a 0.0 sentiment from silence and a 0.0
  from balanced news are different facts.
- **Decimal separator**: `player_sentiment_*.csv` uses `.` decimals, unlike the
  scraped files above. It is a new file; the comma-decimals were not worth
  propagating.
