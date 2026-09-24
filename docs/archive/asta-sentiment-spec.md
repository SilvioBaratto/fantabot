# Spec: Player sentiment in the asta engine value layer

Status: **implemented and reviewed, 2026-08-29.** Plan and task list, archived:
`tasks/archive/asta-sentiment-plan.md` ·
`tasks/archive/asta-sentiment-todo.md`.

Two things in this document were **wrong and are corrected in place**, because a spec that
records a formula the code does not run is worse than no spec: `disponibilita` needed its own
floor (it was a veto, see L1), and the pool-mean normalization must hold uncovered players
*out* rather than divide them too. The reasoning for both is in L1.

Original status: **draft, awaiting review.** Supersedes nothing; extends the advisory engine
described in `tasks/archive/asta-copilota-plan.md`
and consumes the feed specified in [`docs/archive/news-sentiment-spec.md`](news-sentiment-spec.md).

## Objective

`fantabot news-fetch` has produced a full round of readings — 548 rows for `2026/27`,
one per player in the Mantra listone — and **nothing consumes them.** A grep for
`player_sentiment` / `news_sentiment` across `asta_engine/` and `strategy.py` returns
zero hits. The engine plans a 30-man rosa on `quotazioni.fvm` alone: the market's
fantavalore, a price, not a projection.

The gap that costs credits is playing time. `fvm` prices talent and reputation; it
prices a nailed-on starter and a talented bench player far closer together than their
fantasy output will land. The sentiment feed measures exactly that difference, and
measures it weekly:

| Field | Pool mean | Pool σ | What it separates |
|---|---|---|---|
| `titolarita` | 0.418 | 0.311 | starter vs squad player |
| `disponibilita` | 0.832 | 0.335 | injured/suspended vs fit |
| `sentiment` | −0.057 | 0.559 | overall outlook |
| `confidenza` | 0.725 | — | how much evidence stands behind the row |
| `deriva_ruolo` | — | — | 51 players whose frozen Mantra tag is stale |

**User:** the operator running `fantabot asta-optimize` / `asta-live` before and during
the asta iniziale, in both leagues (`3584692` Classic, `4103937` Mantra).

**Success looks like:** a high-`fvm` bench player stops being drafted; a player with no
recent coverage is held with a wider band rather than a fabricated point estimate; and a
drifted role tag is *surfaced*, never silently used to relax a legality check.

### Non-goals

- Replacing `fvm` as the value signal. Sentiment **adjusts** it. The Black-Litterman
  posterior (`SkfolioValueModel`) remains the named successor and is out of scope.
- Fitting weights from data. There is one `data_run`; every constant here is a
  declared prior, tunable by flag, and labelled as such.
- Auto-bidding. This spec is read-only planning; `FANTABOT_AUTO_ACT` is untouched.

## The four integration points

Sentiment enters the engine in four places. They are independent and land in this order.

### L1 — Value mean: `fvm × sentiment`

The layered model: a multiplicative **gate** (will he be on the pitch?) times an additive
**tilt** (how well will he do when he is?). The two are kept separate because the fields
mean different things — `disponibilita`/`titolarita` are probabilities, `forma`/`mercato`
are quality adjustments — and blending them into one weighted sum treats a probability as
a score.

```
avail      = DISP_FLOOR + (1 - DISP_FLOOR) * disponibilita     # DISP_FLOOR = 0.50
start      = TIT_FLOOR  + (1 - TIT_FLOOR)  * titolarita        # TIT_FLOOR  = 0.40
raw_gate   = avail * start
raw_tilt   = w_s*sentiment + w_f*forma + w_m*mercato + w_r*rigorista + w_p*piazzati
raw_effect = raw_gate * (1 + K * raw_tilt)

# 1. age the confidence: the feed is weekly, so one half-life is one missed run
eff_conf   = confidenza * 0.5 ** (age_days / HALF_LIFE_DAYS)   # HALF_LIFE_DAYS = 7

# 2. shrink toward "no opinion" by that aged confidence
shrunk     = 1 + eff_conf * (raw_effect - 1)

# 3. renormalize so the POOL MEAN of the effect is exactly 1.0 -- over the COVERED
#    players only. An uncovered player is held OUT of the mean and comes out at exactly
#    NEUTRAL; dividing him by it too made "no evidence" a buy signal (see below).
covered    = {p for p in pool if p has a row and confidenza > 0}
effect     = shrunk / mean(shrunk over covered)      for a covered player
effect     = 1.0                                     otherwise

mean       = fvm * effect
```

