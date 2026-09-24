# Spec: Agent-driven player news sentiment

Status: **draft, awaiting approval** · Phase 1 (Specify) of spec-driven development.
Supersedes: `scripts/scrape_player_news.py` + `data/player_news_2026-27.csv`.

**Decisions taken** (2026-08-21, in conversation — these are settled, not open):
league system is **both Classic and Mantra** (two leagues) · scope is **`pool` only**,
`roster` dropped · old artefacts **deleted** · sources **preferred, not exclusive** ·
lookback **14 days** · `rigorista` + `piazzati` **in** the schema · rate limits **backed
off and continued**, not failed fast · the Mantra schema grid is collected by a **second
one-off command reusing this spec's SDK plumbing**.

---

## Assumptions I'm making

Correct any of these now or the plan proceeds on them.

1. **The old artefact is deleted, not migrated.** `scripts/scrape_player_news.py`,
   `data/player_news_2026-27.csv` and `data/scrape_news.log` are removed in the same
   change. Nothing reads them today (`target_price.py` and `src/fantabot/` never
   mention them), so there is no consumer to break.
2. **New file, new name.** The grain changes from one-row-per-article to
   one-row-per-player-per-run, so the output is `data/player_sentiment_2026-27.csv`.
   Reusing the old filename with incompatible columns would be a trap.
3. **Auth is the Claude Code OAuth subscription**, exactly as `optimizer-theory` does
   it — no `ANTHROPIC_API_KEY` anywhere, in `os.environ` *or* in `ClaudeAgentOptions.env`.
   Not the `code_generator` API-key path.
4. **Model is `claude-sonnet-5`**, pinned in config, overridable per-run by flag.
5. **Player universe comes from `data/quotazioni_classic.csv`** filtered to
   `stagione == "2026/27"` (523 players), joined to `data/quotazioni_mantra.csv` on
   `id` + `stagione` for the Mantra tag. Both files carry the same 523 players for
   2026/27 — a count mismatch is a broken join, not a data gap.
6. **Prose is Italian, code/docstrings are English** — matches the existing scripts and
   the sibling `optimizer-theory` convention. Summaries and CSV column names are Italian
   because the sources and the rest of `data/` are.
7. **This spec produces data only.** Wiring sentiment into `strategy.py`
   (`pick_starting_lineup`, `decide_bid`) and `scripts/target_price.py` is a follow-up
   phase, out of scope here beyond a read-side adapter that exposes the CSV.
8. **You play two leagues, one Classic and one Mantra.** One news CSV serves both —
   the player pool is identical (the same 523 Serie A players), and the file carries
   the Classic `ruolo` *and* the Mantra `ruoli_mantra` side by side. There is no
   `--system` flag on `news-fetch` and there does not need to be one; `--system` stays
   an axis on the *consumers* (`target_price.py` already has it) and on the Mantra
   lineup engine.
9. **`data/player_sentiment_2026-27.csv` is committed to git.** It is small
   (523 rows/week × ~35 weeks) and it is the historical record — regenerating a past
   Wednesday is impossible, the news has moved on.

---

## Objective

Replace hand-written HTML scraping of `fantacalcio.it/ricerca` with a Claude Agent SDK
pipeline that, for each player, searches and reads the live web and returns a
**structured sentiment record** — an overall score, four fantacalcio-specific
sub-signals, and an Italian summary.

Run weekly (Wednesday) through the season, appending to one CSV, so each player
accumulates a **sentiment time-series** rather than a snapshot.

The same query also collects the one **Mantra** statistic that no file in `data/` can
ever hold: which role the player is *actually* being played in. fantacalcio.it freezes
Mantra role tags in late July and never revisits them for the rest of the season
(`rules/sistema-mantra.md`), so `quotazioni_mantra.csv` drifts from reality by design.
See "Mantra: the statistics we do not have".

**Why replace it:** the current scraper is two bespoke `HTMLParser` subclasses keyed to
`article-card` / `article-body` CSS classes on one site, with a hard-coded July–August
2026 date filter. It breaks on any markup change, it only sees fantacalcio.it, and its
output is 7,000 lines of raw article prose that no code consumes. An agent with
`WebSearch` + `WebFetch` reads any source and returns numbers the strategy can use.

**Users:** the bot itself (`auction.py` for both aste, `lineup.py` weekly), and the
human reading the CSV to sanity-check a bid.

**Success looks like:** on a Wednesday morning a cron job produces 523 validated rows
with no human present, and `decide_bid` can ask "is this player's `disponibilita` below
0.3?" instead of grepping headlines.

### Acceptance criteria

- `fantabot news-fetch --write` produces `data/player_sentiment_2026-27.csv`
  with one row per 2026/27 quotato player, dated today.
- Every numeric field is inside its declared range; no row is written from unvalidated
  model prose.
- A second run the same day is a no-op (resume by `(data_run, id)`); `--force` redoes it.
- A run interrupted at player 200 of 523, restarted, fetches only the remaining 323.
- One player's failure never aborts the run — it is logged and retried on the next run.
- No `ANTHROPIC_API_KEY` reaches the SDK by either channel; a run proves this before
  the first query.
