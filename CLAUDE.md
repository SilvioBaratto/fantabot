# CLAUDE.md

This file provides guidance to Claude Code when working on `fantabot`.

## Project

`fantabot` is a standalone Python Typer CLI that runs fantacalcio for the user
on `leghe.fantacalcio.it` with zero human intervention: submits the weekly
lineup, and bids in both the asta iniziale (season-opening auction) and asta
di riparazione (mid-season repair auction). Modeled after `mailwise`'s
architecture in the sibling workspace — a cron-driven bot with an interactive
one-time auth step, not a request/response API.

Console script: `fantabot` → `fantabot.interface.app:app`. Python ≥3.11. Build backend:
hatchling. No frontend, no BAML (yet — see "Future: BAML upgrade path" below).

## Common commands

```bash
conda activate fanta        # the only environment; there is no .venv
pip install -e ".[dev]"     # re-run whenever pyproject.toml changes
playwright install chromium

docker compose up -d         # Postgres on 54321, Adminer on 18082
alembic upgrade head
fantabot db check            # health, per-table row counts and sizes

fantabot auth login               # interactive login → encrypted tokens in league_tokens
fantabot auth login --force       # re-auth even when every stored token is valid
fantabot auth status        # stored / expires / state per lega; works with no key
fantabot auth forget --league 4103937
fantabot config-check

fantabot news fetch --limit 5           # smoke test, queries but writes nothing
fantabot news fetch --write             # the weekly run: all 523, both leagues
fantabot mantra-grid --write            # one-off, collects the Mantra schema grid

fantabot auth fantalab-login                 # headed, manual; session encrypted into Postgres
fantabot harvest scan --seed seed.json     # which auctions are live, both formats
fantabot harvest collect --seed seed.json --out landing.jsonl   # subscribe, append to disk
fantabot harvest load landing.jsonl --seed seed.json --follow   # landing zone -> Postgres
fantabot harvest backfill events.jsonl --seed seed.json         # a recorded evening

pytest                       # default tier: zero sockets, db tests deselected
pytest -m db                 # integration tier, needs the compose stack
ruff check src tests
mypy

alembic revision --autogenerate -m "..."
alembic upgrade head
alembic check                # models and migrations agree?
```

## Architecture

Four layers, dependencies pointing strictly inward. `tests/test_layers.py` enforces
this over every module, transitively, and `tests/_importgraph.py` is how it reads the
graph — an AST walk that counts imports inside function bodies and under
`TYPE_CHECKING`, because those are where the three real violations were hiding.

```
src/fantabot/
  domain/        Pure. No I/O, no network, no clock, no framework import.
    asta/        legality, optimizer, reservation, roles, sentiment, state, value,
                 bid, drain, live, opponents, prices, report, stateentry
    harvest/     sse, reducer, reconstruct, incremental, registry, compare, backfill,
                 models
    news/        models, mantra, prompt, pool, store, sink, pipeline
    mantra/      models, gates, prompt
    shared/      parsing, club_names, resources, values
    tokens/      claims, crypto, capture, fantalab, status, errors
  application/   Use cases. Orchestrates domain through ports.
                 asta_planner, harvest_loader, harvest_supervisor, news_fetcher,
                 mantra_collector, pricing, auth_login, fantalab_login, reporting
  adapters/      The outside world, one subpackage per kind.
    persistence/ engine, base, models/, repositories/, upserts, scraping, news_pool,
                 news_sentiment
    http/        apileague, fantalab/{rest,rtdb,feed,room,listone}, harvest/{client,
                 transport,stream}
    agent/       env, options, runner
    browser/     capture (Playwright), storage_state
    files/       landing, news_sink, mantra_writer
    tokens/      store, fantalab_store
    scraping/    quotazioni, statistiche, voti
  interface/     Typer only. Nothing else may import typer.
                 app (the root, and the one Console), asta, harvest, console, options
  config.py      settings; the one module both sides may read
  data/          mantra_schemi.json, mantra_compat.json — package data, not runtime state
```

