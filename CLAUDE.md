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
pip install -e ".[dev]"
playwright install chromium
fantabot auth                # one-time interactive login → data/storage_state.json
fantabot config-check
fantabot lineup-submit

fantabot news-fetch --limit 5           # smoke test, queries but writes nothing
fantabot news-fetch --write             # the weekly run: all 523, both leagues
fantabot mantra-grid --write            # one-off, collects the Mantra schema grid

pytest
ruff check src tests
mypy
```

## Architecture

1. **`config.py`** — `pydantic-settings` reading `.env`. `fantabot_auto_act`
   defaults to `false` — the same "safe by default" pattern as mailwise's
   `AUTO_SEND`: every write action (submit lineup, place bid) is gated behind
   it and logs a dry-run message instead when it's off.
2. **`browser.py`** — two Playwright context managers. `context()` reuses
   `data/storage_state.json` (headless, for cron); `interactive_login_context()`
   is headed and only used by `auth.py`.
3. **`auth.py`** — opens a real headed Chrome window and waits for the human
   to log in manually (including any captcha/2FA), then persists
   cookies/localStorage. Deliberately not scripted — matches mailwise's rule
   that the interactive OAuth-equivalent flow never runs inside the
   unattended loop. If leghe.fantacalcio.it's login form turns out to be
   simple (no captcha), this can be scripted later; don't do it speculatively.
4. **`state.py`** — JSON persistence (`data/state.json`) of
   `last_lineup_matchday` and `last_auction_session_id` +
   `processed_bids`, so a cron restart doesn't resubmit a lineup or reset an
   auction's budget tracking mid-session.
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
   `news_sentiment.py` reads the sentiment time-series produced by
   `fantabot news-fetch`: `latest`, `trailing` (silent rows excluded), and
   `drifted()` for players whose frozen Mantra tag no longer describes them.
   Not wired into `strategy.py` yet — that is a later phase.
10. **`agentkit/`** — the Claude Agent SDK plumbing, shared by every command
   that queries. `env.py` closes both credential leak vectors (`os.environ`
   *and* `ClaudeAgentOptions.env`, since `session_resume.py:356` reads either),
   `options.py` builds the options, `runner.py` holds **the one message loop**.
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

## Known unknowns — resolve before flipping `FANTABOT_AUTO_ACT=true`

- **Site DOM**: login form, roster/formazione page, asta iniziale room, asta
  di riparazione room. Map these by running `fantabot auth`, then inspecting
  the live pages (Chrome DevTools MCP or manual devtools) — fill in the
  `NotImplementedError` bodies in `lineup.py` and `auction.py` with real
  selectors once mapped. Don't guess selectors from memory of "a typical
  fantacalcio site" — leghe.fantacalcio.it is a private-league product, not
  the public fantacalcio.it site, and its markup hasn't been inspected.
  **Before mapping more selectors, read `docs/leghe-api.md`** — the site
  actually runs on a separate JSON API (`apileague.fantacalcio.it`) with
  auth reverse-engineered and several read endpoints (league status, teams,
  roster settings) confirmed working. The bearer token it needs is already
  saved by `auth.py`'s `storage_state()` call, so read-side DOM scraping in
  `lineup.py` may be unnecessary — go straight to `httpx` calls for those.
  Lineup submission and auction bidding are still undocumented POST
  endpoints (see "Gaps" in that doc) — those two still need either a live
  Network capture during a real submit/bid, or the DOM path.
- **Asta mechanics**: whether leghe.fantacalcio.it's asta iniziale/riparazione
  is a live simultaneous-bidding room (needs the polling loop as built), a
  turn-based queue, or something else — confirm by watching one before
  trusting `auction.py`'s polling assumption.
- **Stats source**: still unchosen for `StatsSource` proper. News sentiment is
  now covered by `fantabot news-fetch` (see `SPEC.md`), which is a different
  thing: it is opinion and availability, not per-matchday projected scores.
- **Mantra vs Classic**: the user plays **both**, one league each. `models.Role`
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
- `auth.py`'s login stays manual/headed — don't script credential entry
  without first confirming the login form has no captcha/2FA and getting
  explicit sign-off, since a scripted login is what gets accounts flagged.
- `strategy.py` must stay pure (no Playwright, no network). It is no longer the
  only tested module — `agentkit/`, `news/`, `mantra_grid/` and
  `data_sources/news_sentiment.py` all have suites, 146 tests in total — but the
  reason it was testable is the reason they are: the decision logic has no I/O.
  Keep new logic in a pure module and the I/O in a thin shell around it.
- **The test suite makes zero agent calls and opens zero sockets.** Runners and
  sleepers are injected so the fan-out is testable with fakes. Keep it that way;
  a suite that queries is a suite nobody runs.
- Ruff: line length 100, target py311, same `select`/`ignore` as mailwise.
  `mypy --strict` on `src/fantabot` (tests excluded).

## Future: BAML upgrade path

`strategy.py`'s rules (role budget split, formation tie-break) are hand-tuned
heuristics, not learned/LLM-driven. Once a real stats source exists and the
heuristics prove too blunt (e.g. auction target prices need reasoning about
scarcity/form, not just a static split), consider a BAML function for
`target_price`/bid reasoning — following the pattern in `optimizer`,
`dietwise`, `clipcraft`. Don't add BAML now; there's no data to reason over
yet and it would be build-ahead-of-need.