- `pytest` passes with **zero live agent calls**.

---

## Tech Stack

| Piece | Choice | Note |
|---|---|---|
| Language | Python ≥3.11 | unchanged |
| Agent | `claude-agent-sdk>=0.2.143` | already declared in `pyproject.toml`; **not installed in `.venv`** (global is 0.2.142) — `pip install -e ".[dev]"` is a prerequisite |
| Model | `claude-sonnet-5` | user's choice; `fallback_model` unset |
| Tools granted | `WebSearch`, `WebFetch` | `WebFetch` alone cannot discover — it needs a URL |
| Structured output | `ClaudeAgentOptions.output_format={"type":"json_schema","schema":...}` | confirmed present in the installed SDK's option fields |
| Validation | `pydantic>=2.7` | already a dependency |
| CLI | `typer>=0.12` | already a dependency |
| Concurrency | `asyncio` + `Semaphore` | stdlib; no new dependency |
| Storage | `csv` stdlib, append mode | no pandas dependency added |

**No new third-party dependency.** Everything above is already in `pyproject.toml`.

---

## Commands

```bash
# prerequisite — the SDK is declared but not installed in .venv
pip install -e ".[dev]"

# smoke test: 5 players, print records, write nothing
fantabot news-fetch --limit 5

# one named player, full output, no write
fantabot news-fetch --only "Lautaro Martinez"

# the real weekly run — all 523, serves both leagues
fantabot news-fetch --write --concurrency 4

# redo today even though rows exist
fantabot news-fetch --write --force

# widen the window after a break (default 14)
fantabot news-fetch --write --lookback-days 21

# inspect what would be asked, spend nothing
fantabot news-fetch --limit 3 --print-prompt --no-run

# ONE-OFF, not on cron: collect the Mantra schema grid + compatibility matrix
fantabot mantra-grid --write

# gates
pytest
pytest tests/test_news_store.py::test_resume_skips_existing_rows
ruff check src tests scripts
ruff format src tests
mypy
```

### Cron

```cron
# Wednesday 09:00 in-season — always the full 523-player pool, both leagues
0 9 * * 3 cd /Volumes/External\ SSD/fantabot && .venv/bin/fantabot news-fetch --write >> data/news_cron.log 2>&1
```

`mantra-grid` is deliberately **not** on cron: the 11 schemas and the compatibility
matrix are fixed rules, not moving data. Run it once, verify by hand, commit the JSON.

### Flags

| Flag | Default | Meaning |
|---|---|---|
| `--write` | **off** | Gates *writing*, not *querying*. A run without it still spends subscription turns and discards the rows — same convention as `optimizer-theory`. |
| `--force` | off | Ignore resume; re-query players that already have today's row |
| `--limit N` | none | Stop after N players (smoke tests) |
| `--only NAME` | none | One player by `nome`, substring match |
| `--concurrency N` | `4` | Parallel agent queries |
| `--model ID` | `claude-sonnet-5` | Override the pinned model |
| `--season` | `2026/27` | Which `stagione` to filter the quotazioni by |
| `--lookback-days N` | `14` | Window the prompt asks the agent to cover; recorded per row in `giorni_lookback` |
| `--print-prompt` / `--no-run` | off | Show the built prompt / skip querying |

`FANTABOT_AUTO_ACT` does **not** gate this command. That flag guards writes to
`leghe.fantacalcio.it` (submit lineup, place bid). This writes a local CSV and touches
no league account.

---

## Scope policy — which players

**Always the full pool: all 523 quotati for the season.** There is no `--scope` flag.

You play two leagues, one Classic and one Mantra, and both draw from the same 523 Serie A
players. The aste — iniziale and riparazione alike — need the whole pool by definition,
and `target_price.py` values the whole pool. Restricting to a roster would save turns on
the one week it applies and cost coverage on every other.

`--scope roster` is **explicitly dropped from this spec**, not deferred-and-half-built.
It would need roster reading, which does not exist (`lineup.scrape_roster` is an
`NotImplementedError` stub; `docs/leghe-api.md` documents confirmed
`apileague.fantacalcio.it` read endpoints that could supply it), and with two leagues it
would also need a `--league` selector to mean anything. Both belong to the league-API
work, not here. If the flag is passed anyway the command **errors with that
explanation** rather than silently falling back.

### Runtime and rate limits

523 players × one query each, `--concurrency 4`. On a `RateLimitEvent` with
`status != "allowed"` the pipeline **backs off and continues** — it does not fail the
run. A Wednesday run therefore may take hours, which is fine: the lineup deadline is the
weekend, and resume-by-`(data_run, id)` makes a kill and restart free. Chosen over
fail-fast because a partial Wednesday is worth more than no Wednesday.

---

## Mantra: the statistics we do not have

`rules/sistema-mantra.md` describes a game this codebase cannot currently play. Mantra
is not Classic with finer labels on the same engine — it is a different lineup engine,
and only part of the data it needs is on disk.