**Both floors are measured, not guessed — see Task 1's findings below.**

**`TIT_FLOOR = 0.40`** — a squad player is not worthless over a season; injuries ahead of
him and a tactical change both convert him into a starter. `titolarita` is explicitly "next
matchday", so a hard zero over-reads a one-week question. The value comes from the measured
overlap: `fvm` and `titolarita` share **R² ≈ 0.37–0.43** of their rank variance, so roughly
40% of what the gate would "discover" is already in the price. The mapping is deliberately
simple — *the fraction the market already prices is the fraction of value the gate refuses
to strip away.*

**`DISP_FLOOR = 0.50`** — and this floor exists because Task 1 found the spec's original
formula was wrong. `disponibilita` entered as a bare multiplier, so `disponibilita == 0`
forced `raw_gate == 0`, `mean == 0`, and a player the optimizer can never select at any
price. That is a hard veto wearing a soft weight's clothes. It fires on real rows: on the
2026-08-28 run, Yildiz (`fvm` 150, metatarsal fracture, 3 sources, `confidenza` 0.95) came
out at **×0.07** — effectively deleted from the pool.

The category error is the same one `TIT_FLOOR` already guards against. `disponibilita` asks
"is he available *now*"; the asta buys a **season**. A metatarsal fracture costs roughly
8–10 weeks of a 38-week season, so the honest discount is large but nowhere near total.
`DISP_FLOOR = 0.50` puts Yildiz at ×0.33 — heavily marked down, still draftable at the right
price, which is what an auction is for. Note this floor is *not* derived from the R² mapping:
`disponibilita`'s overlap with `fvm` is essentially nil (r = +0.077), so the market genuinely
does not price it. The floor corrects the **horizon**, not double-counting.

**Step 2 is load-bearing, not cosmetic.** The objective is `sum(mu) − lam·Var`. `Var` is
on the `base_variance` scale and does not move with `mu`. Scaling every mean by the raw
gate (pool mean ≈ 0.5) would silently double `lam`'s effective strength and re-tune the
risk knob behind the operator's back. Renormalizing to a pool mean of 1.0 keeps `lam`
meaning what it meant before this change, and confines sentiment's effect to *relative*
ordering — which is the only thing it is entitled to change.

**It also prevents double-counting.** The market already discounts known backups, so some
of the playing-time signal is inside `fvm` already. A pool-mean-preserving effect moves a
player only relative to what the market already expected of the average player.

**Silent rows.** `confidenza == 0` → `shrunk == 1.0` → no adjustment, by construction. The
`news_sentiment` invariant (a silent row is not a neutral row) is satisfied by the algebra,
not by a special case. A player absent from the feed entirely is likewise `effect = 1.0`.

**Corrected 2026-08-29.** The paragraph above was true of `shrunk` and false of `effect`, and
the difference is the whole bug. Setting an uncovered player to 1.0 and then dividing him by
the pool mean *does* move him: covered players average well below 1.0, because most of a
listone is not nailed-on starters, so the divisor is under 1 and having no evidence
multiplied a player **up**. On the real 2026-08-28 pool the one silent row came out at
**×1.368** — a 37% premium for being the player nobody wrote about — and the same premium
applied to every listone id the feed had not reached, growing as the two diverged. That
inverts the feature.

Holding uncovered players out of the mean keeps *both* invariants at once: the covered
subset is centred on 1.0, and since each held-out player contributes exactly 1.0, the mean
over the whole pool is still exactly 1.0, so `lam` is protected as before. Verified at
`1.0000000000` on the live listone.

The suite could not see it. Every test guarding the identity used a pool whose values
already averaged 1.0 — which makes the normalization a no-op, and the guard vacuous.

