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
7. **`data_sources/__init__.py`** — `StatsSource` `Protocol`
   (`projected_scores`, `player_pool`, `target_price`). No implementation
   exists yet — user is still picking a stats/injuries/probable-lineup
   source. Implement one class per source under `data_sources/`, wire it into
   `lineup.run_once` / `auction.watch_and_bid`. Nothing else changes.
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
- **Asta mechanics**: whether leghe.fantacalcio.it's asta iniziale/riparazione
  is a live simultaneous-bidding room (needs the polling loop as built), a
  turn-based queue, or something else — confirm by watching one before
  trusting `auction.py`'s polling assumption.
- **Stats source**: user is choosing (mentioned wanting to search and report
  back). Once chosen, implement `StatsSource` under `data_sources/` — don't
  build a second one speculatively.

## Working rules

- `FANTABOT_AUTO_ACT` defaults to `false` — deliberate, matches mailwise's
  `AUTO_SEND` convention. Don't flip the default; the user opts in via `.env`
  after selectors are verified.
- `auth.py`'s login stays manual/headed — don't script credential entry
  without first confirming the login form has no captcha/2FA and getting
  explicit sign-off, since a scripted login is what gets accounts flagged.
- `strategy.py` must stay pure (no Playwright, no network) — it's the only
  module with a real test suite (`tests/test_strategy.py`) and that only
  works because it has no I/O.
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
