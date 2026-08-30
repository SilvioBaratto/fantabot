# data/

**The database is the source of truth, and now the only copy.** The ten scraped
CSVs that seeded it were removed on 2026-08-28, once each one was verified row
for row against the table it had filled. What remains here is the two Mantra
reference files, which have no table and are still read from disk, and
`aste_live/`, the auction landing zone.

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
docker compose up -d          # Postgres on 54321, Adminer on 18082
alembic upgrade head
fantabot db-check             # health, row counts, sizes
```

## What is still on disk

| path | why it stays |
|---|---|
| `mantra_schemi.json` | the Mantra engine's input; no table models an 11-schema grid |
| `mantra_compat.json` | the full out-of-position matrix, 1,452 cells; same reason |
| `aste_live/` | the auction landing zone — **the durable record**, and Postgres is derived from it |

## Tables

`classic` = 3-role scoring (P/D/C/A), `mantra` = multi-role scoring on the same
match data. Both share a table wherever the grain is identical; the `listone`
column tells them apart.

| table | grain | rows at seed | source |
|---|---|---|---|
| `players` | one footballer | 1,474 | union of every id source |
| `teams` | one club per season | 100 | derived, gated |
| `quotazioni` | player × season × listone | 6,402 | `scripts/scrape_quotazioni.py` |
| `statistiche` | player × season × listone × fonte | 16,068 | `scripts/scrape_statistiche.py` |
| `qi_bias` | player × season × listone | 5,356 | derived from `quotazioni`; becomes a view |
| `target_price` | player × season × listone | 1,046 → 1,088 | `scripts/target_price.py` |
| `voti` | player × matchday | 50,634 | `scripts/scrape_voti.py` |
| `bonus_malus` | player × matchday | 50,634 | `scripts/scrape_voti.py` |
| `player_sentiment` | player × run day | 0 | `fantabot news-fetch --write` |
| `bot_state` | one lega | 0 | `lineup.py` |
| `auction_bids` | one bid | 0 | `auction.py` |
| `league_snapshot`, `league_team_snapshot`, `league_player_pool` | point in time | 0 | not yet produced — SPEC open question 5 |

Row counts are what the CSVs held. They are **floors, not fixtures**: the
scrapers read the live site and it moves.

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

### `voti` / `bonus_malus` — `squadra_raw` is corrupt

⚠️ The scraper labels **every row in a match block with the fixture's home
team**, so the column cannot say which side a player played for. Nothing keys,
indexes or joins on it, and a test enforces that. The full statement lives on
`db/models/matches.py`; the analysis script that measured it in 2026 has since
been deleted, so the finding was moved into the code rather than left as a
citation.

What does survive is the fixture: `squadra_raw` and `avversario_raw` identify
home and away correctly, and the two goal columns are that fixture's score. A
player's real club for a season comes from `quotazioni`.

3,039 rows per file are coach (`Allenatore`) rows with no player id. Postgres
forbids a nullable column in a primary key, and those rows would collide with
each other anyway, so each table has a surrogate key plus two disjoint partial
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
decimal is `3846`. So there are two — `italian_decimal` and `plain_decimal` —
and each refuses the other's format.

### `league_tokens`

One row per lega: the `apileague.fantacalcio.it` bearer token, encrypted with
`FANTABOT_ENCRYPTION_KEY` (Fernet), keyed by `l_id`.

| Column | Type | Note |
|---|---|---|
| `league_id` | `bigint` PK | The `l_id` claim. Same key as `bot_state`. |
| `ciphertext` | `bytea` | The Fernet token. The only place the JWT exists. |
| `key_fingerprint` | `varchar(16)` | `sha256(key)[:8]`, of the *key*. Turns a wrong-key failure into a sentence naming both keys. |
| `issued_at` / `expires_at` | `timestamptz` | The `iat` / `exp` claims. **Plaintext by design** — `token-status` must answer "is it expired" when the key is missing, and `auth_headers` must refuse before opening a socket. |
| `user_id` / `team_id` | `bigint` | The `user_id` / `t_id` claims. |
| `league_name` | `text` | Display only. Never keyed or joined on. |
| `captured_at` | `timestamptz` | When `login` wrote it. |
| `last_seen_at` | `timestamptz` | Last login at which this lega appeared in `leagues[]`. A row behind the newest stamp is `ORPHANED`. |
| `last_verified_at` | `timestamptz` NULL | Last `200` from the API. NULL = never confirmed. |

Written by `fantabot login`, read through `TokenStore`, inspected with
`fantabot token-status`, removed with `fantabot token-forget --league <id>`.
Replaced rather than versioned: a superseded token is a live credential until
its `exp`.

## Still read from disk

- `mantra_schemi.json`, `mantra_compat.json` — the 11 Mantra schemas and the
  out-of-position matrix, collected once by `fantabot mantra-grid` and verified
  by hand. Tracked in git.
- `storage_state.json` — Playwright's cookies. **Opt-in and usually absent**:
  `fantabot login` writes it only under `--save-session`, because as of
  2026-08-26 no working code path reads it. Git-ignored either way.

  It no longer holds the bearer token. That moved to `league_tokens`, encrypted
  — see below.

## What the migration found

Two things that were invisible while the data lived in files:

- **The 2026/27 listone grew from 523 players to 544** on 2026-08-26, when the
  first database-backed scrape picked up 21 signings added since the CSVs were
  captured on 2026-08-19 — Elmas to Atalanta, Badiashile to Napoli, Grabara to
  Juventus among them. Nothing was dropped, and re-running `target_price.py`
  priced all 544 — the 21 newcomers included.
- **`voti.csv` has no blank cells at all**, in any of its six grade columns
  across 50,634 rows. The blanks are in `target_price`. Earlier notes described
  the opposite.

## Historical: resolved open questions

- **Open question 2** — this file is a table dictionary now, not a CSV one.
- **Open question 3** — `voti.squadra_raw` is stored corrupt-but-labelled rather
  than repaired at import. Repairing it would hide a scraper bug that is still
  live.
- **Open question 4** — `docs/fantalab/`'s asta price model is out of scope for
  this phase. `target_price` and `auction_bids` are built to SPEC's Schema, and
  may be reshaped when that model lands.