**Staleness rides on the same lever.** A dated reading is a less trustworthy reading, which
is what `confidenza` already means — so age decays it rather than becoming a second concept
with its own threshold. `HALF_LIFE_DAYS = 7` because `news-fetch` is weekly: one half-life is
exactly one missed run. The decay is continuous, so there is no cliff to argue about, and it
degrades to precisely the honest fallback — at high age `eff_conf → 0`, `effect → 1.0`, and
the engine is back on `fvm` alone. A hard refusal above N days was rejected for the opposite
reason: its fallback is `--no-sentiment`, which throws away a 15-day-old reading that is
worse than fresh but clearly better than nothing.

### L2 — Variance from `confidenza`

Variance is flat today: `4.0`, or `16.0` for a player with no observed sale. That makes
`lam` nearly inert — every player carries the same band, so the mean-variance objective
degenerates to maximizing the mean. `confidenza` is the honest uncertainty signal.

```
variance = base * (1 + A * (1 - confidenza) + B * deriva_ruolo)
```

with `no_history_variance` still dominating for a player the market never priced. A player
with thin coverage keeps his mean and widens his band — the same discipline
`NaiveValueModel` already applies to the no-history case.

### L3 — Role drift: **fail-closed**

This is the point where the obvious design is wrong, and the spec records why.

The tempting move is to union the observed roles into the pool: Yildiz is tagged `A` but
played `T`, so let the optimizer field him at `T`. **This must not be built.**
`rules/sistema-mantra.md:34` is explicit — roles are assigned in late July and *are not
revisited for the rest of the year*. The platform enforces its own frozen tag at lineup
submission. A pool widened by observed roles produces rosters that satisfy our legality
matrix and that the platform then rejects. `mantra_compat.json`'s `-1*` cells exist to
encode exactly this distinction, and CLAUDE.md already records what collapsing them costs.

So: **legality always reads `quotazioni.ruoli_codice`, and drift never expands the pool.**

What drift legitimately means for value: a player tagged `A` who is actually being played
as `W` will still be *fielded* as an `A`, but his output profile is a winger's, not a
centre-forward's — fewer goals than his tag implies. Real drift examples in the current
data: `Deiola` tagged `M;C`, observed `DC`; `De Ketelaere` tagged `A`, observed `W`;
`Fazzini` tagged `C;T`, observed `T;W`. That is role-risk, and role-risk is variance.

Drift therefore lands as: the `B * deriva_ruolo` term in L2, plus a **report column** —
a visible `⚠ tag A / played W` annotation on the roster and advisory output. Surfaced,
never silently applied.

### L4 — Lineup engine (`strategy.py`) — **DEFERRED, not in this phase**

`titolarita` → weight on `projected_score`; `disponibilita` below a threshold → force
`is_available = False`.

**Scoping honesty:** `pick_starting_lineup` is pure and receives *already-scored*
`RosterSlot`s. Sentiment must therefore enter upstream, where `ScoredPlayer` is built —
which is `lineup.py`, whose `scrape_roster` / `scrape_matchday_info` are still
`NotImplementedError`. L4 can deliver a **pure, tested scoring adjuster**; it cannot be
verified end-to-end against a real matchday until those stubs are filled. This task ships
the pure function and its tests and stops there, rather than pretending at a completion it
cannot demonstrate.

Note also that `pick_starting_lineup` is Classic-only (`Role` P/D/C/A, `VALID_FORMATIONS`),
so L4 serves league `3584692`. The Mantra XI engine is separate and unbuilt.

**Decision (2026-08-28): deferred out of this phase.** The asta is the thing with a
deadline; L4 serves the weekly lineup, which is a later concern. Deferring also means the
adjuster gets designed against a real `scrape_roster` return shape rather than an assumed
one. L1–L3 ship without it, and nothing in them depends on it.

## Tech Stack

Unchanged. Python ≥3.11, SQLAlchemy 2.x (sync), Typer + Rich, Postgres 16, pytest,
`mypy --strict`, ruff (line length 100, target py311). No new dependencies. **No BAML** —
`CLAUDE.md`'s upgrade path is explicit that it is build-ahead-of-need until there is data
to reason over, and one `data_run` is not that data.

## Commands

