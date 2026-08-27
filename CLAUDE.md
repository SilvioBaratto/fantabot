# CLAUDE.md

This file provides guidance to Claude Code when working on `fantabot`.

## Project

`fantabot` is a standalone Python Typer CLI that runs fantacalcio for the user
on `leghe.fantacalcio.it` with zero human intervention: submits the weekly
lineup, and bids in both the asta iniziale (season-opening auction) and asta
di riparazione (mid-season repair auction). Modeled after `mailwise`'s
architecture in the sibling workspace — a cron-driven bot with an interactive
one-time auth step, not a request/response API.

Console script: `fantabot` → `fantabot.cli:app`. Python ≥3.11. Build backend:
hatchling. No frontend, no BAML (yet — see "Future: BAML upgrade path" below).

## Common commands

```bash
conda activate fanta        # the only environment; there is no .venv
pip install -e ".[dev]"     # re-run whenever pyproject.toml changes
playwright install chromium

docker compose up -d         # Postgres on 54321, Adminer on 18082
alembic upgrade head
fantabot db-import --all     # one-time seed from the CSVs in data/
fantabot db-check            # health, per-table row counts and sizes

fantabot login               # interactive login → encrypted tokens in league_tokens
fantabot login --force       # re-auth even when every stored token is valid
fantabot token-status        # stored / expires / state per lega; works with no key
fantabot token-forget --league 4103937
fantabot config-check
fantabot lineup-submit

fantabot news-fetch --limit 5           # smoke test, queries but writes nothing
fantabot news-fetch --write             # the weekly run: all 523, both leagues
fantabot mantra-grid --write            # one-off, collects the Mantra schema grid

fantabot fantalab-login                 # headed, manual; session encrypted into Postgres
fantabot aste-scan --seed seed.json     # which auctions are live, both formats
fantabot aste-collect --seed seed.json --out landing.jsonl   # subscribe, append to disk
fantabot aste-load landing.jsonl --seed seed.json --follow   # landing zone -> Postgres
fantabot aste-backfill events.jsonl --seed seed.json         # a recorded evening

pytest                       # default tier: zero sockets, db tests deselected
pytest -m db                 # integration tier, needs the compose stack
ruff check src tests
mypy

alembic revision --autogenerate -m "..."
alembic upgrade head
alembic check                # models and migrations agree?
```

## Architecture

1. **`config.py`** — `pydantic-settings` reading `.env`. `fantabot_auto_act`
   defaults to `false` — the same "safe by default" pattern as mailwise's
   `AUTO_SEND`: every write action (submit lineup, place bid) is gated behind
   it and logs a dry-run message instead when it's off.
2. **`browser.py`** — two Playwright context managers. `context()` reuses
   `data/storage_state.json` if one was kept; `interactive_login_context()` is
   headed, used only by `login.py`, and **writes nothing** — the caller reads
   `storage_state()` inside the body and decides whether to persist it.
3. **`login.py`** — `fantabot login`. Opens a real headed Chrome window, waits
   for the human to log in (captcha/2FA included), then reads each lega's token
   out of `localStorage`, encrypts it and writes it to `league_tokens`. **The
   sign-in is never scripted, and no page is ever clicked** — every entry in
   `leagues[]` carries its own working token, measured 2026-08-26, so there is
   nothing to navigate. Everything checkable is checked *before* the browser
   opens: key present, key valid, database reachable.
4. **`state.py`** — one function: `storage_state_path()`. Runtime state moved
   to Postgres (`bot_state`, `auction_bids`), keyed by lega because the account
   is in two and one flat file could not tell them apart. This module imports
   nothing from `db/` on purpose — `browser.py` sits on its import chain, and
   `fantabot --help` has to work before a database exists.
5. **`models.py`** — frozen dataclasses (`Player`, `RosterSlot`, `Lineup`,
   `AuctionListing`, `BidDecision`, ...) plus `VALID_FORMATIONS`, the 7 legal
   classic-mode (D, C, A) splits summing to 10 outfield players.