`tests/` mirrors this. Which file goes where is a written table,
`tests/_testtree.py`, not a rule: deriving it from a file's imports puts
`test_token_store.py` in `domain/`, and deriving it from the filename puts
`test_state.py` under `domain/asta/` when it is about `adapters/browser/storage_state.py`.

### The rules the layers encode

* **`domain/` is pure, and that is why this repository is testable.** No
  `fantabot.adapters.persistence`, no sqlalchemy, no playwright, no httpx, no agent SDK,
  no typer, no rich, no `fantabot.config`, no `fantabot.interface`. The ratchet in
  `test_layers.py` started at seven violations and is at zero; a new one fails, and so
  does a fixed one whose line nobody deleted.
* **`application/` orchestrates and does not present.** It takes a `Reporter`
  (`application/reporting.py`) and a `BrowserFactory` rather than importing the Console
  or Playwright. One violation is recorded: `pricing.run` builds Rich tables.
* **`interface/` is the only importer of typer**, and holds the one `Console()`.
* **The database is never on the collection path.** `tests/application/test_aste_outage.py`
  walks the imports of every capture module and fails if any can reach persistence. An
  outage must cost catch-up time and never a record.
* **`agentkit`/`adapters/agent` owns the SDK.** Exactly one `async for message` in the
  repo, and no `claude_agent_sdk` import outside that package — both enforced.
* **Role drift is fail-closed.** `ruolo_campo` is what a player is observed playing;
  `rules/sistema-mantra.md:34` says the platform freezes its own tag in late July and
  enforces it at submission. So `deriva_ruolo` widens a band and warns, and `ruolo_campo`
  never reaches a decision module — checked over the whole domain package.

### Where the decisions live

* **`domain/asta/sentiment.py`** — the news feed as a multiplier on `fvm`. Pure, and with
  no clock: `as_of` is a parameter, because a pure module that reads the clock has tests
  that are a coin flip. Four layers: a gate (`disponibilita`, `titolarita`, each through
  its own floor), a tilt scaled by `--tilt-k`, a confidence shrink on a 7-day half-life,
  and a normalization pinning the pool mean at exactly 1.0. That last is load-bearing:
  the objective is `sum(mu) - lam*Var` and `Var` does not scale with `mu`, so rescaling
  every mean would silently re-tune the operator's `lam`. `TIT_FLOOR = 0.40` is measured
  — `fvm` and `titolarita` share R² ≈ 0.37–0.43 of their rank variance. `DISP_FLOOR = 0.50`
  fixes a horizon mismatch instead: `disponibilita` asks "available *now*" and an asta
  buys a season. `--no-sentiment` is the ablation control, asserted to reproduce the
  pre-sentiment field for field. Spec: `docs/spec-asta-sentiment.md`.
* **`application/asta_planner.py`** — the one place the value model is built. Three
  commands each had their own copy and they drifted: `asta bid` was still planning on
  plain `fvm` after `asta optimize` had moved to the sentiment-adjusted model, which on
  the 2026-08-28 data would chase a player with a metatarsal fracture to 62 credits.
* **`domain/harvest/incremental.py`** — the reconstruction fold, made resumable, so
  `harvest load --follow` stops re-reading a 1.22 GB landing zone every ten seconds.
  Always pass emitted closes through `drain`: Postgres rejects a statement whose `VALUES`
  repeat a conflict key, and under `--follow` that retries for ever behind a "database
  unreachable" message.
* **`domain/asta/legality.py`** — L1, bipartite matching over the 11 Mantra schemi.
  `-1*` is kept distinct from `-1` and never folded in: it is refused at submission and
  allowed only after a forced substitution, so a matcher treating them alike builds
  lineups the platform rejects.
