# data/

**The database is the source of truth for the scraped data, and now the only
copy of it.** The ten scraped CSVs that seeded it were removed on 2026-08-28,
once each one was verified row for row against the table it had filled.

**The two Mantra reference files are no longer here either.** They moved to
`src/fantabot/data/` and are **package data**, reached through
`src/fantabot/domain/shared/resources.py` (`importlib.resources`) rather than
from a path relative to the working directory — see *Package data, not here*
below.

**This directory is still live, though.** `fantabot_data_dir` defaults to
`./data` in `src/fantabot/config.py`, so what is left is runtime state the app
and the CLI both address: `room_journal.jsonl`, the live room's only record
(`config.journal_path()`, written by `asta room`/`asta bid`, paged by the app's
`GET /asta/journal`); `storage_state.json` when `auth login --save-session`
writes one; and `aste_live/`, the auction landing zone.

Every CSV was checked two ways before deletion — a row count against its table,
and **key-level containment**: every `(stagione, player_id, listone)` in the file
present in the database. `voti.csv` and `bonus_malus.csv` needed a third look,
because a key check on `player_id` reported 3,039 rows missing. Those are coach
rows, which carry no player id and are stored with `player_id` NULL; both tables
hold 50,634 rows, exactly the file totals. Nothing was dropped.

`fantabot db-import` and the eleven importers behind it were **removed on
2026-08-30**. They read CSVs that are not on disk and are not in git, so
`db-import --all` already reported `missing <file> — skipped` for every table and
wrote nothing. To re-seed from scratch, run the scrapers — they read the live
site, so the counts below are floors from the capture day, not fixtures.

```bash
fantabot-app db start         # the bundled Postgres at ~/.fantabot/pgdata
alembic upgrade head
fantabot db check             # health, row counts, sizes
```

## What is still on disk

| path | why it stays |
|---|---|
| `aste_live/` | the auction landing zone — **the durable record**, and Postgres is derived from it |
| `room_journal.jsonl` | the live room's own record, one line per cycle; `config.journal_path()` |
| `storage_state.json` | Playwright's cookies, opt-in and usually absent — see *Still read from disk* |

`aste_live/` is here by an **exported** `FANTABOT_HARVEST_DIR`, not by default.
`config.harvest_dir()` is `~/.fantabot/aste_live` when nothing overrides, and the
root `CLAUDE.md` ("One harvest home, and it is derived") is where that rule is
written. The directory this README sits beside is what the variable happens to
point at on the machine that collected the 2026-08/09 evenings.

## Package data, not here

`mantra_schemi.json`, `mantra_compat.json` and `mantra_starts_order.json` live at
`src/fantabot/data/`. They are the matcher's and the submitter's inputs — code in
every sense that matters — and they are read through
`src/fantabot/domain/shared/resources.py`, which resolves them with
`importlib.resources` instead of a path relative to the working directory. Read
from `./data` they were correct only when the process had been started from the
repository root, and a non-editable `pip install` had no `data/` at all.

## Tables

`classic` = 3-role scoring (P/D/C/A), `mantra` = multi-role scoring on the same
match data. Both share a table wherever the grain is identical; the `listone`
column tells them apart.

| table | grain | rows at seed | source |
|---|---|---|---|
| `players` | one footballer | 1,474 | union of every id source |
| `teams` | one club per season | 100 | derived, gated |
| `quotazioni` | player × season × listone | 6,402 | `fantabot db scrape quotazioni` |
| `statistiche` | player × season × listone × fonte | 16,068 | `fantabot db scrape statistiche` |
| `qi_bias` | player × season × listone | 5,356 | **a view** over `quotazioni` (migration `a1c4e77b3f01`) |
| `target_price` | player × season × listone | 1,046 → 1,088 | `fantabot db price` |
| `match_grain` | player × matchday | 50,634 | `fantabot db scrape voti` |
| `player_sentiment` | player × run day | 0 | `fantabot news fetch --write` |
| `league_snapshot`, `league_team_snapshot`, `league_player_pool` | point in time | 0 | `fantabot lega sync --write` |

Row counts are what the CSVs held. They are **floors, not fixtures**: the
scrapers read the live site and it moves.