**The structural fact first:** `models.py` defines `Role` as a 4-value StrEnum (`P/D/C/A`)
and `VALID_FORMATIONS` as 7 Classic `(D, C, A)` tuples. `strategy.pick_starting_lineup`
is built on both. Mantra has **12 role codes** across **11 schemas** laid out on **four**
lines (defense / midfield / trequarti / attack), not three. `scripts/target_price.py`
does accept `--system mantra`, but only by collapsing Mantra's compound codes into the
4 Classic buckets (its own comment: *"mantra: compound code, first component mapped
below"*). So today's Mantra support is "pretend it is Classic."

### What Mantra requires, and where we stand

| # | Requirement (`rules/`) | Status | Source |
|---|---|---|---|
| 1 | **12 role codes** Por, Dc, B, Dd, Ds, E, M, C, T, W, A, Pc; players may be polivalente | ✅ **have** | `quotazioni_mantra.csv`: 523 players for 2026/27 across **24 combos** (`DC` 71, `M;C` 66, `POR` 66, `PC` 58, `C;T` 31, `DS;E` 31 …), uppercase, `;`-joined |
| 2 | **11 tactical schemas** — slot-by-slot role lists, 4 lines, exactly 5 defensive-profile (Dd, Ds, Dc, B, E, M) + 5 offensive-profile (C, T, W, A, Pc); some slots list two interchangeable roles | ❌ **missing entirely** | must be collected — no CSV, no code |
| 3 | **Per-formation out-of-position compatibility matrix** | ❌ **missing** | `sistema-mantra.md` says so in as many words: *"fantacalcio.it publishes a full per-formation compatibility table as a separate download that isn't captured here."* Prose gives the general blocked pairs (B/Dd/Ds→Dc, Dd↔Ds, E→M, M→E, W→T) and the one named exception (4-1-4-1: W↔T never, not even with malus) |
| 4 | **Substitution engine** BASIC/EASY/MASTER, combination search in bench order (`ABC, ABD, ABE, ACD…`), −1 malus tiers, GK first | ❌ **missing** | pure logic, no data to scrape. `leghe-private.md` recommends **MASTER** — where **bench order is the whole lever**, not bench composition |
| 5 | **D-Factor** — average vote of the 5 defensive-profile players, ≥3 of which must be Dc/B/Dd/Ds (E and M fill at most 2); 5+1 variant folds in the GK | ⚠️ **derivable, not derived** | join `voti.csv` → `quotazioni_mantra.csv` on `id`+`stagione`. **`voti.csv` carries `ruolo_codice`/`ruolo` in Classic P/D/C/A only** — the Mantra role has to come from the join |
| 6 | **R-Factor** — count of players with base vote ≥6 (mutually exclusive with D-Factor) | ⚠️ **derivable, not derived** | `voti.csv` `voto_*` columns |
| 7 | **Role drift** — the platform assigns Mantra roles **in late July and never revisits them all season**; `sistema-mantra.md` admits *"a team's or player's tactical role can evolve mid-season without the platform's role tag following along"* | ❌ **missing — and only the web knows** | **this pipeline** |
| 8 | **Position actually played, per match** | ❌ **missing** | no CSV has it; probable-lineup and match-report coverage does |
| 9 | **Real-world starter status** (drives SWITCH, which resolves *before* substitutions — `leghe-private.md`) | ❌ **missing** | `titolarita`, already in this spec's schema |
| 10 | **Assist quality tiers** soft/standard/gold | ❌ **missing** | `bonus_malus.csv` has a flat integer `assist`, no tier split. From 2026/27, newly created leagues default to soft=0.5 / standard=1 / gold=1 (`assist.md`) — so a flat count computes the wrong fantavoto |
| 11 | **Bonus/malus role grouping** — Mantra splits GK vs outfield only, not the 12 roles | ✅ **have** | `leghe-private.md`; simplifies scoring, nothing to collect |
| 12 | **Roster shape** — min 23 incl. 2 GK, **no per-role slot constraints**, 280 credits at 28 players | ✅ **have** | `leghe-private.md`. Note `strategy.allocate_auction_budget`'s 5/15/35/45 GK/DEF/MID/ATT split assumes Classic's four buckets and does not apply |

### Why item 7 is this spec's job and the rest is not

Items 2, 3 and 4 are **static rules artefacts** — a schema grid, a compatibility matrix,
an engine. They are collected once, verified by hand, and encoded as code and constants.
Items 5, 6 and 10 are **joins over CSVs we already have** (or, for 10, a scraper change).
None of them belong in a weekly news agent.

Item 7 is different, and it is the one Mantra statistic that **cannot** be derived from
any file in `data/`: the platform's role tag is frozen in late July by its own rules, so
`quotazioni_mantra.csv` is guaranteed to drift from reality as the season runs, and it
will never self-correct. A `W`-tagged player who has spent six weeks playing as a `T`
is still `W` in every CSV we own. Every lineup built from that tag is wrong, and
silently so.

That drift is **exactly** what weekly news coverage reports — probable lineups, match
reports, tactical previews. So the agent already fetching news for sentiment collects it
in the same query, at no extra cost.

### What this spec therefore adds

**In the weekly pipeline:**

1. Two new schema fields: `ruolo_campo` (Mantra codes the player is **actually** playing,
   per recent coverage) and the frozen tag `ruoli_mantra` carried in from
   `quotazioni_mantra.csv` for the join.
2. One derived column `deriva_ruolo`, computed **host-side** in `news/mantra.py` — pure,
   testable, no model judgement: `0.0` when `ruolo_campo ⊆ ruoli_mantra` or when nothing
   was observed, otherwise the model's own `confidenza`. The model reports what it sees;
   the host decides what that means.
3. The prompt carries the frozen tag and the 12-code legend, and asks explicitly whether
   the player is still playing there.

**As a second, one-off command — `fantabot mantra-grid`:**

Items 2 and 3 in the table above (the 11 schemas and the out-of-position matrix) are
collected by pointing the SDK plumbing this spec already builds at the rules pages
instead of at a player. Same `agentkit/` options builder, same env guard, same message
loop; a different prompt, a different schema, and a JSON writer instead of a CSV one.

```
fantabot mantra-grid --write
  -> data/mantra_schemi.json    11 schemas: name, 4 lines, slot-by-slot role lists,
                                 with the 5-defensive / 5-offensive invariant asserted
                                 host-side before anything is written
  -> data/mantra_compat.json    per-formation out-of-position matrix, plus the general
                                 blocked pairs and the named 4-1-4-1 W/T exception
```

It is **not on cron and not resumable** — it runs once, its output is verified by hand
against `rules/sistema-mantra.md`, and the JSON is committed. These are fixed rules, not
moving data.

**Host-side gates before either file is written** (a mis-transcribed matrix would produce
illegal lineups silently, all season):

- exactly 11 schemas;
- every schema fields exactly 10 outfield slots, split exactly 5 defensive-profile
  (Dd, Ds, Dc, B, E, M) and 5 offensive-profile (C, T, W, A, Pc);
- every role code in every slot is one of the 12 known codes;
- the compatibility matrix mentions all 11 schemas and encodes the 4-1-4-1 W/T exception.

A failed gate writes nothing and prints what was wrong. The agent may be re-run; the
constants may not be hand-patched past a gate.

### What this spec still does NOT do

The **Mantra lineup engine** itself — formation selection over the 11 schemas,
polivalente slot assignment, bench ordering for MASTER mode, and the BASIC/EASY/MASTER
substitution simulator with its combination search. That is a separate spec, and
`mantra-grid`'s two JSON files are its input. Items 5, 6 and 10 in the table (D-Factor,
R-Factor, assist tiers) also stay out: they are joins and scraper changes, not agent work.