* **`adapters/http/harvest/`** — the SSE collector. `harvest collect --seed` re-reads the
  seed every 60 s and adopts auctions that opened after launch; turnover was 20–30% of
  the live population per 15–20 minutes. `--pool` must exceed the live population — 649
  on 2026-08-27 against a default of 250. Both commands re-read the seed, and both had
  to learn it separately.

## Known unknowns — resolve before flipping `FANTABOT_AUTO_ACT=true`

- **Lineup submission**: not built, and no longer scaffolded. The Classic modules
  that stood in for it were deleted rather than left raising `NotImplementedError`
  against markup nobody had inspected. **Start from `docs/leghe-api.md`**, not from
  the DOM: the site runs a separate JSON API (`apileague.fantacalcio.it`) with auth
  reverse-engineered and several read endpoints confirmed working, and the bearer
  token is an encrypted row in `league_tokens` reachable through
  `apileague.auth_headers(league_id, store=...)`. Only the submit POST is still
  undocumented (see "Gaps" in that doc) and needs a live Network capture.
- ~~**Asta mechanics**~~ **Resolved.** Not the leghe.fantacalcio.it room at all —
  the asta runs on FantaLab, and `asta bid` drives its unauthenticated RTDB
  directly. See `docs/fantalab/06-asta-write-path.md`, verified live 2026-08-28.
- ~~**The bot could not actually bid**~~ **Resolved 2026-09-01** by the asta-room
  phase ([`tasks/archive/asta-room-spec.md`](tasks/archive/asta-room-spec.md)).
  Three defects, none visible from reading the code:
  **B1** — the lot arrives from `auction/<fl>` as a FantaLab uuid while every
  walk-away is keyed by fantacalcio id, so `asta bid` answered "not a target,
  hold" every two seconds for a whole evening. No bid, no error, nothing to
  notice. **B2** — the walk-away is `objective − objective without him`, a
  correct marginal value and a wrong reservation price: over a pool of
  substitutes it collapses, and 10 of 30 measured exactly 0.0 while the same plan
  budgeted 96 credits for one of them. It is now floored at
  `max(MIN_BID, α · planning_cost)`. **B3** — 41 of 570 pool players are absent
  from FantaLab's listone and can never be called, yet the optimizer planned
  around them.
- **α is 1.00, and the reason is arithmetic, not taste.** The plan is built to
  cost exactly the budget at `planning_cost` (measured: 500 of 500), so a floor
  of `1.0 × planning_cost` makes the bidder's ceiling agree with the plan's own
  budget; at 0.8 it would cap us at 400 for a plan we priced at 500. `asta
  calibrate` replays 45 recorded 8×500 rooms and is the evidence. ⚠ Its
  acceptance threshold was wrong **twice** — once measuring corpus overlap
  instead of the floor, once from a denominator that reported `won %` above
  100% — and was removed rather than lowered a third time. Both versions are
  recorded in the archived spec.
  **The mechanism this bullet describes is superseded, the conclusion is not.**
  The asta-fixes phase (closed 2026-09-02,
  [`tasks/archive/asta-fixes-spec.md`](tasks/archive/asta-fixes-spec.md) §2.A) deleted
  `price_floor`/`--floor-alpha` — a floor computed only for the pre-briefed 40 plan
  members — because the first live auction proved it silently held every unplanned lot
  at any price, `walk_away: null` on 4,501 of 5,192 journal rows.
  `domain/asta/reservation.lot_ceiling` replaces it for the one lot actually on the
  block each cycle: a real re-solve with that lot forced in, planned or not, scaled by
  `--ceiling-alpha` (still `1.00` by default, same arithmetic). `reservations()`
  survives only for the LISTONE table and copilot brief, where 40 players a cycle must
  be priced and a full re-solve for each is not affordable. The denominator defect is
  also gone — Task 1.3's rewrite of `_replay_one` makes `won` unable to exceed
  `available` by construction; a real sweep against the live database now reads
  32–49% across every alpha, never near 100%, and a repaired acceptance threshold is
  once again possible, just not built.