```bash
conda activate fanta

# the engine, with sentiment on by default once this lands
fantabot asta-optimize --budget 500
fantabot asta-optimize --budget 500 --no-sentiment          # ablation: the current behaviour
fantabot asta-optimize --budget 500 --sentiment-run 2026-08-28
fantabot asta-optimize --budget 500 --lam 0.3 --fallbacks 3
fantabot asta-optimize --budget 500 --tilt-k 0              # gate only, tilt inert

# asta-live: sentiment ON by default too, re-read each advisory cycle
fantabot asta-live --replay events.jsonl --team <id> --budget 500
fantabot asta-live --replay events.jsonl --team <id> --no-sentiment
fantabot asta-legality --rosa "<ids>"

# verification
pytest                       # default tier: zero sockets, db tests deselected
pytest -m db                 # integration tier, needs docker compose up -d
pytest tests/test_asta_sentiment.py -v
ruff check src tests
mypy
```

`--no-sentiment` is not a courtesy flag. It is the ablation control: the only way to show
what this change actually did to a roster is to build the same roster without it.

## Project Structure

```
src/fantabot/asta_engine/
  sentiment.py      NEW — pure. The gate/tilt/shrink/normalize algebra and the
                    variance modulation. Imports no SQLAlchemy, no I/O.
  value.py          extended — NaiveValueModel gains per-player variance, so L2
                    can widen one player's band without widening everyone's.
  report.py         extended — build_value() takes an optional sentiment mapping;
                    format_roster() gains the drift column.
  cli.py            extended — fetches sentiment rows; adds --sentiment/--no-sentiment,
                    --sentiment-run and --tilt-k to asta-optimize AND asta-live.
  optimizer.py      UNCHANGED. It reads ValueModel; it must not learn about sentiment.
  legality.py       UNCHANGED. Fail-closed on the frozen tag (L3).

src/fantabot/data_sources/
  news_sentiment.py extended — a bulk `all_latest()` for the whole pool. The existing
                    per-player `latest()` would be 548 queries.

src/fantabot/db/repositories/
  sentiment.py      extended — the bulk read behind all_latest().

src/fantabot/
  strategy.py       NOT TOUCHED — L4 is deferred out of this phase.

tests/
  test_asta_sentiment.py         NEW — the pure algebra: gate, tilt, shrink, normalize.
  test_asta_sentiment_wiring.py  NEW — build_value with/without sentiment; drift column.
  test_asta_value.py             extended — per-player variance.
  test_strategy.py               extended (L4).
  test_db_boundary.py            extended — sentiment.py imports no I/O.

docs/archive/asta-sentiment-spec.md      this file
tasks/plan.md, tasks/todo.md     Phase 2 (per CLAUDE.md, archive the current pair first)
```

## Code Style

Pure module, frozen dataclasses, keyword-only tunables with declared defaults, a docstring
that says *why* the constant is what it is. Matching `value.py` and `prices.py`:

```python
@dataclass(frozen=True)
class SentimentWeights:
    """The priors. Not fitted — there is one data_run — so every one is a declared guess.

    ``tit_floor`` is why a squad player keeps value and ``disp_floor`` is why an injured
    one does: both fields ask about the *next* matchday, and an asta is a season-long bet.
    Without ``disp_floor`` an injured player's mean is exactly 0 and he is unbuyable at any
    price — a veto, not a weight. ``k`` is deliberately small; the tilt
    corrects the gate, it does not overrule it. ``half_life_days`` is 7 because the feed
    is weekly, so one half-life is exactly one missed run.
    """

    tit_floor: float = 0.40
    disp_floor: float = 0.50
    k: float = 0.25
    half_life_days: float = 7.0
    w_sentiment: float = 0.40
    w_forma: float = 0.20
    w_mercato: float = 0.20
    w_rigorista: float = 0.15
    w_piazzati: float = 0.05


def effect_by_id(
    rows: Mapping[str, SentimentRow],
    pool_ids: Iterable[str],
    *,
    weights: SentimentWeights = SentimentWeights(),
) -> dict[str, float]:
    """Per-player multiplier on ``fvm``, renormalized to a pool mean of 1.0. Pure.

    A player missing from ``rows`` — or carrying ``confidenza == 0``, which means "no
    coverage was found", not "neutral" — gets exactly 1.0 before normalization. The
    normalization is what keeps ``lam`` meaning what it meant: see the spec, L1 step 2.
    """
```

