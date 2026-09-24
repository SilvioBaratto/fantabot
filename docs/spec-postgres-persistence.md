# Spec: Postgres persistence layer

> **Complete and archived — 2026-08-26.** All 16 success criteria met; 51 tasks
> across 13 phases, see
> `../tasks/archive/postgres-persistence-plan.md`.
> Lived at `SPEC.md`
> until the next phase took that name. Its Open Question 1 — the bearer token, ruled on
> 2026-08-26 to live in Postgres encrypted — became the next phase's spec in full:
> `../tasks/archive/token-store-spec.md`. That link
> said `../SPEC.md` and therefore meant whichever phase held the name; by 2026-08-30 it was
> four phases wrong.

Phase 3 of fantabot. Replaces flat-file persistence with a dockerized Postgres
database, an Alembic migration chain, and a repository layer — following the
structure already proven in the sibling `optimizer/` project.

Previous phase: [`docs/spec-news-sentiment.md`](spec-news-sentiment.md)
(complete except the 523-player run).

---

## Assumptions I'm making

Correct any of these now — they shape everything below.

1. **Only the database is dockerized.** `fantabot` itself keeps running as a
   host-installed CLI under cron. No `api` container, no app image, no
   `entrypoint.sh`. `docker-compose.yml` holds exactly two services: `db` and
   `adminer`. This is what "not the full solution just the db" means.
2. **`optimizer/ingestion/` is the template, not `project-initializer`.**
   Both were surveyed. `project-initializer`'s FastAPI template assumes a
   request/response lifecycle (`get_db` dependency, session-per-request,
   `app/infrastructure/orm/`) that fantabot has no counterpart for. `optimizer`'s
   ingestion daemon is the true analogue — a CLI/cron process with no HTTP
   server, a `DatabaseManager` singleton, and `get_session()` as a context
   manager. Where the two disagree, `optimizer` wins.
3. **Sync SQLAlchemy 2.x + psycopg2.** Not async. fantabot is a batch process;
   `project-initializer`'s `--async-db` overlay ships no Alembic support anyway,
   and `postgresql+asyncpg://` breaks `alembic upgrade head`.
4. **The DB becomes the source of truth; the CSVs become a one-time seed.**
   Per your answer. After `fantabot db-import` runs once, all reads and writes go
   through SQLAlchemy. The CSVs stay on disk and in `.gitignore` as the
   historical seed, but nothing writes to them again.
5. **`scripts/*.py` scrapers are rewritten to write to the DB** as part of this
   phase — otherwise "DB is truth" is false the next time a scraper runs.
6. **`data/storage_state.json` does NOT go in the database — in this phase.**
   It holds live session cookies and the league-scoped bearer JWT, and it stays
   a git-ignored file on disk for the duration of this work.

   **Superseded for the next phase, ruled 2026-08-26:** the token *will* live in
   Postgres, encrypted, written by a CLI login flow. See Open Question 1, which
   now carries that design rather than the question. Nothing in this phase
   depends on the current arrangement beyond `state.storage_state_path()`.
7. **Postgres 16-alpine, Adminer** — matching every other project in the
   workspace. No pgAdmin (nothing in the workspace uses it).
8. **Host ports 54321 (Postgres) and 18082 (Adminer)**, both currently free.
   Taken across the workspace: 54320 + 18081 (optimizer), 5433 + 8090
   (clipcraft), 5435, 8080. Both overridable via env.
9. **The default `pytest` run still opens zero sockets.** DB-backed tests are
   opt-in behind a marker. This is a load-bearing rule in `CLAUDE.md`, not a
   preference.
10. **`FANTABOT_AUTO_ACT` is untouched.** It gates writes to the *remote site*.
    Writes to the local database are not remote actions and are not gated by it.

---

## Objective

Give fantabot a real database so that:

- **Cross-file queries stop being hand-rolled joins.** `scripts/join_qi_bias_performance.py`,
  `analyze_qi_bias_by_team.py` and `analyze_low_minutes_bias.py` each re-parse
  multiple CSVs and join them in Python. These become SQL.