- **The MAX cap has no server backstop.** `docs/fantalab/01:142` calls it
  client-enforced and `06:389-412` shows the RTDB rules validating only that a
  raise exceeds the current price and names the right lot. `domain/asta/bid.py`'s
  `max_cap` guard is the only thing between a "pay anything" walk-away — `lot_ceiling`
  for the one lot actually on the block (`reservations()` for the bulk LISTONE/brief
  read, per the asta-fixes note above) — and a rosa that cannot be fielded.
- **Arming needs two locks and a record.** `FANTABOT_AUTO_ACT` **and** `--arm`,
  both opt-in, because the env var is process-wide `.env` state and the operator
  who edits it in the morning is not the one at the keyboard at 21:47. First
  Ctrl-C disarms and keeps drawing; second exits.
- **Stats source**: still unchosen. News sentiment is covered by
  `fantabot news fetch` (see `docs/spec-news-sentiment.md`), which is a different
  thing: it is opinion and availability, not per-matchday projected scores. When one
  is picked, write the interface against the consumer that exists then.
- ~~**Bearer token**~~ **Resolved.** Encrypted in Postgres (`league_tokens`),
  written by `fantabot auth login`, read through `apileague.auth_headers`. Spec:
  [`tasks/archive/token-store-spec.md`](tasks/archive/token-store-spec.md) — recovered from commit
  `edb693c` on 2026-08-30, because `SPEC.md` had been overwritten by four later
  phases and nine links still pointed at it. `SPEC.md` holds only the **current**
  phase; a closing phase copies its spec to `docs/spec-<phase>.md` first.
- **Mantra vs Classic**: the user plays **both**, one league each — and as of
  2026-08-26 we know which is which: **`3584692` (Legamiallerotaie) is Classic**
  (`sroles=1`, `minrl=[3,8,8,6]`, 25-man) and **`4103937` (Legamiallerotaie2) is
  Mantra** (`sroles=2`, `minrl=[2,28]`, 30-man). By elimination from the roster
  settings endpoint, not from field names — see `docs/leghe-api.md`. The Classic
  role model (`Role` P/D/C/A, `VALID_FORMATIONS`) was deleted with the rest of that
  scaffolding in W2; nothing here can field a Classic XI, and `domain/asta/` is Mantra
  only — 12 role codes across 11 schemas on four lines. The schema grid ships as package
  data at `src/fantabot/data/mantra_schemi.json`.
- ~~**`mantra_compat.json` is thin**~~ **Resolved 2026-08-28.** It held one entry
  and ten empty lists; it is now the whole table — 11 schemas × 11 slots × 12
  roles = 1,452 cells — transcribed from the published PDF, which is kept at
  `docs/sources/`. The single entry it did have was *correct*; the file was
  simply answering a much narrower question than L1 asks. The load-bearing value
  is **`-1*`**: not schierabile at lineup submission, allowed with a malus only
  after a forced substitution. Collapsing it into `-1` reads as "allowed" and
  builds lineups the platform rejects.
  Two things the transcription caught. `mantra_schemi.json` had **4-3-1-2's
  `T/A/Pc` slot truncated to `A/Pc`**, because a gate asserted a ceiling of two
  roles per slot that the source does not have — the first collection got it
  right and the gate rejected it. And the gates only ever ran against fixtures,
  never against the shipped file, so a one-entry matrix passed for a week; a test
  now judges the artefact itself.

## Working rules

- `FANTABOT_AUTO_ACT` defaults to `false` — deliberate, matches mailwise's
  `AUTO_SEND` convention. Don't flip the default; the user opts in via `.env`
  after selectors are verified.