6. **`strategy.py`** — **the only module with real, tested decision logic**;
   pure functions, no I/O, no Playwright. `pick_starting_lineup` picks the
   formation that fields the most players (attackers break ties) from
   available+fieldable roster slots by projected score, then picks
   captain/vice as the top-2 scorers among starters. `allocate_auction_budget`
   splits total credits by role (default 5/15/35/45 GK/DEF/MID/ATT — a common
   classic-mode heuristic, not derived from real market data yet).
   `decide_bid` bids current+1, capped at `min(target_price, role_budget_left)`,
   returns `None` (pass) once at the cap.
7. **`data_sources/`** — `StatsSource` `Protocol` (`projected_scores`,
   `player_pool`, `target_price`), still unimplemented. Alongside it,
   `models.py` (the frozen value types, and the single definition of the eight
   `SCORES`) and `news_sentiment.py`, a thin adapter over the sentiment read
   repository: `latest`, `trailing` (silent rows excluded), `drifted()`. It
   holds a session, never a cached table — `auction.py` polls for hours and
   would otherwise hold a frozen reading for a whole asta. Not wired into
   `strategy.py` yet.

13. **`db/`** — the persistence shell. `engine.py` builds the Engine lazily on
   first `get_session()`, never at import; `base.py` carries the naming
   convention that makes `alembic downgrade` work; `models/` is the schema,
   `repositories/` every query, `importers/` the one-time CSV seed. Everything
   here is I/O — decision logic stays in the pure modules. A test enforces that
   `create_engine` appears nowhere outside this package.
10. **`agentkit/`** — the Claude Agent SDK plumbing, shared by every command
   that queries. `env.py` closes both credential leak vectors (`os.environ`
   *and* `ClaudeAgentOptions.env`, since `session_resume.py:356` reads either),
   `options.py` builds the options, `runner.py` holds **the one message loop**.
   `runner.run` calls `assert_auth`, which picks one of two mirror-image proofs:
   `assert_subscription_auth` ("no credential anywhere", the default) or
   `assert_byo_backend` ("a base URL *and* a token"), the latter only when
   `FANTABOT_AGENT_BASE_URL` is set. That opt-in routes the fan-out at any
   Anthropic-compatible shim — Ollama's is the **local** daemon on
   `http://localhost:11434`; `ollama.com` serves no `/v1/messages`, so cloud
   models go through that same daemon with a `:cloud` model suffix. Verified
   end-to-end on 2026-08-26 against `deepseek-v4-flash:cloud`: **WebSearch and
   WebFetch both work** — Claude Code runs them client-side, not through
   Anthropic's server-side `web_search` tool, so a shim does not cost the search
   (the guides warning otherwise are about Bedrock, which is a different path) —
   and `output_format`'s json_schema survives, because the shim honours forced
   `tool_choice`. `news-fetch --limit 1` returned a schema-valid row citing four
   same-day sources. `resolve_agent_model` refuses a `claude-*` id with a base
   URL set, and a non-`claude-*` id without one; both are otherwise silent until
   the cron log. Two cosmetic stderr lines are expected on this path: a
   `claude.ai connectors are disabled` warning, and one
   `[claude-code:unrecognized_model] ... generate_session_title` per query.
   Agent-level failures are returned, never raised. Two tests enforce the
   boundary: exactly one `async for message` in the repo, and no
   `claude_agent_sdk` import outside `agentkit/`.
11. **`news/`** — `fantabot news-fetch`. One query per player over
   WebSearch/WebFetch, validated against `PlayerSentiment`, appended to
   `data/player_sentiment_2026-27.csv` (tracked by git — a past Wednesday
   cannot be regenerated). `models`/`mantra`/`prompt`/`pool` are pure; only
   `store`/`pipeline` do I/O. `mantra.drift()` is the reason the whole thing
   exists for Mantra: the platform freezes role tags in late July and never
   revisits them, so `quotazioni_mantra.csv` drifts by design and this is the
   only column that can say so.