- **The auction loop can survive a crash.** `auction.py:85` decrements
  `role_budget` **in memory only**. A mid-asta crash loses the budget state
  entirely. `processed_bids` exists in `state.json` and is reset by
  `auction.py:65` but **nothing ever appends to it** — it is dead state. The
  thing that needs persisting is not persisted, and the thing persisted is not
  used. A transactional `auction_bids` table fixes both.
- **Credit and roster drift becomes queryable.** `docs/lega-legamiallerotaie2.md`
  is a hand-captured point-in-time snapshot. Snapshots belong in tables, taken on
  a schedule, so "what did the market look like before that bid" is answerable.
- **The sentiment time-series gets transactional appends.** `news/store.py`
  appends to a CSV and rebuilds its resume index by re-reading the whole file.
  An upsert on `(data_run, player_id)` is the same semantics with crash safety.

**Success looks like:** `docker compose up -d`, `alembic upgrade head`,
`fantabot db-import`, and every number that was in a CSV is in a table, verified
by row counts, with the CSV readers deleted.

### Non-goals

- No API, no web UI, no ORM-backed frontend. Adminer is the only UI.
- No async, no connection pooling across processes, no pgBouncer.
- No hosted/remote Postgres. Local Docker only.
- Not touching `strategy.py`'s purity, `agentkit/`'s boundary, or the
  `NotImplementedError` DOM stubs in `lineup.py`/`auction.py`.

---

## Tech Stack

| Component | Version | Note |
|---|---|---|
| PostgreSQL | `postgres:16-alpine` | Workspace standard |
| Adminer | `adminer:latest` | `ADMINER_DEFAULT_SERVER: db` |
| SQLAlchemy | `>=2.0.50` | 2.x typed `Mapped[]` / `mapped_column` style |
| Alembic | `>=1.18.4` | |
| psycopg2-binary | `>=2.9.12` | Sync driver |
| Python | `>=3.11` (3.12 on disk) | Unchanged |

Existing deps unchanged. `pydantic-settings` already present — the DB URL
becomes another `Settings` field, not a new config mechanism.

---

## Commands

```bash
# --- database lifecycle ---
docker compose up -d                      # db + adminer
docker compose ps                         # check health
docker compose logs -f db
docker compose down                       # stop, keep volume
docker compose down -v                    # stop AND destroy data — never in a script

# Adminer:  http://localhost:18082   (System: PostgreSQL, Server: db,
#           User: postgres, Password: postgres, Database: fantabot)

# --- migrations ---
alembic revision --autogenerate -m "add player_sentiment table"
alembic upgrade head
alembic downgrade -1
alembic current
alembic history --verbose

# --- new fantabot subcommands ---
fantabot db-check                         # health + latency + per-table row counts/sizes
fantabot db-import --all                  # one-time CSV -> Postgres seed
fantabot db-import --table quotazioni     # single table, re-runnable
fantabot db-import --all --dry-run        # parse and validate, write nothing

# --- test / lint (unchanged defaults) ---
pytest                                    # zero sockets, DB tests deselected
pytest -m db                              # integration tests, requires docker compose up
ruff check src tests
mypy
```

`db-import` is idempotent, so a killed run can be restarted.

**`--dry-run` is a flag, not the default** — ruled 2026-08-26, resolving a
contradiction in this spec. An earlier draft of this section said dry-run was
the default posture, but success criterion 6 requires bare
`fantabot db-import --all` to load. SC 6 is the specific, testable statement and
wins. The safe-by-default posture is instead the mandatory explicit
`--all`/`--table` (no bare `db-import`) plus the allowlist gate on truncate.

---

## Project Structure