- `auth login`'s sign-in stays manual/headed — don't script credential entry
  without first confirming the login form has no captcha/2FA and getting
  explicit sign-off, since a scripted login is what gets accounts flagged. The
  same rule now covers **any** page interaction after sign-in:
  `application/auth_login.py` navigates once and clicks nothing, and a test asserts the
  page's recorded call list is exactly `["goto"]`. The browser itself is injected —
  `adapters/browser/capture.real_browser` is bound by the interface, so the use case
  never imports Playwright.
- **A bearer token is never printed, logged, `repr`'d or committed** — in any
  form, truncated or whole. `tests/adapters/tokens/test_token_secrecy.py` enforces it: no JWT
  literal in any tracked file, an AST walk over every print/log/raise argument in the
  modules that hold one, and `decrypt(` confined to an allowlist split in two —
  `DECRYPT_SITES` (the three that decrypt and must) and `DECRYPT_RESERVED`
  (`apileague.py`, which does not, listed so a change there fails at review). The
  modules are named as *modules* and resolved through the import system: they were
  paths filtered by `is_file()`, and one move dropped `apileague.py` from every
  assertion in the file without turning any of them red. Never pass the encryption key
  on argv — `ps` shows it and the shell keeps it in history.
- **Decision logic stays pure — no Playwright, no network, no clock.** That is what
  `domain/` means, and it is enforced rather than intended: 1039 tests in the default tier
  plus 136 in `db`, opening zero sockets and making zero agent calls. Keep new logic in a
  pure module and the I/O in a thin shell around it. The clock counts as I/O: the asta
  feature reads the calendar in exactly one place (`interface/asta.py::_today`),
  enforced by `tests/domain/asta/test_asta_clock.py`, because the golden harness has to
  freeze it.
- **The test suite makes zero agent calls and opens zero sockets.** Runners and
  sleepers are injected so the fan-out is testable with fakes. Keep it that way;
  a suite that queries is a suite nobody runs.
- Ruff: line length 100, target py311, same `select`/`ignore` as mailwise.
  `mypy --strict` on `src/fantabot` (tests excluded).
- **The database is the source of truth.** `data/`'s CSVs were the one-time seed and
  nothing reads them any more; the scrapers and `news fetch` write to Postgres. Row
  counts in `data/README.md` are floors, not fixtures — the scrapers read a live site
  and it moves. The two Mantra JSON artefacts are the exception and are **package data**
  at `src/fantabot/data/`, reached through `domain/shared/resources.py`: they are the
  matcher's input, and reading them relative to the working directory meant
  `asta legality` and `mantra-grid --write` agreed only when both ran from the
  repository root.
- **Archive `SPEC.md`, `tasks/plan.md` and `tasks/todo.md` when a phase closes**, to
  `tasks/archive/<phase>-spec.md`, `-plan.md` and `-todo.md`. Not to `docs/` — `.gitignore:23`
  ignores it, which is how the token-store spec came to survive only in git history. Repoint that phase's spec
  at the archived path in the same commit. Those two filenames are reused by
  every phase, so an inbound link to them silently starts describing different
  work — four references had rotted this way by 2026-08-28, in
  `domain/tokens/status.py`, `tests/adapters/tokens/test_token_secrecy.py` and two older specs. A spec is a
  record and is not amended to rewrite history; a link that no longer resolves
  is a different thing, and is repaired.

- Every importer and repository write is an **upsert**. A killed run is
  restarted, never repaired.

## Future: BAML upgrade path

`domain/asta`'s tunables — `SentimentWeights`' floors and tilt weights, the role
composition, `DEFAULT_SAME_TEAM_RHO` — are declared priors and hand-tuned heuristics,
not learned. Only `tit_floor` is fitted. Once a real stats source exists and they prove
too blunt (e.g. walk-aways need reasoning about scarcity and form, not a static tilt),
consider a BAML function for the pricing — following the pattern in `optimizer`,
`dietwise`, `clipcraft`. Don't add BAML now; there is one `data_run` to reason over and
it would be build-ahead-of-need.
