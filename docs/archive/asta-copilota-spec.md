# SPEC — Asta Copilota (advisory MVP)

Spec-driven development, phase 1. Written 2026-08-28 against
`tasks/archive/asta-design.md` (the architectural design) and the decisions pinned
with the user this session. This spec scopes the **advisory MVP** only; the deferred layers
(Monte-Carlo opponent sim, μ-pacing rigor, auto-bid) are named but out of scope here.

Guiding constraint from the user: **not all data is ready — start implementing the code and
the algorithm anyway.** The spec is therefore built around interfaces that let the engine be
written and tested today against partial data, with the richer estimators slotted in later.

---

## 1. Objective

A **live-connected advisory tool** that, during the user's Mantra asta, always holds the
**optimal completable 30-man roster** and re-plans as players are sold — telling the user which
players to chase and the walk-away price for each. It advises; the human clicks.

- **User:** the account owner, during the **asta iniziale/riparazione** of Mantra **lega
  4103937** (30-man, `sroles=2`, `minrl=[2,28]`, 11 schemi, legal-XI required).
- **Objective function:** maximize expected **30-man season fantapoints** of the whole roster,
  penalized by risk — `maximize Σμ − λ·wᵀΣw` — subject to budget (500), the Mantra role
  composition, and **the roster must field a legal XI across the 11 schemi** (that constraint
  *is* L1). `λ` is the risk knob: high = diversify (season-title), low = allow a controlled
  same-team stack (comeback). Same-team fantapoints are positively correlated, so `Σ` carries a
  team-covariance term — the diversification lever, validated against the finance literature.
- **Rolling re-optimization:** on each live assignment, re-solve for the best roster reachable
  with remaining budget + remaining pool; when a target is lost, fall to the next-best. Greedy &
  deterministic in the MVP.
- **Real-time opponent tracking:** from the buyer-named feed, reconstruct each rival's roster,
  spend, remaining budget, and role concentration live.
- **Safe drain (group dynamics):** the LLM decides *when* a player is one we don't want and
  conditions suit pushing rivals to overpay, and *suggests* a **capped** push — cap strictly
  below the price at which we would risk owning him. Suggest-only in the MVP.

**Non-goals (MVP):** placing bids (advisory only), the Classic system, the Monte-Carlo simulator,
μ-pacing for the contested top-40, and reimplementing the FantaLab room.

## 2. Commands

New Typer subcommands, registered like the existing ones. All are read/advisory; none write to
FantaLab.

| Command | Does | Reads | Writes |
|---|---|---|---|
| `fantabot asta-legality --rosa <ids>` | Debug L1: which of the 11 schemi a given rosa can field, and each player's marginal legality. | `data/mantra_*.json`, `quotazioni` | nothing |
| `fantabot asta-optimize [--owned <ids>] [--budget N] [--lam λ]` | Compute + print the current **optimal reachable 30-man** roster + the ranked fallback chain, given what we already own and our remaining budget. Offline/dry — the engine, runnable without a live room. | DB (value inputs, ladders) | nothing |
| `fantabot asta-live [--league 4103937] [--lam λ]` | Connect to the room (read), maintain live state, re-optimize on each assignment, and render the advisory surface: optimal roster, next targets + walk-away numbers, opponent tracker, drain suggestions. | live room feed + DB | nothing (advisory) |

`--lam` (λ) defaults to a season-title value; overridable per run. `asta-optimize` is the
testable core; `asta-live` is the thin I/O shell around it.

## 3. Project structure

New package `src/fantabot/asta_engine/`. **Pure decision logic and I/O are split**, as the repo
enforces elsewhere (`strategy.py` pure, `db/` all I/O, news `models/prompt/pool` pure vs
`store/pipeline` I/O).

```
src/fantabot/asta_engine/
  roles.py         # PURE. Mantra 12-code role type (Por/Dd/Ds/Dc/B/E/M/C/T/W/A/Pc),
                   #       schema + slot value types, case-folding DB(UPPER)↔JSON(Mixed).
  legality.py      # PURE. L1 bipartite matcher: fieldable_schemi(rosa) -> frozenset,
                   #       marginal_legality(rosa, player). Carries -1* as its own state,
                   #       never folded into -1. Loads mantra_schemi/compat via mantra_grid loaders.
  value.py         # ValueModel Protocol -> per-player (mean, variance). PURE interface.
                   #   NaiveValueModel (PURE, v1): point value from target_price/qi + flat variance.
                   #   SkfolioValueModel (later): Black-Litterman posterior + shrinkage/factor Σ.
  prices.py        # I/O shell. Expected clearing price per player from the loaded Mantra ladders.
  state.py         # PURE. AstaState (our rosa, budget, taken players, opponents), frozen + updates.
  optimizer.py     # PURE. optimize_roster(state, value, prices, lam) -> Roster + fallbacks.
                   #       ILP (or greedy) max Σμ − λ·wᵀΣw s.t. budget + minrl + L1 legal-XI.
  reservation.py   # PURE. Walk-away per target = marginal value into the current optimal roster,
                   #       budget-feasible. (μ-pacing is a later upgrade, same signature.)
  opponents.py     # PURE. Reconstruct rival roster/spend/budget/role-concentration from events.
  drain.py         # PURE. Given state, propose safe capped-push candidates (cap guarantees no
                   #       misfire). The LLM decides WHEN; this bounds HOW HIGH.
  live.py          # I/O shell. Subscribe to the room feed -> events -> re-opt -> advisory render.
  __init__.py
```