Sequencing rationale: role drift has to be collected **from the first Wednesday** or the
history has a hole that cannot be backfilled. A schema grid collected in November is
exactly as good as one collected in August — but since it is nearly free once the
plumbing exists, it ships here anyway, so the engine spec starts unblocked.

### Doc bug found while reading

`rules/sistema-mantra.md` heads its role section **"Roles (11 codes)"** and then lists
**12** rows (Por, Dc, B, Dd, Ds, E, M, C, T, W, A, Pc) — and its own closing line
repeats all 12 (`por/dc/b/dd/ds/e/m/c/w/t/a/pc`). The count in the heading is wrong.
Fix the heading as part of this change; a "12 codes" constant in code that disagrees
with the rules doc it cites will waste someone's afternoon later.

---

## Project Structure

```
src/fantabot/
  agentkit/                 ← NEW: SDK plumbing, shared by both commands
    __init__.py
    env.py                  strip_dangerous_env() / assert_subscription_auth()
    runner.py               the ONE query() message loop -> Outcome[T]
    options.py              build_options(): model, tools, setting_sources, schema
  news/                     ← NEW: the weekly producer
    __init__.py             public surface: run_news_fetch()
    models.py               PlayerSentiment (pydantic) = the json_schema + NewsRow
    prompt.py               pure: Player + lookback -> prompt string (Italian)
    mantra.py               pure: Mantra role-code parsing + drift computation
    store.py                pure-ish: CSV header, row serialization, resume index
    pool.py                 the 523-player universe: quotazioni classic ⋈ mantra
    pipeline.py             fan-out, semaphore, rate-limit backoff, logging
  mantra_grid/              ← NEW: the one-off rules collector
    __init__.py             public surface: run_mantra_grid()
    models.py               Schema / Slot / CompatMatrix pydantic models
    prompt.py               pure: the two rules-page prompts
    gates.py                pure: the four host-side invariants, fail-closed
    writer.py               JSON writer
  data_sources/
    news_sentiment.py       ← NEW: the reader. Loads the CSV, exposes latest +
                              trailing-window sentiment and drift per player id.
  cli.py                    ← MODIFIED: + news_fetch, + mantra_grid

tests/
  test_agentkit_env.py      both leak vectors closed; assert raises when key present
  test_agentkit_runner.py   message loop against a FAKE result object (Protocol)
  test_news_models.py       schema ranges, coercion, rejection of out-of-range
  test_news_prompt.py       prompt is deterministic, carries name/team/role/tag/window
  test_news_mantra.py       code normalization, subset check, drift = 0 / = confidenza
  test_news_store.py        header, append, resume index, force, comma/quote escaping
  test_news_pool.py         523 classic ⋈ 523 mantra; a missing id is an error, not a null
  test_news_pipeline.py     fan-out, one-player failure is isolated, concurrency cap,
                            RateLimitEvent backs off instead of raising
  test_mantra_grid_gates.py 11 schemas, 5+5 split, known codes, 4-1-4-1 exception —
                            each invariant has a failing fixture that must be rejected

data/
  player_sentiment_2026-27.csv   ← NEW output (committed)
  mantra_schemi.json             ← NEW, one-off (committed)
  mantra_compat.json             ← NEW, one-off (committed)
  news_run.log                   ← per-run log (gitignored)

DELETED:
  scripts/scrape_player_news.py
  data/player_news_2026-27.csv
  data/scrape_news.log
```