```
docker-compose.yml              → NEW. db + adminer only. No app service.
.env                            → NEW keys: FANTABOT_DATABASE_URL, FANTABOT_DB_HOST_PORT,
                                  FANTABOT_ADMINER_HOST_PORT
alembic.ini                     → NEW. repo root, alongside pyproject.toml
alembic/
  env.py                        → imports settings + Base, sets sqlalchemy.url at runtime
  script.py.mako
  versions/                     → one migration per schema change, hand-reviewed

src/fantabot/db/                → NEW package. The I/O shell — no decision logic.
  __init__.py                   → re-exports Base, get_session, database_manager
  engine.py                     → DatabaseManager (QueuePool, health cache, get_session)
  base.py                       → Base(DeclarativeBase) + TimestampMixin
  models/
    __init__.py                 → imports EVERY model so Base.metadata is complete
    reference.py                → players, teams, quotazioni, statistiche, qi_bias, target_price
    matches.py                  → voti, bonus_malus
    sentiment.py                → player_sentiment
    runtime.py                  → bot_state, auction_bids
    league.py                   → league_snapshot, league_team_snapshot, league_player_pool
  repositories/
    _base.py                    → RepositoryBase(session)
    admin.py                    → AdminRepository: health, table introspection, truncate
    reference.py, sentiment.py, runtime.py, league.py
  importers/
    _csv.py                     → comma-decimal + null-sentinel + coach-row handling
    quotazioni.py, statistiche.py, voti.py, bonus_malus.py, qi_bias.py, target_price.py

tests/
  test_db_models.py             → metadata assertions, no connection
  test_importers.py             → pure parsing, tmp_path CSVs, no connection
  test_repositories_fake.py     → repository protocol against an in-memory fake
  integration/test_db.py        → @pytest.mark.db, requires a live database
```

**`src/fantabot/db/` is a shell, not a layer with opinions.** The existing rule
holds: pure logic in pure modules, I/O in a thin wrapper. No scoring, no budget
math, no formation logic goes in here.

---

## Schema

### Naming

Column names stay **Italian, mirroring the CSVs** (`stagione`, `squadra`,
`ruolo`, `giornata`, `riassunto`, `fonti`, `deriva_ruolo`). Table names are
Italian plurals where the CSV is Italian, English where the concept is ours
(`bot_state`, `auction_bids`, `league_snapshot`). Python identifiers stay
English, as they already do.

### Departures from the CSV shape — each deliberate

| CSV form | Table form | Why |
|---|---|---|
| `;`-joined `ruoli_codice` (`"B;DS;E"`) | `text[]` | Postgres arrays are queryable; `;`-splitting in SQL is not. Classic stores a 1-element array so both listoni share one column. |
| `;`-joined `fonti`, `flags` | `text[]` | Same. |
| Comma-decimals as strings (`"6,25"`) | `numeric` | Converted at import. |
| `""` and `"0,0"` both meaning "no data" | `NULL` | The two sentinels mean different things (did-not-play vs no-data) and are collapsed only where `target_price.py` already collapses them. |
| Two parallel classic/mantra files | one table + `listone` enum | Identical grain; the only difference is the role column. |
| `stagione` missing from `target_price_*` (filename only) | real column, `NOT NULL` | A fix, not a mapping. |

### Tables

**Reference (season-scoped, from `scripts/`)**

- `players` — `id` (int PK, from the platform), `nome`. Names drift across
  seasons; season-specific attributes live in `quotazioni`, not here.
- `teams` — `(stagione, codice)` PK, `nome_completo`. **Required, not optional:**
  `quotazioni_*`/`statistiche_*`/`qi_bias_*`/`target_price_*` use 3-letter codes
  (`ATA`, `MIL`) while `voti.csv`/`bonus_malus.csv` use full names
  (`Fiorentina`). 27 distinct values across seasons, 20 in 2026/27 — so it is
  season-scoped, not global.
- `quotazioni` — `(stagione, player_id, listone)` PK; `ruoli_codice text[]`,
  `ruoli text[]`, `qi`, `qa`, `fvm`. 3201 rows/file → ~6402.
- `statistiche` — `(stagione, fonte, player_id, listone)` PK; the 11 stat
  columns. 8034 rows/file.