Rules carried over unchanged: `strategy.py` and every `asta_engine` pure module stay free
of Playwright, network and SQLAlchemy; the I/O lives in `cli.py`; Italian stays only in the
`news/` prompt surface, and identifiers here are English.

## Testing Strategy

pytest, tests flat in `tests/`, default tier opens **zero sockets** and makes zero agent
calls. The `db` marker is the only tier that touches Postgres.

| Level | Covers | Where |
|---|---|---|
| Pure unit | gate/tilt/shrink/normalize algebra, boundary values, the silent-row identity | `test_asta_sentiment.py` |
| Wiring | `build_value` with and without sentiment; drift column renders | `test_asta_sentiment_wiring.py` |
| Property | pool-mean of `effect` == 1.0 for any input; `effect > 0` always | `test_asta_sentiment.py` |
| Decay | a reading aged one half-life carries half the adjustment; far past it, `effect → 1.0` | `test_asta_sentiment.py` |
| Regression | `--no-sentiment` reproduces today's roster **exactly** | `test_asta_sentiment_wiring.py` |
| Boundary | `sentiment.py` imports nothing from `fantabot.db` | `test_db_boundary.py` |
| Integration | bulk `all_latest()` against real rows | `test_sentiment_repo.py` (`db` tier) |

Three tests are load-bearing and named here because they encode the decisions above:

1. **The silent-row identity.** `confidenza == 0` ⟹ `effect == 1.0` exactly, before
   normalization. Guards the `news_sentiment` invariant against a future refactor.
2. **The normalization property.** For any non-empty pool, `mean(effect) == 1.0` within
   float tolerance. This is what protects `lam`.
3. **Drift never expands the pool.** Feed a player tagged `A` and observed `W`; assert
   `build_pool` still yields `{A}` and that `fieldable_schemi` is unchanged. This is the
   L3 fail-closed rule as an executable assertion.

Coverage expectation: every branch of the pure module. No coverage target on the I/O shell.

## Boundaries

**Always**
- Keep `sentiment.py` pure; `pytest`, `ruff check src tests` and `mypy` green before commit.
- Exclude `confidenza == 0` rows from every average — a silent row is not a neutral row.
- Renormalize the effect to a pool mean of 1.0 before it reaches the objective.
- Read legality from `quotazioni.ruoli_codice`, always.
- Declare every constant as a prior with a stated reason, and expose it as a flag.

**Ask first**
- Changing `DEFAULT_SAME_TEAM_RHO`, `base_variance` or `lam` defaults — they are the
  operator's calibration, and moving them silently re-tunes decisions already reviewed.
- Any schema change or Alembic migration (none is anticipated; the feed is already stored).
- Making sentiment on-by-default in `asta-live` as well as `asta-optimize`.
- Adding a dependency, including anything that would pull in skfolio early.

**Never**
- Let `deriva_ruolo` or `ruolo_campo` widen the pool or relax a legality check.
- Clamp an out-of-range model value — the schema treats it as a failed query, and a clamp
  hides a misread prompt behind a plausible number that then sets a bid.
- Fabricate a reading for a player with no coverage; `effect = 1.0` is the honest answer.
- Print, log or commit a bearer token (the standing repo rule, unchanged here).
- Flip `FANTABOT_AUTO_ACT`.

## Success Criteria

- [ ] `fantabot asta-optimize --budget 500` and `--no-sentiment` both return a legal,
      budget-feasible 30-man rosa, and the two differ.
- [ ] `--no-sentiment` reproduces the pre-change roster **exactly**, asserted by test.
- [ ] For every non-empty pool, `mean(effect) == 1.0` (±1e-9), asserted by test.
- [ ] A player with `confidenza == 0` and a player absent from the feed both receive
      `effect == 1.0`, asserted by test.
- [ ] **No player's `effect` is ever 0.** `min(effect) > 0` across the whole real pool,
      asserted by test — an injured star must be markable-down, never unbuyable.
- [ ] At least one player currently drafted on `fvm` alone with `titolarita < 0.2` is no
      longer in the optimal roster — the concrete thing this change exists to do.
- [ ] The drift column renders for all 51 `deriva_ruolo > 0` players, and
      `fieldable_schemi` output is byte-identical with and without sentiment.