12. **`mantra_grid/`** — `fantabot mantra-grid`, one-off, **not on cron**.
   Collects the 11 schemas and the out-of-position matrix into
   `data/mantra_schemi.json` / `mantra_compat.json`. Six fail-closed gates in
   `gates.py`; a failed gate writes nothing and the output is never
   hand-patched to satisfy a check.
8. **`lineup.py`** — orchestrates one matchday: scrape deadline/roster →
   score via `StatsSource` → `strategy.pick_starting_lineup` → submit (or dry
   run). `scrape_matchday_info` / `scrape_roster` / `submit_lineup` are
   `NotImplementedError` stubs — DOM not mapped yet.
9. **`auction.py`** — `watch_and_bid` is a **long-lived polling loop**
   (5s interval), not a single cron-triggered action: asta iniziale/riparazione
   on this site are live sessions, so the loop must be started shortly before
   the scheduled auction and left running for its duration. Session identity
   (`scrape_session_id`) lets `state.py` dedupe/reset role budgets across
   restarts. `scrape_session_id` / `scrape_current_listing` / `place_bid` /
   `is_session_over` are `NotImplementedError` stubs — same reason as above.

14. **`aste/`** — the auction harvester, and the reason `docs/fantalab/05` exists.
   `sse.py`/`reducer.py`/`reconstruct.py`/`registry.py`/`compare.py` are pure;
   `stream.py`/`transport.py`/`landing.py`/`loader.py`/`supervisor.py` are the
   I/O shell. **The database is never on the collection path** — a test walks the
   capture modules' imports and fails if any can reach `fantabot.db`, because an
   outage must cost catch-up time and never a record.
   **`scripts/*_aste_live.py` is the retired poller.** Kept as a fallback and as
   the thing `scripts/compare_collectors.py` measures against; it reads merged
   snapshots, so two raises inside one interval collapse into one. The shadow run
   of 2026-08-27 put numbers on that: same 23 rooms, 105 shared sales, and **224
   rungs the poller could not see.** Use `aste-collect`.


## Known unknowns — resolve before flipping `FANTABOT_AUTO_ACT=true`

- **Site DOM**: login form, roster/formazione page, asta iniziale room, asta
  di riparazione room. Map these by running `fantabot login`, then inspecting
  the live pages (Chrome DevTools MCP or manual devtools) — fill in the
  `NotImplementedError` bodies in `lineup.py` and `auction.py` with real
  selectors once mapped. Don't guess selectors from memory of "a typical
  fantacalcio site" — leghe.fantacalcio.it is a private-league product, not
  the public fantacalcio.it site, and its markup hasn't been inspected.
  **Before mapping more selectors, read `docs/leghe-api.md`** — the site
  actually runs on a separate JSON API (`apileague.fantacalcio.it`) with
  auth reverse-engineered and several read endpoints (league status, teams,
  roster settings) confirmed working. The bearer token it needs is now an
  encrypted row in `league_tokens`, reachable through
  `apileague.auth_headers(league_id, store=...)`, so read-side DOM scraping in
  `lineup.py` may be unnecessary — go straight to `httpx` calls for those.
  Lineup submission and auction bidding are still undocumented POST
  endpoints (see "Gaps" in that doc) — those two still need either a live
  Network capture during a real submit/bid, or the DOM path.
- **Asta mechanics**: whether leghe.fantacalcio.it's asta iniziale/riparazione
  is a live simultaneous-bidding room (needs the polling loop as built), a
  turn-based queue, or something else — confirm by watching one before
  trusting `auction.py`'s polling assumption.
- **Stats source**: still unchosen for `StatsSource` proper. News sentiment is
  now covered by `fantabot news-fetch` (see `docs/spec-news-sentiment.md`), which is a different
  thing: it is opinion and availability, not per-matchday projected scores.