**Why a package and not a `scripts/` file:** the other `scripts/*.py` are one-shot
preseason scrapes run by hand. This one runs on cron every week for a season, is
imported by `data_sources/`, and is under `mypy --strict` — that makes it library code.

**Why `agentkit/` is separate from `news/`:** two callers need the same options builder,
env guard and message loop, and `optimizer-theory` is explicit that the copy-the-loop
approach is what it had to undo — its `adapters/claude_sdk.py` docstring opens with
*"The one message loop. Replaces five copies of it."* One caller does not justify the
split; two do, and `mantra-grid` is the second on day one.

---

## Output format

`data/player_sentiment_2026-27.csv`, append-only, UTF-8, `,` separator, `.` decimals.

```csv
data_run,giorni_lookback,stagione,id,nome,squadra,ruolo,ruoli_mantra,ruolo_campo,deriva_ruolo,sentiment,disponibilita,titolarita,mercato,forma,rigorista,piazzati,confidenza,riassunto,n_fonti,fonti,modello
2026-08-26,14,2026/27,6916,Ahanor,ATA,Difensore,B;DS;E,,0.00,-0.40,0.20,0.30,-0.60,0.00,0.00,0.00,0.70,"Infortunio muscolare rimediato in amichevole, out 2-3 settimane. Il Chelsea continua a monitorarlo.",3,https://a;https://b;https://c,claude-sonnet-5
2026-09-02,14,2026/27,6916,Ahanor,ATA,Difensore,B;DS;E,DS,0.00,0.10,0.75,0.45,-0.30,0.00,0.00,0.00,0.60,"Rientrato in gruppo, convocabile per la 2a giornata. Interesse Chelsea raffreddato.",2,https://d;https://e,claude-sonnet-5
2026-10-07,14,2026/27,632,Zaccagni,LAZ,Centrocampista,W;A,T,0.85,0.30,0.90,0.85,0.00,0.20,0.95,0.85,"Sarri lo schiera stabilmente da trequartista centrale dalla 4a giornata: tre gare di fila dietro la punta. Resta il battitore designato di punizioni e corner.",4,https://f;https://g,claude-sonnet-5
```