- [ ] `variance` is no longer constant across the pool; `lam` visibly changes the roster.
- [ ] `--tilt-k 0` reproduces the gate-only roster exactly, so the tilt is provably inert
      at zero and reversible by flag.
- [ ] A reading aged `HALF_LIFE_DAYS` carries half the adjustment of a same-day one, and a
      reading aged far past it converges to `effect == 1.0` — asserted by test.
- [ ] `asta-live` applies sentiment by default and re-reads it each advisory cycle, so a
      row written mid-session is visible to the next cycle.
- [ ] Full `pytest` green, zero sockets. `pytest -m db` green. `ruff` + `mypy --strict` clean.
- [ ] `tasks/plan.md` / `tasks/todo.md` archived per CLAUDE.md before this phase's pair
      is written, with the superseded spec's links repointed in the same commit.

## Decisions and Open Questions

All five resolved 2026-08-28. Recorded here rather than deleted: the reasoning is the part
worth keeping.

1. **The tilt ships in full, `k` tunable.** All five weights at the priors above, `K = 0.25`
   default, exposed as `--tilt-k`. Accepted with eyes open: five constants, one `data_run`,
   zero fits. The asta is imminent and this is the most informed model available; `--tilt-k 0`
   is the escape hatch, and refitting after ~4 runs is named in Deferred.
2. **`fvm` ↔ `titolarita` overlap — MEASURED, Task 1 complete (2026-08-28).**

   | Measure | Value | Reading |
   |---|---|---|
   | `fvm ~ titolarita`, Pearson | r = +0.373, R² = 0.139 | misleading — `fvm` is hard right-skewed (mean 25.8, median 14, max 414), so a handful of stars dominate the raw fit |
   | `fvm ~ titolarita`, **Spearman** | ρ = +0.612, **R² = 0.374** | the robust number, and the one `TIT_FLOOR` is set from |
   | `log1p(fvm) ~ titolarita` | R² = 0.362 | agrees with Spearman, confirming skew was the whole gap |
   | `log1p(fvm) ~ titolarita + disponibilita` | R² = 0.429 | both fields together |
   | `fvm ~ disponibilita` | r = +0.077, R² = 0.006 | the market does **not** price availability at all |

   n = 547 (548 joined rows, 1 silent row excluded per the `confidenza == 0` invariant).

   **So `fvm` already carries roughly 37–43% of the playing-time signal — substantial, but
   the majority is genuinely new.** Hence `TIT_FLOOR = 0.40`: a moderate gate, not an
   aggressive one.

   Two findings worth more than the headline number:

   - **`disponibilita` enters the joint model with a negative coefficient** (β = −0.985 on
     `log1p(fvm)`) despite being ~uncorrelated on its own. Not causal — a confound. High-`fvm`
     stars are the ones anyone reports on, so they are the ones marked down for knocks and
     rotation, while an anonymous squad player is "fully available" precisely because nothing
     is ever written about him. It does mean availability carries information `fvm` has none
     of, which is the case for using it at all.
   - **The residual inspection is what caught the veto bug** (see `DISP_FLOOR` above). The
     10 most "underpriced" players included Yildiz and Zaniolo at `titolarita == 0`,
     `disponibilita == 0` — both correctly reported as freshly injured, both reduced to ×0.07
     by the formula as originally written. Reading the residuals by name, rather than
     trusting the R², is what surfaced it.
3. **`asta-live` uses sentiment by default, re-read each cycle.** Walk-away prices come out
   of the value model, so a live advisory on plain `fvm` would contradict the roster planned
   with sentiment. Re-reading (rather than pinning at session start) follows
   `news_sentiment.py`'s explicit rationale — it holds a session and never a cached table,
   precisely so a player ruled out *during* a long asta moves the walk-away.
4. **Staleness decays `confidenza`**, 7-day half-life, per L1 above. No new concept, no
   threshold, no cliff.
5. **L4 deferred** out of this phase — see the L4 section.

### Still open, named not tasked

- Refitting the tilt weights against a real series (~4 `data_run`s, roughly four weeks out).
- `HALF_LIFE_DAYS = 7` is itself a prior; the same series that fits the weights can test it.