CLI commands live in `cli.py` (or an `asta_engine/cli.py` registered like `aste/cli.py`). The
skfolio value impl, `prices.py`, and `live.py` are the only modules that touch I/O / external
libs; everything else is importable and testable with no DB, no sockets, no skfolio.

**Data sources (already in the DB / on disk):** `data/mantra_schemi.json`,
`data/mantra_compat.json` (1,452 cells, 120 `-1*`), `quotazioni.ruoli_codice` (548 Mantra
2026/27), `voti`/`bonus_malus` (4 seasons), `target_price`, the Mantra `asta_assignment` ladders,
and `player_sentiment` (populating).

## 4. Code style

Inherits the repo conventions (see `CLAUDE.md`):

- **Ruff** line length 100, target py311; **`mypy --strict`** on `src/fantabot` (tests excluded).
- **Frozen dataclasses** for value types (`AstaState`, `Roster`, `PlayerValue`, …), like
  `models.py`.
- **English** code + docstrings; **Italian** only for user-facing/LLM-prompt surface.
- **Pure decision logic, thin I/O shell.** No Playwright/network/DB import in the pure modules; a
  test enforces the boundary (as `agentkit`/`aste` already do).
- `skfolio>=1.0` added to `pyproject.toml` deps (used only in `value.py`'s skfolio impl).
- Reasons-at-the-point-of-decision comments, matching the surrounding code's density.

## 5. Testing strategy

- **Default `pytest` tier stays zero-sockets.** The live layer is tested with an **injected fake
  event stream**, mirroring the news pipeline's injected runner — no real room, no sockets.
- **L1 legality** — unit tests against the artefacts: a known rosa fields exactly the expected
  schemi; `-1*` cells are refused at submission and never counted as `-1`; the 5 prose Mantra
  rules (B/Dd/Ds→Dc, Dd↔Ds, E↔M, W→T) map to refusal in every place the slot exists.
- **Optimizer** — invariants on every output: within budget, satisfies `minrl`, fields a legal XI
  (via L1), exactly 30 players; the fallback chain is distinct and monotone non-increasing in
  value; a golden roster on a fixed fixture.
- **ValueModel** — `NaiveValueModel` deterministic on fixtures; the skfolio impl tested on a tiny
  synthetic panel (`make_synthetic_*`) so it needs no live DB.
- **Opponents / reservation / drain** — pure, fixture-driven; drain's cap is asserted to be
  strictly below the misfire threshold in every proposed push.
- **Rolling re-opt** — feed a scripted sequence of assignment events, assert the roster re-plans
  correctly when a target is taken.

## 6. Boundaries

**Always**
- Advisory only in the MVP: compute and display; the human places every bid.
- Keep `-1*` distinct from `-1` — collapsing it builds lineups the platform rejects.
- Decision logic pure and tested; the deterministic walk-away number renders regardless of the
  network (no screen blocks on a socket).
- Report uncertainty rather than hide it — especially where `E[presenze]` is absent (newcomers,
  January arrivals): shrink to prior and surface the wider band.
- Upserts for any persistence; a killed run is restarted, never repaired. `mypy`/`ruff`/tests
  green before a task is done.

**Ask first**
- Enabling auto-bid, touching `FANTABOT_AUTO_ACT`, or using the undocumented bid-POST endpoint.
- Any write/interaction to the live FantaLab room.
- Changing the league-settings assumptions (30-man, `minrl=[2,28]`, budget 500, chiamata mode).
- Adopting the skfolio value impl as the default over the naive one (a quality decision).

**Never**
- Place a bid automatically in the MVP.
- Propose a drain push whose cap could leave us owning a player we don't want (no misfire — the
  cap is a hard guarantee, not a target).
- Flip the `FANTABOT_AUTO_ACT` default to true.
- Fabricate a value for a player with no history — mark it shrink-to-prior with explicit
  uncertainty.
- Reimplement the FantaLab room, or depend on a paid subscription.

## Data readiness (why we can start now)

| Input | State | How the spec handles it |
|---|---|---|
| L1 inputs (schemi, compat, roles) | **ready + verified** | build L1 first |
| `voti`/`bonus_malus` (4 seasons), `target_price`, `quotazioni` | **ready** | feed `NaiveValueModel` v1 |
| Mantra ladders (`asta_assignment`) | **ready (partial, 170 auctions)** | `prices.py` v1 expected prices |
| `player_sentiment` | **populating now** | BL views once the run lands; naive value doesn't need it |
| `E[presenze]` denominator | **missing** | absorbed into the shrinkage prior; uncertainty reported, not hidden |
| Classic fact data, full 1.19 GB landing file | **missing** | out of MVP scope (Mantra only); re-harvest later |

## Build order (MVP, Mantra)

1. **L1 legality matcher** (`roles.py`, `legality.py`) — pure, inputs ready, blocks everything.
2. **ValueModel interface + `NaiveValueModel`** (`value.py`) + **`prices.py`** — mean/variance +
   expected price per player from data in hand.
3. **Roster optimizer** (`optimizer.py`, `state.py`) — the core; `asta-optimize` command.
4. **Live room read** (`live.py`) — confirm the own-room feed; emit assignment events.
5. **Rolling greedy re-opt + reservation price** (`reservation.py`) — advisory numbers.
6. **Advisory surface + opponent tracker** (`opponents.py`, `asta-live`).
7. **LLM helpers** — state-entry parser + `drain.py` safe-drain suggester.

Deferred (post-MVP): `SkfolioValueModel` (BL + covariance) as the default value layer, L4
Monte-Carlo simulator, μ-pacing for the top-40, auto-bid execution.