- `qi_bias` — `(stagione, player_id, listone)` PK; `qi`, `qa`, `fvm`, `delta`,
  `pct_delta`. 2678 rows/file.
- `target_price` — `(stagione, player_id, listone)` PK; `macro_role`, `qi`,
  `prior_media_fantavoto`, `predicted_pct_delta`, `team_factor`,
  `target_price`, `flags text[]`. 523 rows/file — the 2026/27 pool.

**Match grain**

- `voti` — `(stagione, giornata, player_id)` PK, `player_id` **nullable**.
  3039 rows have an empty `id` (coach / `Allenatore` rows); either a surrogate
  PK or a nullable FK is required — a `NOT NULL` FK rejects them.
- `bonus_malus` — same key, same nullable-`player_id` caveat.

> ⚠️ **`squadra` in `voti.csv` and `bonus_malus.csv` is corrupt.** The scraper
> mislabels it as the fixture's *home* team for every row in a match block —
> documented at `scripts/analyze_qi_bias_by_team.py:8-13` and confirmed
> (2022/23 giornata 1: 331 rows, only 10 distinct `(squadra, avversario)`
> pairs). Store it as `squadra_raw` with a comment, and resolve a player's real
> team through `quotazioni` on `(stagione, player_id)`. Do **not** build a
> `teams` FK on this column, and do not key on it.

**Sentiment**

- `player_sentiment` — columns exactly `news/store.py:COLUMNS`, PK
  `(data_run, player_id)`. That PK **is** the existing `existing_keys()` resume
  index, so the resume behaviour becomes an `ON CONFLICT DO NOTHING` upsert
  (`--force` → `DO UPDATE`). Eight score columns `numeric`, `fonti text[]`.

  **`deriva_ruolo` is `numeric(3,2)`, not boolean** — ruled 2026-08-26. SPEC
  originally specified a boolean; `news/store.py:build_row` writes a float in
  `(0, 1]`, and `data_sources/news_sentiment.py:drifted()` ranks players by that
  value. A boolean collapses the ranking to arbitrary order and the confidence
  is unrecoverable. Numeric can be narrowed later; boolean cannot be widened.

  This table is empty today: **`data/player_sentiment_2026-27.csv` does not
  exist yet** — the 523-player run is still outstanding. There is nothing to
  migrate, which makes this the last cheap moment to change its storage. The
  previous spec states that changing the schema after the first real run
  invalidates existing history.

**Runtime state** (replaces `data/state.json`)

- `bot_state` — `league_id` PK, `last_lineup_matchday`, `last_auction_session_id`,
  `updated_at`. Keyed by league because the account is in **two** leghe
  (`3584692`, `4103937`) and today's single flat JSON cannot represent that.
- `auction_bids` — `(league_id, session_id, player_id, placed_at)`; `role`,
  `amount`, `outcome`. **This is what `processed_bids` was supposed to be.**
  Per-role remaining budget is derived by summing this table, so a mid-asta
  crash no longer loses it.

`processed_bids` is **not** ported — it is dead state. `state.json`'s untyped
`dict[str, Any]` passthrough is dropped along with it: `load()` currently merges
unknown on-disk keys straight through and `save()` uses `default=str`, so a
`date` silently round-trips as a string. Typed columns are a deliberate
behaviour change, and `state.py` has **zero direct test coverage** today — so it
gets tests as part of this work.

**League snapshots** (from `apileague.fantacalcio.it`, per `docs/leghe-api.md`)

- `league_snapshot` — `(captured_at, league_id)`; `competition_id`, `sId`,
  `mday`, `mstr`, `budg`, `roster_size`.
- `league_team_snapshot` — `(captured_at, league_id, team_id)`; `idu`, `nome`,
  `owner`, `cri`, `crs`, `cr`.
- `league_player_pool` — `(captured_at, league_id, player_id)`; `quotd`,
  `fvm_classic`, `fvm_mantra`, `marle text[]`. 541 rows per capture.