- **Bearer token**: still a git-ignored file. The decision is made — encrypted
  in Postgres, written by a CLI login flow that re-authenticates on expiry —
  but it is the next phase, and `SPEC.md` is now that spec in full.
- **Mantra vs Classic**: the user plays **both**, one league each — and as of
  2026-08-26 we know which is which: **`3584692` (Legamiallerotaie) is Classic**
  (`sroles=1`, `minrl=[3,8,8,6]`, 25-man) and **`4103937` (Legamiallerotaie2) is
  Mantra** (`sroles=2`, `minrl=[2,28]`, 30-man). By elimination from the roster
  settings endpoint, not from field names — see `docs/leghe-api.md`. `models.Role`
  (P/D/C/A) and `VALID_FORMATIONS` (7 Classic tuples) are Classic-only, so
  `strategy.pick_starting_lineup` cannot field a Mantra XI at all — Mantra has 12
  role codes across 11 schemas on four lines. `data/mantra_schemi.json` is now on
  disk as that engine's input; the engine itself is a separate spec.
- **`mantra_compat.json` is thin**: it lists blocked pairs only for 4-1-4-1, from
  a 2024-25 season PDF. Plausible — "illegal even with the malus" is rare — but
  unverified in detail. Confirm before a Mantra lineup depends on it.

## Working rules

- `FANTABOT_AUTO_ACT` defaults to `false` — deliberate, matches mailwise's
  `AUTO_SEND` convention. Don't flip the default; the user opts in via `.env`
  after selectors are verified.
- `login.py`'s sign-in stays manual/headed — don't script credential entry
  without first confirming the login form has no captcha/2FA and getting
  explicit sign-off, since a scripted login is what gets accounts flagged. The
  same rule now covers **any** page interaction after sign-in: `login.py`
  navigates once and clicks nothing, and a test asserts the page's recorded
  call list is exactly `["goto"]`.
- **A bearer token is never printed, logged, `repr`'d or committed** — in any
  form, truncated or whole. `tests/test_token_secrecy.py` enforces it: no JWT
  literal in any tracked file, an AST walk over every print/log/raise argument
  under `tokens/`, and `decrypt(` confined to `tokens/crypto.py` and
  `tokens/store.py`. Never pass the encryption key on argv — `ps` shows it and
  the shell keeps it in history.
- `strategy.py` must stay pure (no Playwright, no network). It is no longer the
  only tested module — `agentkit/`, `news/`, `mantra_grid/` and
  `data_sources/news_sentiment.py`, `tokens/` and `apileague.py` all have suites,
  503 tests in total — but the
  reason it was testable is the reason they are: the decision logic has no I/O.
  Keep new logic in a pure module and the I/O in a thin shell around it.
- **The test suite makes zero agent calls and opens zero sockets.** Runners and
  sleepers are injected so the fan-out is testable with fakes. Keep it that way;
  a suite that queries is a suite nobody runs.
- Ruff: line length 100, target py311, same `select`/`ignore` as mailwise.
  `mypy --strict` on `src/fantabot` (tests excluded).
- **The database is the source of truth.** `data/`'s CSVs are the one-time seed
  and nothing reads them any more; the scrapers and `news-fetch` write to
  Postgres. Row counts in `data/README.md` are floors, not fixtures — the
  scrapers read a live site and it moves.
- Every importer and repository write is an **upsert**. A killed run is
  restarted, never repaired.

## Future: BAML upgrade path

`strategy.py`'s rules (role budget split, formation tie-break) are hand-tuned
heuristics, not learned/LLM-driven. Once a real stats source exists and the
heuristics prove too blunt (e.g. auction target prices need reasoning about
scarcity/form, not just a static split), consider a BAML function for
`target_price`/bid reasoning — following the pattern in `optimizer`,
`dietwise`, `clipcraft`. Don't add BAML now; there's no data to reason over
yet and it would be build-ahead-of-need.