| Column | Type | Range | Meaning |
|---|---|---|---|
| `data_run` | `str` | `yyyy-mm-dd` | the Wednesday this row was produced |
| `giorni_lookback` | int | | the window this row covers, in days. Recorded per row because it is tunable: change the default and old rows stay interpretable |
| `stagione`, `id`, `nome`, `squadra`, `ruolo` | | | joins to every other file in `data/` on `id` + `stagione`; `ruolo` is the **Classic** role, as in every other CSV |
| `ruoli_mantra` | str | | the frozen late-July Mantra tag, `;`-joined uppercase, copied from `quotazioni_mantra.csv`. Carried so a reader needs one file, not two |
| `ruolo_campo` | str | | Mantra codes the player is **actually** playing per this run's coverage, `;`-joined, **normalized to uppercase and sorted** so the cell is comparable to `ruoli_mantra` beside it (live runs return the prompt legend's casing, `B;Ds;E`). Empty = coverage said nothing about position |
| `deriva_ruolo` | float | 0..1 | computed host-side: `0.0` if `ruolo_campo ⊆ ruoli_mantra` or `ruolo_campo` empty, else `confidenza`. **>0 means the platform's tag is stale** and any Mantra lineup built from it is wrong. Note the subset rule bites on polivalenti: Ahanor is tagged `B;DS;E`, so observing `DS` is a *narrowing*, not drift — the narrowing is carried by `ruolo_campo` itself. Zaccagni tagged `W;A` and reported at `T` is drift, because `T` is in neither slot |
| `sentiment` | float | −1..+1 | overall fantacalcio outlook |
| `disponibilita` | float | 0..1 | fit, not suspended, available to play |
| `titolarita` | float | 0..1 | probability of starting |
| `mercato` | float | −1..+1 | −1 leaving/benched by an arrival, +1 arriving/role strengthened |
| `forma` | float | −1..+1 | recent form |
| `rigorista` | float | 0..1 | probability this is the club's designated penalty taker. Worth +3/−3 per event under the baseline bonus table — often a bigger auction signal than sentiment |
| `piazzati` | float | 0..1 | set-piece duty (corners, free kicks). Fuzzier than `rigorista`: often split between two players and rotated, so expect lower `confidenza` behind it |
| `confidenza` | float | 0..1 | evidence strength. **0.0 = no news found** — an honest gap, not a neutral player |
| `riassunto` | str | ≤600 chars | Italian, one paragraph, facts + dates only. Raised from 400 at Checkpoint C: a 9-player live sample came back at 336–399 chars with four at 380+, so 400 was the binding constraint on detail rather than a backstop |
| `n_fonti` | int | ≥0 | count of URLs actually fetched |
| `fonti` | str | | `;`-joined URLs (`;` matches `quotazioni_mantra.csv`'s multi-value convention) |
| `modello` | str | | model id, so a mid-season model change is visible in the data |

**Decimal separator is `.`, not `,`.** The existing scraped CSVs use Italian
comma-decimals — `data/README.md` lists that as a *gotcha* to work around, not a
convention to propagate. New file, clean floats.

**`confidenza == 0.0` is the no-news signal.** A player with no coverage gets a row (so
the time-series has no holes) with all scores at 0.0, `n_fonti=0`, and
`riassunto="Nessuna notizia rilevante nel periodo."` Downstream code must check
`confidenza` before trusting `sentiment` — a 0.0 sentiment from silence is not the same
as a 0.0 sentiment from balanced news.

---

## Sources and lookback

**Preferred, not exclusive.** The prompt names the sources worth trying first and then
gets out of the way — a club's own injury bulletin is routinely the primary source and
beats every aggregator, and the low-profile players where news matters most are exactly
the ones the big sites ignore.

```
Fonti preferite (non esclusive): fantacalcio.it, gazzetta.it,
tuttomercatoweb.com, il sito ufficiale del club.
Se tacciono, cerca altrove. Riporta in `fonti` solo gli URL letti davvero.
```

The `fonti` column records what was actually read, so source drift is visible in the
data rather than assumed away.

**Window: 14 days** (`--lookback-days`, recorded per row in `giorni_lookback`). Two full
match rounds in view, so a Wednesday run always sees the previous weekend plus any
midweek round.

The cost of 14 over 7 is that a stale story keeps re-scoring and the series lags reality.
Two mitigations, both in the prompt rather than in post-processing:

- **recency weighting** — the prompt states explicitly that items from the last 3 days
  outweigh items from 10 days ago, and that a story already resolved (injury healed,
  transfer window shut) must not keep depressing the score;
- **date discipline** — `riassunto` must carry dates, so a human reading a row can see at
  a glance whether it is describing this week or last.

Consecutive runs overlap by 7 days. That is correct, not double-counting: the row records
what was true *that Wednesday*, and a two-week injury genuinely was true on both.

---

## Code Style

Existing conventions: line length 100, `from __future__ import annotations`, module
docstring explaining *why*, frozen dataclasses for values, pure functions kept free of
I/O. Ruff `select = ["E","F","I","UP","B","SIM","RUF"]`, `mypy --strict` on `src`.

The schema is the contract — the model fills it, pydantic enforces it:

```python
# src/fantabot/news/models.py
class PlayerSentiment(BaseModel):
    """What one agent query must return. This IS the json_schema handed to the SDK.

    Field descriptions are prompt surface, not documentation: the model reads them.
    Ranges are enforced host-side too — a model that returns 1.4 is a failed query,
    not a clamped row, because a clamp hides a misread prompt.
    """

    model_config = ConfigDict(extra="forbid")

    sentiment: float = Field(ge=-1.0, le=1.0, description="Outlook fantacalcistico complessivo")
    disponibilita: float = Field(ge=0.0, le=1.0, description="0 = infortunato/squalificato, 1 = pienamente disponibile")
    titolarita: float = Field(ge=0.0, le=1.0, description="Probabilita di partire titolare")
    mercato: float = Field(ge=-1.0, le=1.0, description="-1 in uscita o oscurato da un acquisto, +1 in arrivo o ruolo rafforzato")
    forma: float = Field(ge=-1.0, le=1.0, description="Forma recente")
    rigorista: float = Field(ge=0.0, le=1.0, description="Probabilita che sia il rigorista designato della squadra")
    piazzati: float = Field(ge=0.0, le=1.0, description="Probabilita che batta calci piazzati (corner, punizioni)")
    confidenza: float = Field(ge=0.0, le=1.0, description="0 se nessuna notizia trovata")
    riassunto: str = Field(max_length=600, description="Un paragrafo in italiano, solo fatti con date")
    fonti: list[str] = Field(default_factory=list, description="URL effettivamente letti")

    # Mantra. The platform freezes role tags in late July and never revisits them
    # (rules/sistema-mantra.md), so quotazioni_mantra.csv drifts by design and no
    # file in data/ can correct it. The prompt hands the model the frozen tag; this
    # field is what recent coverage says the player is actually being played as.
    # Empty list is the honest answer for "coverage didn't mention his position" —
    # it is NOT the same as "he's still where the tag says", and the host-side drift
    # computation in news/mantra.py treats the two differently.
    ruolo_campo: list[str] = Field(
        default_factory=list,
        description="Codici ruolo Mantra effettivamente ricoperti nelle ultime partite, fra: Por Dc B Dd Ds E M C T W A Pc. Vuoto se le fonti non dicono nulla sulla posizione in campo.",
    )
```

The agent options, with every non-obvious choice justified in place:

```python
# src/fantabot/news/agent.py
def build_options(player: Player, model: str) -> ClaudeAgentOptions:
    return ClaudeAgentOptions(
        model=model,
        # Empty: the SDK reads ANTHROPIC_API_KEY from options.env as well as
        # os.environ (session_resume.py), so both channels must stay clean for the
        # CLI to fall back to the ~/.claude OAuth profile.
        env={},
        allowed_tools=["WebSearch", "WebFetch"],
        # A news query has no business editing files or spawning agents. Task is
        # named explicitly: an agent that delegates burns the concurrency budget
        # and returns prose instead of structured output.
        disallowed_tools=["Task", "Agent", "Bash", "Write", "Edit", "NotebookEdit"],
        # [] and not None: without this the SDK loads the repo's CLAUDE.md, its
        # hooks and its skills into every one of 523 queries. This agent needs to
        # know about one footballer, not about fantabot.
        setting_sources=[],
        output_format={"type": "json_schema", "schema": PlayerSentiment.model_json_schema()},
        max_turns=12,          # ~6 searches + fetches; a runaway player can't eat the run
        permission_mode="bypassPermissions",  # unattended cron, tools are read-only
    )
```

---

## Testing Strategy

**Framework:** pytest, already configured (`pythonpath = ["src"]`).

**Hard rule: the suite makes zero agent calls.** Same reason `strategy.py` is the only
tested module today — purity is what makes testing possible. Every module above is built
so its logic is reachable without a subprocess:

| Module | How it is tested |
|---|---|
| `models.py` | pydantic directly: valid record parses, `sentiment=1.4` raises, `extra` field raises, `riassunto` over the cap raises, and the cap keeps headroom over what the model actually writes |
| `prompt.py` | pure function — same player in, same string out; asserts name/team/role/date and the frozen Mantra tag are present |
| `mantra.py` | pure: `"DD;DC"` parses to `{DD, DC}`; lowercase/whitespace normalized; `ruolo_campo=[]` → drift 0.0; `["DD"] ⊆ {DD,DC}` → 0.0; `["T"] ⊄ {W}` → drift == `confidenza`; an unknown code raises rather than silently scoring 0 |
| `pool.py` | 523 classic rows ⋈ 523 mantra rows → 523 players; an `id` present in one file and not the other raises, it is never nulled |
| `mantra_grid/gates.py` | pure, one failing fixture per invariant: 10 schemas rejected, a schema with 6 defensive-profile slots rejected, an unknown role code rejected, a matrix missing the 4-1-4-1 W/T exception rejected. A gate that cannot fail is not a gate |
| `store.py` | tmp_path CSV: header written once, append preserves prior rows, resume index skips `(data_run, id)`, `--force` overrides, a `riassunto` containing `,` and `"` round-trips |
| `env.py` | monkeypatched `os.environ`: every var in `DANGEROUS_VARS` is cleared; `assert_subscription_auth` raises when a key is in `os.environ` **and** when it is in `options.env` |
| `agent.py` | a fake result object satisfying a `ResultLike` Protocol (the `optimizer-theory` pattern) — structural typing lets the fake and the real `ResultMessage` share one signature. Covers: valid structured output, missing structured output, schema-rejected output, error subtype |
| `pipeline.py` | fake runner: 10 players where #4 raises → 9 rows + 1 logged failure, run completes; semaphore never exceeds `--concurrency`; a `RateLimitEvent` with `status != "allowed"` backs off and continues rather than raising |
| `data_sources/news_sentiment.py` | fixture CSV: latest row per id, trailing-4-week mean, missing player returns `None`, `confidenza=0` rows excluded from the mean |

**Coverage expectation:** every module in `src/fantabot/news/` has a test file. No
percentage target — the target is that each of the failure modes above has a named test.

**What is deliberately NOT tested:** that the model returns *good* sentiment. That is a
judgement call verified by hand on a sample of ~10 players before the first full run, and
recorded in the PR, not asserted in CI.

---

## Boundaries

**Always do**
- Strip credentials from `os.environ` **and** leave `options.env` empty before any query.
- Validate every model response against `PlayerSentiment` before it reaches the CSV.
- Write a row for every player attempted, including no-news players (`confidenza=0.0`).
- Compute `deriva_ruolo` host-side from `ruolo_campo` vs `ruoli_mantra`. Never ask the
  model whether a role tag is stale — it does not know what tag we hold.
- Append, never rewrite: history is not reproducible.
- Log per-player failures and continue the run.
- Back off and continue on `RateLimitEvent`; never abort a Wednesday for it.
- Record `giorni_lookback` on every row, so a tuned window never makes old rows lie.
- Keep `prompt.py`, `models.py`, `store.py` free of SDK imports — same purity rule that
  makes `strategy.py` testable.
- Run `pytest` + `ruff check` + `mypy` before committing.

**Ask first**
- Adding any third-party dependency (the design needs none).
- Granting the agent a tool beyond `WebSearch`/`WebFetch`.
- Changing the CSV schema after the first real run — it invalidates existing history.
- Encoding the 11 Mantra schemas or the out-of-position matrix from memory. They are not
  in `rules/`; the compatibility table is explicitly a separate download. Collect and
  verify them, do not reconstruct them.
- Hand-patching `mantra_schemi.json` / `mantra_compat.json` past a failing gate. Re-run
  the collector or fix the gate — never edit the output to satisfy the check.
- Raising `--concurrency` above 4 (subscription rate limits).
- Running the full 523-player pool for the first time.

**Never do**
- Put `ANTHROPIC_API_KEY` (or Bedrock/Vertex/Foundry vars) anywhere near this pipeline.
- Clamp an out-of-range score into range — that hides a misread prompt.
- Let the agent write files, run shell commands, or spawn subagents.
- Invent a sentiment for a player with no news; `confidenza=0.0` is the honest answer.
- Treat an empty `ruolo_campo` as confirmation that the frozen tag is still correct. It
  means the sources were silent, nothing more.
- Build a Mantra lineup off `models.Role` or `VALID_FORMATIONS` — both are Classic-only.
  Mantra needs its own schema grid; `mantra-grid` collects it, the engine that consumes
  it is a separate spec.
- Put `mantra-grid` on cron. It collects fixed rules, and a silent weekly re-collection
  is how a mis-transcribed matrix would slip in unnoticed.
- Delete or rewrite past `data_run` rows.
- Flip `FANTABOT_AUTO_ACT` as part of this work — unrelated, and it guards the league account.

---

## Success Criteria

1. `scripts/scrape_player_news.py`, `data/player_news_2026-27.csv`, `data/scrape_news.log`
   are gone; nothing in the repo references them (`grep -r player_news` is empty except
   in this spec and git history).
2. `fantabot news-fetch --limit 5` returns 5 validated `PlayerSentiment`
   records and writes nothing.
3. `fantabot news-fetch --write` produces 523 rows dated today; re-running
   it the same day adds 0 rows; with `--force` it replaces them.
4. Killing the run at ~200 players and restarting completes the remaining ~323 only.
5. A test proves an `ANTHROPIC_API_KEY` in either channel raises before the first query.
6. `pytest` green, `ruff check src tests scripts` clean, `mypy` clean on `src/fantabot`,
   with no network access during the suite.
7. `data_sources/news_sentiment.py` returns the latest and trailing-4-week sentiment for
   a given player id from the CSV.
8. `data/README.md` documents the new file and drops the old one.
9. `ruoli_mantra` is populated for all 523 rows from `quotazioni_mantra.csv` (2026/27 has
   523 players across 24 role combos — the counts must match exactly; a mismatch means
   the join key is wrong).
10. `deriva_ruolo > 0` on at least one hand-checked player known to have changed position,
    and `0.0` on a player who has not — verified by hand on the first real run, not
    asserted in CI.
11. `rules/sistema-mantra.md`'s "Roles (11 codes)" heading is corrected to 12.
12. `fantabot news-fetch --scope roster` exits with an error naming the league-API work,
    not a silent fallback to the full pool.
13. `fantabot mantra-grid --write` produces `data/mantra_schemi.json` with 11 schemas,
    each 5 defensive-profile + 5 offensive-profile, and `data/mantra_compat.json`
    encoding the 4-1-4-1 W/T exception — all four gates passing, output spot-checked by
    hand against `rules/sistema-mantra.md` before commit.
14. Both commands share one `agentkit/` message loop; `grep -c "async for message"` over
    `src/fantabot` returns 1.

---

## Open Questions

Seven of the original ten are **settled** — see "Decisions taken" at the top of this
document. What remains:

1. **Rate-limit reality at 523 players.** Unknown until the first full run. The design
   backs off and continues, so the failure mode is a slow Wednesday, not a lost one — but
   if a run stretches past a working day, `--concurrency` and the pool size become worth
   revisiting. *No decision needed now; this is a thing to measure on run one.*
2. **When does sentiment reach `strategy.py`?** Out of scope here. The likely shape is
   `decide_bid` discounting `target_price` by `disponibilita` and lifting it by
   `rigorista`, and `pick_starting_lineup` weighting projected scores by `titolarita` —
   but that needs the aste tooling, and for the Mantra league it needs the lineup engine
   first.
3. **Assist tiers.** `bonus_malus.csv` has a flat integer `assist`. Under 2026/27 defaults
   for newly created leagues (soft=0.5 / standard=1 / gold=1, per `assist.md`) a flat
   count computes the wrong fantavoto for **both** your leagues. Fixing it is a
   `scrape_voti.py` change, not a news-agent change — separate task, but it is wrong
   right now and worth knowing.
4. **D-Factor / R-Factor, if the Mantra league enables either.** Both are derivable from a
   `voti.csv` ⋈ `quotazioni_mantra.csv` join that nobody has written. Which (if either)
   your league actually has configured decides whether that join is urgent or academic.

---

## Next phase

On approval this moves to **Phase 2 (Plan)** — `tasks/plan.md` and `tasks/todo.md`, per
the `/plan` convention — with the build order roughly: `agentkit/` → `news/` pure modules
→ `news/pipeline.py` → `mantra_grid/` → `data_sources/news_sentiment.py` → deletions →
`data/README.md`.