Snapshots are **append-only and time-stamped**, never updated in place — the
point is the drift.

---

## Code Style

Sync SQLAlchemy 2.x, typed, `mypy --strict` clean. `Mapped[...]` annotations are
mandatory — strict mode rejects untyped `Column()`.

```python
# src/fantabot/db/base.py
from datetime import datetime

from sqlalchemy import DateTime, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Declarative base for every fantabot table."""

    type_annotation_map = {datetime: DateTime(timezone=True)}


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
```

```python
# src/fantabot/db/models/reference.py
from sqlalchemy import ARRAY, ForeignKey, Index, Numeric, SmallInteger, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from fantabot.db.base import Base, TimestampMixin


class Quotazione(Base, TimestampMixin):
    """One player's valuation for one season, on one listone.

    Classic and Mantra share this table; ``ruoli_codice`` holds a single-element
    array for Classic (``{"P"}``) and the full set for Mantra (``{"B","DS","E"}``).
    The source CSVs store these ``;``-joined — the array is a deliberate
    departure, see this spec.
    """

    __tablename__ = "quotazioni"

    stagione: Mapped[str] = mapped_column(String(7), primary_key=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"), primary_key=True)
    listone: Mapped[str] = mapped_column(String(7), primary_key=True)  # classic | mantra

    squadra: Mapped[str] = mapped_column(String(3), nullable=False)
    ruoli_codice: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False)
    ruoli: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False)
    qi: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    qa: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    fvm: Mapped[int] = mapped_column(SmallInteger, nullable=False)

    __table_args__ = (Index("idx_quotazioni_squadra", "stagione", "squadra"),)
```

Importers keep the messy parsing in one place, pure and testable:

```python
# src/fantabot/db/importers/_csv.py
from decimal import Decimal

_NO_DATA = {"", "0,0"}


def italian_decimal(raw: str) -> Decimal | None:
    """``"6,25"`` -> ``Decimal("6.25")``; ``""`` and ``"0,0"`` -> ``None``.

    The scraped CSVs quote decimals Italian-style. ``""`` means did-not-play
    (voti.csv) and ``"0,0"`` means no-data — ``scripts/target_price.py`` already
    filters the latter. A bare ``::numeric`` cast fails on one and silently
    yields zero on the other.
    """
    if raw.strip() in _NO_DATA:
        return None
    return Decimal(raw.strip().replace(",", "."))


def split_codes(raw: str) -> list[str]:
    """``"B;DS;E"`` -> ``["B", "DS", "E"]``. Upper-cased; empty -> ``[]``."""
    return [part.strip().upper() for part in raw.split(";") if part.strip()]
```

Ruff: line length 100, target py311, existing `select`/`ignore` unchanged.

> Note: `B008` is already in the ruff ignore list — that rule exists for
> FastAPI's `Depends()` defaults and was inherited, not earned. This phase does
> not add a reason to keep it. Leave it; flag it if it ever masks a real bug.

---

## Testing Strategy

The rule from `CLAUDE.md` is absolute and this spec does not weaken it:

> **The test suite makes zero agent calls and opens zero sockets.**

Four tiers:

1. **Model metadata** (`tests/test_db_models.py`) — assert table names, PKs,
   nullability, that `Base.metadata` contains every expected table. Pure
   introspection, no engine, no connection.
2. **Importer parsing** (`tests/test_importers.py`) — `tmp_path` CSV fixtures
   through `italian_decimal` / `split_codes` / the row mappers. Covers every
   gotcha explicitly: `"6,25"`, `""`, `"0,0"`, empty-`id` coach rows, mixed-case
   Mantra codes, `target_price`'s missing `stagione`.
3. **Repository behaviour** (`tests/test_repositories_fake.py`) — repositories
   take a `Session`; tests pass an in-memory fake implementing the narrow
   protocol actually used. Same injection pattern as `agentkit/`'s runner fakes.