**Two tables in the seed are gone.** `bot_state` and `auction_bids` were dropped
by `alembic/versions/1942efd6a2dc_drop_bot_state_and_auction_bids.py`, both at
zero rows; their stated sources, `lineup.py` and `auction.py`, were the W2
scaffolding and were deleted with them. The live room's record is
`room_journal.jsonl` (above), not a table.

**And the dictionary above is the seed's, not the whole schema.** Added since:
`league_competition`, `league_fixture`, `league_custom_role` (with the three
snapshot tables, `lega sync --write`); `asta`, `asta_event`, `asta_assignment`
(`harvest load` / `harvest backfill`); `player_exclusion` (`db exclude`);
`fantalab_session` (`auth fantalab-login`). `alembic/versions/` is the record;
`fantabot db check` prints the live counts.

### `players` — 1,474, not 1,414

Seeded from the **union** of every id source, not from `quotazioni` alone.
`quotazioni` knows 1,414 ids; `voti`/`bonus_malus` reference 60 more — players
who appeared in a match but never got a quotazione, from short loans and
mid-season transfers away. Seeding from `quotazioni` looks correct until `voti`
loads and 88 rows per file violate the foreign key.

94 ids are spelled more than one way across seasons (`SORIANO`/`Soriano`,
`Lucumi'`/`Lucumì`). The most recent season wins, ties break toward
`quotazioni`.

### `teams` — the bridge between two vocabularies

`quotazioni`, `statistiche`, `qi_bias` and `target_price` identify a club by a
three-letter code; `voti` and `bonus_malus` use the full name. Nothing in the
data states the correspondence, so it is derived — the code is the name's first
three letters, upper-cased — and then **gated**: a prefix collision or an
unresolved code raises and nothing is written. A partial mapping is the worse
failure, because it makes later joins return zero rows while every table still
looks populated.

Season-scoped, not global: 27 distinct clubs across five seasons, 20 in any one.

### `statistiche` — `media_voto` is nullable and that is the point

The source writes `"0,0"` for a player it has no average for. That is absent,
not a grade of zero, and 2,846 rows carry it. Stored as 0 they would drag every
average computed from this table toward zero and nothing would look wrong. The
counter columns are the opposite case and are NOT NULL.

### `match_grain` — `squadra_raw` is corrupt

The two tables this section was written about, `voti` and `bonus_malus`, are
**one table** since 2026-08-30:
`alembic/versions/b7d2f5a91c34_merge_voti_and_bonus_malus.py` merged them into
`match_grain` on the natural key `(stagione, giornata, nome)` — 50,634 rows each,
matching 50,634 for 50,634 with no orphans either way, and the six shared
descriptor columns disagreeing on zero rows. Everything below is about the merged
table; the two names survive only where a *file* or a *command* still carries them
(`voti.csv`, `fantabot db scrape voti`).

⚠️ The scraper labels **every row in a match block with the fixture's home
team**, so the column cannot say which side a player played for. Nothing keys,
indexes or joins on it, and a test enforces that. The full statement lives on
`src/fantabot/adapters/persistence/models/matches.py`; the analysis script that
measured it in 2026 has since been deleted, so the finding was moved into the code
rather than left as a citation.

What does survive is the fixture: `squadra_raw` and `avversario_raw` identify
home and away correctly, and the two goal columns are that fixture's score. A
player's real club for a season comes from `quotazioni`.

3,039 rows per file are coach (`Allenatore`) rows with no player id. Postgres
forbids a nullable column in a primary key, and those rows would collide with
each other anyway, so the table has a surrogate key plus two disjoint partial
unique indexes — one for rows with a player, one for rows without.

### `target_price` — the season the CSV never had

`stagione` does not exist in `target_price_2026_27_*.csv`; it lived in the
filename. It is a real NOT NULL column here, which is what lets a second
season's prices coexist with this one.

`prior_media_fantavoto` and `predicted_pct_delta` are nullable — 160 and 363
rows per listone have nothing to reason from. Unlike `statistiche`, this file
marks absence with a blank, so **zero is a real prediction**: one player
genuinely forecasts `+0.0`.

## Decimal separators are not consistent

Measured, not assumed:

| file | comma-decimals | dot-decimals | absent marked as |
|---|---|---|---|
| `statistiche_*.csv` | 13,222 | 0 | `"0,0"` |
| `voti.csv` | 102,100 | 0 | — |
| `qi_bias_*.csv` | 0 | all | — |
| `target_price_*.csv` | 0 | all | `""` |

One parser would have to guess, and guessing wrong does not raise: `"38.46"`
with commas swapped for dots is still `38.46`, and `"38,46"` read as a plain
decimal is `3846`. There were two — `italian_decimal` and `plain_decimal` —
and each refuses the other's format.

### `league_tokens`

One row per lega: the `apileague.fantacalcio.it` bearer token, encrypted with
`FANTABOT_ENCRYPTION_KEY` (Fernet), keyed by `l_id`.

| Column | Type | Note |
|---|---|---|
| `league_id` | `bigint` PK | The `l_id` claim, and the key every `league_*` table is joined on. |
| `ciphertext` | `bytea` | The Fernet token. The only place the JWT exists. |
| `key_fingerprint` | `varchar(16)` | `sha256(key)[:8]`, of the *key*. Turns a wrong-key failure into a sentence naming both keys. |
| `issued_at` / `expires_at` | `timestamptz` | The `iat` / `exp` claims. **Plaintext by design** — `token-status` must answer "is it expired" when the key is missing, and `auth_headers` must refuse before opening a socket. |
| `user_id` / `team_id` | `bigint` | The `user_id` / `t_id` claims. |
| `league_name` | `text` | Display only. Never keyed or joined on. |
| `captured_at` | `timestamptz` | When `login` wrote it. |
| `last_seen_at` | `timestamptz` | Last login at which this lega appeared in `leagues[]`. A row behind the newest stamp is `ORPHANED`. |
| `last_verified_at` | `timestamptz` NULL | Last `200` from the API. NULL = never confirmed. |

Written by `fantabot auth login`, read through `TokenStore`, inspected with
`fantabot auth status`, removed with `fantabot auth forget --league <id>`.
Replaced rather than versioned: a superseded token is a live credential until
its `exp`.

## Still read from disk

- `mantra_schemi.json`, `mantra_compat.json`, `mantra_starts_order.json` — the 11
  Mantra schemas, the out-of-position matrix and the platform's positional
  `starts[]` order. Collected once by `fantabot mantra-grid` and verified by hand,
  tracked in git — but **not from this directory**: they ship as package data at
  `src/fantabot/data/`. See *Package data, not here* above.
- `storage_state.json` — Playwright's cookies, under `fantabot_data_dir` and so
  under `data/` by default. **Opt-in and usually absent**: `fantabot auth login`
  writes it only under `--save-session`, because as of 2026-08-26 no working code
  path reads it. Git-ignored either way.

  It no longer holds the bearer token. That moved to `league_tokens`, encrypted
  — see the section above.

## What the migration found

Two things that were invisible while the data lived in files:

- **The 2026/27 listone grew from 523 players to 544** on 2026-08-26, when the
  first database-backed scrape picked up 21 signings added since the CSVs were
  captured on 2026-08-19 — Elmas to Atalanta, Badiashile to Napoli, Grabara to
  Juventus among them. Nothing was dropped, and re-running `target_price.py`
  priced all 544 — the 21 newcomers included. (That script is gone; the pricing
  is `fantabot db price` over `application/pricing.py` now.)
- **`voti.csv` has no blank cells at all**, in any of its six grade columns
  across 50,634 rows. The blanks are in `target_price`. Earlier notes described
  the opposite.

## Historical: resolved open questions

- **Open question 2** — this file is a table dictionary now, not a CSV one.
- **Open question 3** — `voti.squadra_raw` — `match_grain.squadra_raw` since the
  merge — is stored corrupt-but-labelled rather than repaired at import.
  Repairing it would hide a scraper bug that is still live.
- **Open question 4** — `docs/fantalab/`'s asta price model is out of scope for
  this phase. `target_price` and `auction_bids` were built to SPEC's Schema, and
  may be reshaped when that model lands. `auction_bids` has since been dropped
  (above); the asta's own tables are `asta`/`asta_event`/`asta_assignment`.