4. **Integration** (`tests/integration/test_db.py`) — `@pytest.mark.db`,
   **deselected by default** via `addopts = "-m 'not db'"` in `pyproject.toml`.
   Requires `docker compose up -d`. This is the only tier that connects.

Existing suites that pin the CSV contract — `tests/test_news_store.py` (204
lines) and `tests/test_news_sentiment_source.py` (182 lines) — **stay green
until their modules are ported**, then are rewritten against the repository, not
deleted. All 146 current tests must still pass at every checkpoint.

Add: `tests/test_state.py`, which does not exist today, before `state.py` is
replaced. Porting untested code is how behaviour changes go unnoticed.

---

## Boundaries

**Always**

- Generate migrations with `alembic revision --autogenerate`, then **read the
  generated file before applying it**. Autogenerate misses `ARRAY` type changes,
  server defaults, and index renames.
- One migration per logical schema change, with a descriptive `-m` message.
- Run `alembic upgrade head` against a scratch database before committing a
  migration.
- Convert comma-decimals at import, never with a raw SQL cast.
- Keep `src/fantabot/db/` free of decision logic.
- Keep `data/` in `.gitignore` exactly as it is during this phase.

**Ask first**

- Deleting any CSV writer from `scripts/` (assumption 5 says they get rewritten;
  deleting the file is a separate call).
- Deleting the CSVs themselves, or removing them from `.gitignore`'s negation
  block.
- Anything that would put `storage_state.json` or the bearer JWT into Postgres.
- Adding a dependency beyond the three listed.
- Changing `player_sentiment`'s columns after the first real 523-player run.
- Making any DB call from a module that is currently pure (`strategy.py`,
  `news/models|mantra|prompt|pool`, `mantra_grid/gates.py`).

**Never**

- Commit `.env`, credentials, or a real `DATABASE_URL` with a non-default password.
- Run `docker compose down -v` from a script — it destroys the volume.
- Hand-edit a table to match a migration, or hand-edit a migration to match a
  table. The chain is the record.
- Make the default `pytest` run require a live database.
- `TRUNCATE` outside `AdminRepository`'s allowlist-gated path.
- Flip `FANTABOT_AUTO_ACT`'s default. Unrelated to this phase and still `false`.

---

## Success Criteria

Specific and checkable:

1. `docker compose up -d` brings up `db` and `adminer`; `docker compose ps`
   shows `db` healthy within 30s via its `pg_isready` healthcheck.
2. Adminer at `http://localhost:18082` connects to `db` with
   `ADMINER_DEFAULT_SERVER` prefilled.
3. Neither host port collides with optimizer (54320/18081) or clipcraft
   (5433/8090); both are env-overridable.
4. `alembic upgrade head` on an empty database creates every table listed under
   **Schema**, and `alembic downgrade base` removes them all cleanly.
5. `alembic revision --autogenerate` immediately after `upgrade head` produces
   an **empty** migration — models and migrations agree.
6. `fantabot db-import --all` loads all ten CSVs, and per-table row counts match
   the source files exactly: quotazioni 3201×2, statistiche 8034×2, voti 50634,
   bonus_malus 50634, qi_bias 2678×2, target_price 523×2.
7. Re-running `fantabot db-import --all` is a no-op — same row counts, no
   duplicate-key errors.
8. All 3039 empty-`id` coach rows in `voti`/`bonus_malus` load without loss.
9. No `numeric` column contains `0` where the source held `""` or `"0,0"`.
10. `fantabot db-check` reports health with latency, plus row count and size per
    table, for every table in the allowlist.
11. `fantabot news-fetch --write` writes to `player_sentiment`; re-running the
    same day inserts zero rows without `--force`; `--force` updates in place.
12. A mid-asta kill and restart recovers per-role remaining budget from
    `auction_bids` — the case that loses data today.
13. `pytest` passes with **zero sockets opened**, DB tests deselected, and all
    146 pre-existing tests still green.
14. `pytest -m db` passes against a live compose stack.
15. `ruff check src tests` and `mypy` are clean; `mypy --strict` covers
    `src/fantabot/db/`.
16. `state.py` has direct test coverage before it is replaced.

---

## Open Questions

1. ~~**Where does the bearer JWT live?**~~ **Resolved 2026-08-26 — and it is
   the next phase, not this one.**

   The decision: a CLI command opens the leghe.fantacalcio.it login, the user
   authenticates there, the command extracts the league-scoped bearer token and
   stores it **encrypted in Postgres**. When the token expires, the same command
   re-authenticates.

   What that phase inherits, already established:

   - **Where the token is.** `docs/leghe-api.md` documents the extraction:
     `localStorage["LEAGUES2024_LOCAL"]` →
     `current-user-{userId}.currentLeague.token`. The account-level `jwt`
     beside it does *not* authenticate against `apileague` — only
     `currentLeague.token` does.
   - **When it expires.** The JWT's `exp` claim, ~365 days after `iat`. So
     "has it expired" is a local check on a decoded claim, not a round-trip —
     and a `401 ATH001` is the runtime signal that it expired early.
   - **Why there is more than one.** The token is scoped to a single `l_id`, so
     a two-lega account needs one per lega. `bot_state` and `auction_bids` are
     already keyed by `league_id`; the token table should be too.
   - **Who writes it.** `auth.py` already runs a headed Playwright login and
     calls `storage_state()`. The new command is that flow plus a parse and an
     encrypted write, not a new browser integration.

   Open inside that phase, and not decided here: **where the encryption key
   lives.** A key in `.env` protects a database dump but not someone who can
   read `.env`, which is still strictly better than today's plaintext file —
   but it should be chosen deliberately rather than defaulted into.
2. **Does `data/README.md` become a data dictionary for the tables, or get
   replaced by comments in the model files?** It is already stale — it documents
   7 CSVs when 10 exist (`qi_bias_*` and `target_price_*` are undocumented).
3. ~~**Should `voti.squadra_raw` be repaired at import?**~~ **Resolved
   2026-08-26: stored corrupt-but-labelled.** Repairing it would hide a scraper
   bug that is still live. Nothing keys, indexes or joins on the column, and a
   test enforces that.
4. ~~**Do the four `docs/fantalab/*.md` entity models belong in this schema?**~~
   **Resolved 2026-08-26: out of scope.** `target_price` and `auction_bids` are
   built to this spec's Schema section. `02-data-model.md`'s price model (fasce,
   per-mille FVM, PMA time-series) and `01-auction-engine.md`'s state machine are
   a later phase and may reshape both tables then. Noted as a known cost.
5. ~~**Snapshot cadence.**~~ **Resolved 2026-08-26: the producer is deferred.**
   The three `league_*` tables ship in this phase and stay empty. Building the
   capture needs an HTTP client, which is on the Ask-first list, and it needs
   the token question above answered first — the endpoints in
   `docs/leghe-api.md` all require the league-scoped bearer.

   **Named blocker:** Open Question 1's next-phase work. Cadence gets decided
   with the producer, not before it.
6. ~~**`.gitignore` cleanup**~~ **Resolved 2026-08-26: collapsed.** The
   duplicated `data/` negation block appears once now. `git ls-files data/` is
   byte-identical before and after, so nothing entered or left version control.

---

## Next phase

Not this spec, in rough dependency order:

1. **Encrypted token storage and a CLI login flow** — Open Question 1 above.
   Everything that talks to `apileague.fantacalcio.it` waits on this, including
   the league snapshot producer.
2. **The league snapshot producer** — Open Question 5. The three tables are
   already there.
3. **The `NotImplementedError` stubs** in `lineup.py` and `auction.py`. The
   auction one also gains a settle step: bids are recorded as `pending` today
   and nothing resolves them, because resolving needs the room's result.
4. **The Mantra lineup engine** — `data/mantra_schemi.json` is on disk as its
   input, and `player_sentiment.deriva_ruolo` is the drift warning it needs.
