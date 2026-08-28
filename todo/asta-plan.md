# Design: what to build for the asta, and how much of it is machine learning

Brainstorm output, 2026-08-26. **Not an approved task plan** — no tasks, no checkpoints, no
commits. It is the architectural verdict that a task plan would be written against.

Source documents: [`docs/fantalab/00-asta-e-requisiti-cli.md`](../docs/fantalab/00-asta-e-requisiti-cli.md)
(the requirements, in Italian), [`data/README.md`](../data/README.md) (what we actually hold).

---

## Status — 2026-08-28

**Every input this design was blocked on now exists.** Two things landed since the brainstorm.

**One evening of streaming collection** (2026-08-27, 8½ hours, 1,122 auctions): **137,959 real
sales**, **1,754,493 ladder rungs** of which 97.6% name the bidder, 8,018 distinct opponents, and
**2,584 sales in the exact 8×500 Mantra shape**. Zero ladders with a non-increasing step. L4 was
the layer this plan said to fit "against the only two anchors we have"; that fallback is retired.

**The Mantra compatibility table**, transcribed from the published PDF: 1,452 cells, verified
against the prose rules two independent ways. L1's one unverified input, and the reason it was
called *"il pezzo di lavoro nuovo più grosso del progetto"* and blocking.

| layer | inputs | state |
|---|---|---|
| L1 legality | schemi + full compat matrix + roles | **complete** |
| L2 value | 4 seasons of `voti`/`bonus_malus`/`statistiche`/`qi_bias` | complete **except `E[presenze]`** |
| L3 value→credits | derived from L1+L2 | no new data needed |
| L4 opponents | 1.75M rungs with bidder ids and timestamps | **complete** |
| L5 live decision | L2 variance + L4 | follows from the above |

**What is actually missing, in order:**

1. **`E[presenze]` has no denominator.** `voti` records only graded appearances, so *injured*,
   *benched* and *not in Serie A* are indistinguishable. The one factor the design says carries
   the signal is the one the data cannot cleanly express. Needs a squad-membership source.
2. **Nothing is in Postgres.** Every figure above was computed by streaming a 1.19 GB file.
3. **`player_sentiment` has 2 rows** — the precompute layer Part 2 depends on has never run.
4. **League settings** (§7), partly answerable from `docs/leghe-api.md` and from the collected
   population — 898 of 1,126 leagues use `chiamata`.
5. **The WINE 2016 constant**, still unretrieved. A provable floor; nothing depends on it.

**Nothing on that list blocks starting.** Build order steps 0–3 can begin today.

---

## Verdict

The Monte Carlo auction simulator is **needed**. A machine-learning model that "picks the best
players" is **the wrong frame**.

The asta is not a prediction problem. It is **allocation under a budget constraint against
strategic opponents**. Player projection is one small input to it. The binding levers are
algorithmic and economic, not learned:

- the hardest piece of work is **bipartite matching** (roster legality across the 11 schemi) —
  deterministic, zero ML, and already named as blocking in §10;
- the second hardest is **turning value into credits** so the numbers are budget-consistent —
  arithmetic, and the place where the hand-tuned ×1.7 of §14 gets replaced by something derived;
- the genuinely statistical part is the **opponent model**, and it is blocked on data we do not
  have.

A second finding, from the literature search on 2026-08-26, sharpens this. Our asta is a
**second-price pacing game**, and computing its equilibrium is **PPAD-complete** — provably
intractable, not merely unsolved. So there is no optimal algorithm to go find. What exists is a
near-optimal one with regret guarantees (adaptive pacing, L5) and a simulator to handle the part
it does not cover. That is not a compromise; given the hardness result it is the ceiling.

> **Superseded 2026-08-26 on the data question.** The prices are no longer missing — see
> build-order step 5 and [`docs/fantalab/05-osserva-aste-harvest.md`](../docs/fantalab/05-osserva-aste-harvest.md).
> The paragraph below is kept because it states *why* they mattered, and because coverage
> is only ever as complete as the evenings we were watching.

Beyond that, the single biggest lever in the whole project was data we did not have: **real
prices paid in real Mantra aste**. §14 names the source (`Osserva Aste`, 45 public Mantra aste in progress).
Without it the bots in §11 are invented, and the acceptance test in §17 — *"if the first `Pc`
goes for 60 when reality was 192, the opponent model is too timid"* — cannot be run at all.

Scraping those aste beats every modeling decision below.

---

# Part 1 — The five layers

Only two of the five are ML-ish. Listed in dependency order.

## L1 — Roster legality (schema matching)

**Question:** given a rosa, how many of the 11 schemi can it field?

**Technique:** bipartite matching / max-flow. Players on one side, schema slots on the other.
Multi-ruolo players get multiple edges; slots that accept two roles get multiple edges. 519
players × 11 schemi resolves in microseconds — this is a small graph, not a hard one.

**ML content: none.** Pure combinatorics.

**Why it comes first:** §10 calls it *"il pezzo di lavoro nuovo più grosso del progetto"* and
makes it blocking. Everything downstream depends on it — replacement level (L3) is defined per
slot, and the multi-ruolo premium is an output of this function rather than a hand-tuned bonus.

**Inputs complete as of 2026-08-28.** [`data/mantra_schemi.json`](../data/mantra_schemi.json)
for the 11 schemas, [`data/mantra_compat.json`](../data/mantra_compat.json) for the full
per-formation matrix, and the `ruoli_codice` array on the `quotazioni` table for player roles —
548 Mantra players for 2026/27, **49% of them multi-ruolo**, confirming §5's 44%. The CSVs this
section used to name were deleted once the database was verified to hold them.

The caveat that stood here — *"blocked pairs for 4-1-4-1 only, from a 2024-25 PDF, confirm it"* —
is **resolved, and it was worse than thin**. The file held one entry and ten empty lists, and the
entry was *correct*: 4-1-4-1 really is the only schema forbidding the W/T swap outright. It was
answering a much narrower question than L1 asks. It is now the whole table — 11 schemas × 11
slots × 12 roles = **1,452 cells** — transcribed from the published PDF, which is kept at
[`docs/sources/`](../docs/sources/) because its URL is season-stamped.

**The value L1 must not collapse is `-1*`.** Four values appear: `ok`, `-1` (malus, allowed),
`-1*` (**refused at lineup submission**, allowed with a malus only as the outcome of a forced
substitution), and `no`. 120 cells are `-1*`. Treating them as `-1` reads as "allowed" and
produces lineups the platform rejects. The five prose rules in `rules/sistema-mantra.md` — B/Dd/Ds
into Dc, Dd against Ds, E into M, M into E, W into T — are exactly those cells, in all 94 places
the slot exists, which is what verifies the transcription against an independent source.

Two defects the transcription exposed, both now fixed:

* `mantra_schemi.json` had 4-3-1-2's **`T/A/Pc` slot truncated to `A/Pc`**, because a gate
  asserted a ceiling of two roles per slot that the source does not have. L1's primary input was
  wrong about a slot.
* The gates had only ever run against fixtures, never against the shipped file, so the one-entry
  matrix passed for a week. Tests now judge `data/` directly.

## L2 — Player value

**Question:** expected season fantapoints per player.

Decompose it: `E[presenze] × E[fantavoto | plays]`. The two factors have different statistical
character and should not be estimated together.

**Technique:** hierarchical / empirical-Bayes shrinkage. Each player's observed mean is shrunk
toward a role × team prior, weighted by sample size.

**Why not gradient boosting:** n ≈ 1,500 players, and the target is extremely noisy. `voto` is
essentially noise centered on 6.0 — the signal lives in the bonus columns (gol, assist, rigori)
and in whether the player appears at all. Under those conditions shrinkage captures nearly all
of the recoverable signal and GBM buys very little. Try GBM later as a refinement; do not start
there.

**Output must be a distribution, not a point.** L5 needs the variance.

Data is in hand: `voti` and `bonus_malus` at 50,634 rows each across four seasons
(2022/23–2025/26, ~11,900 player rows per season), plus `statistiche` and `qi_bias`. Note the trap
recorded in `data/README.md` — `statistiche.media_voto` uses `"0,0"` for *absent*, not for a grade
of zero, in 2,846 rows.

**`E[presenze]` has no denominator, and this is the real gap in L2** (measured 2026-08-28). A row
in `voti` exists *only where the player was graded* — `rows_without_a_vote` is 0 in every season,
and players average 20.4 rows against 38 matchdays. So *injured*, *benched* and *not in Serie A
that season* are indistinguishable, and `rows / 38` systematically under-rates anyone who arrived
in January or came up with a promoted side. The factor the decomposition calls out as carrying the
signal is the one the data cannot cleanly express. Closing it needs a squad-membership table
nothing currently scrapes; short of that, the shrinkage prior has to absorb it and the uncertainty
has to be reported rather than hidden.

**21% of the 2026/27 Mantra listone has no history at all** — 431 of 548 players appear in `voti`,
117 do not. Those are the pure shrink-to-prior cases, and they include exactly the newcomers the
market overpays for.

Partly built already: `scripts/target_price.py` and `scripts/analyze_qi_bias.py`.

## L3 — Value to credits

**Question:** what is this player worth in this league's currency?

**Technique:** value over replacement, then budget normalization. Not ML — arithmetic, and the
step most people get wrong.

1. A player's worth is his points **minus the points of the freely available player at the same
   slot**. The 1-credit riserva is the baseline. More than half the listone goes unsold (§4), so
   a 1-credit player always exists.
2. Normalize so that total VOR across the 208 mandatory purchases equals `4000 − 208` credits.

Budget consistency then holds by construction. This is what §14's *"moltiplicare per circa 1,7"*
is reaching for, done as a derivation instead of a constant — and it recomputes itself when the
league settings change, which §13 requires.

**The role split is now measured.** `strategy.allocate_auction_budget` uses a hand-tuned
5/15/35/45 for P/D/C/A. Across 116,000 Classic sales collected 2026-08-27/28 the market spends:

| role | share of all credits | the heuristic | error |
|---|---|---|---|
| P | **2.7%** | 5% | −46% |
| D | **12.9%** | 15% | −14% |
| C | **28.7%** | 35% | −18% |
| A | **55.6%** | 45% | **+24%** |

The heuristic under-budgets attackers by a quarter and over-budgets everyone else. Median
attacker 19 credits against a median midfielder's 10. Replace the literal; it is now an empirical
constant rather than a guess, and VOR should reproduce it rather than contradict it.

**Prices are log-normal, and every price needs normalizing by league budget first.** Raw skew is
**37.2**; after a log it is **0.13**. Fit in log space. And the population is heterogeneous —
budgets of 500 (652 leagues), 1000 (341), 750, 600, 300; team counts of 8 (440), 10 (338), 6, 4 —
so a price of 60 means nothing until it is expressed as a fraction of one team's credits. The
maximum observed price of 9,102 is a big-budget league, not an error.

**Mantra twist:** replacement level is defined per **slot**, computed through L1. The multi-ruolo
premium (§5, 44% of the listone) then falls out as option value rather than being invented.

## L4 — Opponent model and price formation

**This is where Monte Carlo lives, and it is the honest statistics of the project.**

Model each rival's willingness to pay:

```
WTP_i(p) = consensus_price(p) × exp(ε_i) × star_hunger_i(p) × budget_pressure_i(t)
```

Clearing price is `second-highest WTP + 1` — the timer mechanics of §2 make this a second-price
auction in effect: you pay one credit above the runner-up's walk-away point.

**Fitting: the data exists as of 2026-08-28, and the two-anchor fallback below is retired.**
One evening of streaming collection (1,122 auctions, 8½ hours) produced:

| | |
|---|---|
| ladder rungs | **1,754,493** |
| rungs naming the bidder (`Bid.team_id`) | **97.6%** |
| rungs carrying a timestamp | **100%** |
| distinct bidders | **8,018** |
| real sales (buyer present) | **137,959** |
| Mantra sales | 13,483 across 81 auctions |
| **the exact 8×500 Mantra shape** | **2,584 sales across 16 auctions** |
| ladders with a non-increasing step | **0 of 115,782 multi-rung** |
| player linkage | 98.15% |

Every rung names the team that pushed, so `ε_i` and `star_hunger_i(p)` are estimable per bidder
rather than assumed, and `budget_pressure_i(t)` reconstructs from cumulative spend against the
seed's declared credits (79 of 81 Mantra auctions internally consistent). This is what the ladder
was collected for: clearing prices alone are what polling already produced.

~~Absent that data, fit against the only two anchors we have — Malen 192 and Martinez L. 191 in
an 8×500 Mantra lega (§4) — and treat the result as a deliberately wide prior.~~

**Competition is the strongest live price signal available**, and it is information polling could
not collect at all:

| ladder rungs so far | median clearing price / QA |
|---|---|
| 1–2 | 0.17 |
| 2–5 | 0.33 |
| 5–10 | 0.88 |
| 10–20 | 1.69 |
| 20+ | **3.33** |

A 20× monotone spread over 122,000 sales. At rung 12 the model can already say it is heading for
~1.7× QA. Quotazione alone is a weak anchor by comparison — interquartile price/QA spans an order
of magnitude in every role.

**The irreducible uncertainty is CV ≈ 0.85.** For 210 players priced in 30+ leagues, the
coefficient of variation of price-as-%-of-budget has median 0.85 (p10 0.46, p90 1.54). The same
player, same day, same normalization, varies ±85% at one sigma. No point predictor beats that,
because the variation is in the opponents, not the player — which is the empirical case for
pacing over a supervised price model.

**Concentration:** the top 5 players take **24.8%** of a league's spend, top 10 **38.4%**, top 25
**62.2%**. Accuracy on the top ten is worth more than accuracy on the other 240 combined.

**No budget-depletion signal was found**, and this is unresolved rather than settled. Price/QA by
decile of auction progress across 663 auctions with 100+ sales is noise: `1.20, 1.25, 0.86, 0.75,
1.50, 1.12, 0.58, 1.82, 1.67, 0.33`. Benoit & Krishna predicts decay and it is absent. Two
readings, not separable without more work: call order is not random, or these auctions do not run
budgets down hard enough. **The seed records `asta_mode`, so the test is available** — condition
the deciles on `chiamata` vs `random` vs `alphabetic` before building any depletion term.

**At least one bot must deplete budgets on purpose.** Benoit & Krishna (2001) show this is a
real equilibrium strategy, not a quirk: a bidder with a large budget bids up an early object to
drain a rival, then takes the object it actually wants against weakened opposition. A bot pool
whose only variation is willingness-to-pay noise will never produce this, will underprice the
top, and will fail §17's realism test in the direction that costs us most — the plan looks
disciplined right up until the third contested player.

**Then simulate.** 10,000 aste under a given configuration produces a clearing-price distribution
per player, which is exactly what §11 asks for: *"su 500 aste con questa strategia, quanto mi è
costato in media il secondo `Pc`, e quante volte l'ho perso."*

The simulator must reproduce the engine rules of §13's "cosa NON si configura", especially the
**MAX and its unlock**. Getting MAX wrong produces rose that could not exist (§11, §18).

## L5 — The live decision (Copilota)

**Question:** the walk-away price. One number: *fin dove arrivo*.

The principle: bid up to the point where the marginal value of this player equals the value of
those credits spent elsewhere. That "value elsewhere" is the shadow price of a credit, `μ`.

```
walkaway(p) = VOR(p) / (1 + μ)
```

**How `μ` is updated is the part worth getting right.** The obvious rule — remaining league value
over remaining league credits — is wrong in a specific way: it pushes every error in our VOR
estimates straight into the price. The literature's rule is a dual subgradient step on *observed
expenditure*, which self-corrects even when the value model is wrong (Balseiro & Gur 2019;
Balseiro, Lu & Mirrokni 2020):

```
after each assignment t:
    ρ    = target spend rate            = budget / expected remaining purchases
    g̃_t  = ρ − (what we would have spent on t at the current μ)
    μ    ← max(0, μ − η·g̃_t)          # or the multiplicative form μ·exp(−η·g̃_t)
```

Same cost — still a handful of lines, still microseconds, §15's 2-second budget met with four
orders of magnitude to spare. Strictly more robust. It also carries a guarantee: `O(√T)` regret,
asymptotically optimal, holding **even when opponents' bids are arbitrary or adversarial** — no
opponent budgets or valuations required as input.

### Two regimes, and one rule does not cover both

The regret guarantee above is asymptotic in `T`. Ours is `T ≈ 250`, and §4 says the outcome turns
on four or five of those — Malen at 192 is **38% of one budget on a single object**. That is
exactly the lumpy, non-asymptotic regime where pacing arguments stop applying.

So the Copilota runs two rules, and says which one it is using:

| Regime | Population | Rule |
|---|---|---|
| **Tail** | the ~480 players clearing at 1–10 credits | adaptive pacing, as above. Cheap, robust, provably near-optimal. |
| **Top** | the ~40 contested players (§4's four or five, plus margin) | Monte Carlo rollout against the L4 bots: value of winning at `p` versus losing and continuing optimally. Precomputed offline into a small table, looked up live. |

Pretending one rule spans both is the quiet way to lose the asta: pacing is calibrated by
average spend, and the top of the board is where the average stops describing anything.

## Explicitly rejected: solving the game exactly

Two separate reasons, and the second is a theorem.

**Exact MDP.** State is `(my rosa × my credits × 7 × (rosa, credits) × remaining pool)`.
Astronomical. Unsolvable here and unnecessary — Monte Carlo rollout plus the `μ` update captures
substantially all of the value. Do not build a Markov model.

**Exact equilibrium.** Our asta is a second-price pacing game, the standard model for exactly
this problem: each bidder picks a multiplier `α_i ∈ [0,1]` and bids `α_i · v_ij`. An equilibrium
always exists (Conitzer et al. 2022) — and **computing one is PPAD-complete** (Chen, Kroer &
Kumar 2023), PPAD-hard even for a `γ`-approximation with any constant `γ < 1/3` (Chen & Li 2025).

Two footnotes, both worth knowing:

- **First-price would have been easy.** It has a unique equilibrium computable by the
  Eisenberg–Gale convex program. Second-price introduces the non-linearity that breaks
  tractability, and FantaLab's timer mechanics make our asta second-price. Bad luck, not bad
  modeling.
- **Our instance sits in a tractable island — uselessly.** Polynomial-time algorithms exist for a
  constant number of buyers (Yan, Wang & Liu 2026) and for a constant number of goods (Huang,
  Yan, Wang & Liu 2026). We have 8 buyers, so we qualify. But the algorithm enumerates cells of a
  hyperplane arrangement in `n−1 = 7` dimensions over `≈ n·m = 4,000` hyperplanes. Polynomial with
  a hopeless exponent. Tractable on paper, not on a laptop, and not in ten seconds.

**Consequence for this project: "find the optimal algorithm" is retired as a goal.** The Monte
Carlo simulator is not a rough substitute for solving the game — given the hardness result, it
*is* the state of the art response. Build it without apology.

## Where our problem leaves the literature

One gap, and it is the one that makes L1 structural rather than decorative.

**Every pacing result assumes additive valuations** — a value `v_ij` per good, summed over the
goods won. Ours is not additive. A player's worth depends on which schemi his roles keep open
(§10), so the roster has complementarities by construction, and 44% of the listone is multi-ruolo
(§5) precisely because those complementarities are the game. Under combinatorial valuations with
budgets, revenue maximization is NP-hard; the known results are a greedy 2-approximation and a
randomized ≈1.582.

**Consequence:** the pacing loop must never see a raw player value. It sees the *marginal* value
that L1 computes against the current rosa — how much this player adds given what we already hold
and which schemi that leaves open. L1 is not a legality checker bolted onto the side; it is the
function that produces the number L5 prices.

**A worst-case floor exists, and it is worth having.** Anagnostopoulos, Cavallo, Leonardi &
Sviridenko (WINE 2016) study this exact setting — managers bidding in rounds until roster
positions fill — and give simple strategies guaranteeing a constant fraction of the best
achievable value **regardless of what opponents bid**. No opponent model required. That is the
right thing to fall back to when the L4 bots turn out to be miscalibrated, which on current data
they will be. See Open questions: the paper's PDF resisted three fetch attempts and the constant
is not yet in hand.

---

## Where the effort is misallocated

| Thing | Verdict |
|---|---|
| ML to project fantapoints | Overthought. Shrinkage ≈ GBM at this n and noise level. |
| ML to "choose the best players" | Wrong frame. The choice is greedy once prices are right. |
| Markov / exact DP | Overthought. Drop. |
| Monte Carlo asta simulator | **Correct. The core of the project**, and — given PPAD-completeness — the ceiling, not a fallback. |
| Searching for "the optimal algorithm" | Retired. It provably does not exist in efficient form. |
| Fitting opponent WTP to real aste | **Underthought. Highest value per hour.** Still true, and now unblocked: 1.75M rungs with bidder ids. |
| Schema-legality matching | **Underthought. Blocking, and it is plain algorithms.** Inputs complete 2026-08-28; nothing stands in front of it. |
| Budget-consistent price normalization | Underthought. Replaces the ×1.7 hack with a derivation — and the measured role split shows the current constants are off by a quarter on attackers. |
| ~~Scraping the 45 public Mantra aste~~ | **Done, and 25× larger than planned.** 1,122 auctions in one evening, 81 of them Mantra. Retired as a work item. |
| Loading the landing zone into Postgres | New, and now the cheapest unblocking step. Everything measured so far was computed by streaming a 1.19 GB file. |

## Two facts that shrink the problem

1. **§4: the asta is decided on four or five players.** Precision matters on the top ~40, not on
   all 519. Everything else clears at 1–10 credits. Allocate modeling effort accordingly.
2. **Credits are conserved — the game is zero-sum.** Absolute projection errors partly cancel;
   what survives is *ranking* and *price relative to market*. Mediocre projections with correct
   inflation arithmetic beat excellent projections on the wrong price scale.

---

# Part 2 — The LLM during the asta

## The requirement that was wrong

§15 used to read *"funziona senza rete: durante l'asta niente chiamate a internet"*, marked
**[M]**. That was simply incorrect about how the tool runs — the asta happens fully connected,
and token spend is not a constraint.

**Corrected in the source doc on 2026-08-26.** §15 now states the requirement that was actually
worth having: *il numero non dipende dalla rete* — the walk-away price is computed locally and
renders regardless, and no screen ever blocks on a socket. The network is a layer above, not the
critical path.

Two objections to live querying survive that correction, because they are physics rather than
policy:

- **Tail latency, not median.** A 1.5s median is fine; a p99 of 8s means the number arrives after
  the hammer. The timer resets to 10s on every rilancio (§2).
- **Near-zero new information inside the window.** The asta lasts one evening. Infortuni,
  ballottaggi and mercato are all knowable at 18:00. Live querying buys almost no information
  that a 17:00 precompute does not already hold.

Both are now manageable rather than blocking. They shape the design below.

## The reframe: precompute is what makes live fast

The latency in the existing `news-fetch` path is **WebSearch/WebFetch round trips**, not
generation — 3 to 8 seconds of it.

So precompute is not the alternative to live querying. It is the **enabler** of it. Cache the
static per-player brief the night before, hand it to the live call as a fact block, and the live
call needs no search at all. That reliably lands under 1.5s.

Run both layers. They compose.

### The precompute layer

The player universe is known in advance: 519 rows (548 Mantra rows for 2026/27 in the
`quotazioni` table), fixed. Nothing revealed during the asta could not have been scored the
evening before.

**The table this writes to is empty.** `player_sentiment` holds 2 rows — `news-fetch --write` has
never had a real run against the database. That is a scheduled job rather than missing source
data, but the precompute layer is what makes the live call fast enough to be used at all, so it
is a prerequisite of Part 2 and not an optimisation of it.

Run the model over every player the night before, plus a refresh on the morning of. Write to
Postgres. During the asta it is a dictionary lookup — zero latency, zero network.

At concurrency 8 this completes in well under an hour, which means the precompute pass can use a
**large, slow model with search enabled** — strictly better judgement than anything obtainable in
a 10-second window.

The pipeline already exists in the right shape: `news-fetch` runs one query per player over
WebSearch/WebFetch, validates against a schema, and writes to `player_sentiment`. A second
pipeline — call it `asta-brief` — reuses `agentkit/` with a different prompt and a different
table. Same purity split the repo already enforces: `models`/`prompt`/`pool` pure, `store`/`pipeline`
doing the I/O.

## What the live LLM should actually do, ranked

**1. Parse the state entries. This is the sleeper hit.**

§12 and §18 both say that **data entry is the real adoption risk**, not model quality: *"se
registrare costa più di tre secondi, a metà serata smetterò di farlo e lo strumento diventa
inutile."*

With live tokens: type `malen a luca 192` and the model emits `{player_id, price, team}`. Fuzzy
names, nicknames, typos, all absorbed. That is a 3s → 0.5s win on the one thing that determines
whether the tool is still being used at 22:30. Larger payoff than any player judgement.

**2. Adversarially check our own number.**

The engine says walkaway 45. A live call sees the full state, the number, and how it was derived,
and answers one question: *does this look wrong?* This is a guard against a bug in `μ` or in VOR
at 21:47, when there is no time to debug. Genuinely state-dependent, genuinely useful.

**3. Narrate opponent state.**

"Team 5 holds 3 `Pc` and 210 credits with 9 slots left" is arithmetic the engine already does.
The model turns it into *"Team 5 is cornering `Pc`, expect a push on the next one"*. Modest value,
cheap.

**4. Handle novel situations.**

Cambio ruolo mid-asta (§13 lists it as an admin power), an unscored newcomer, admin weirdness.
Cache miss, so a live call is the only path. Rare but real.

**5. Judge the player.**

Still better precomputed — the facts are static, and precompute buys a bigger model and more
search. Live adds nothing here.

Note the inversion: the originally requested use, #5, is the weakest of the five. #1 and #2 are
where free tokens actually pay.

## Latency engineering

- **Hedged requests.** Fire 3–5 identical calls in parallel and take the first valid response.
  This kills the p99 tail outright, and it is affordable only because token spend is unconstrained.
  Highest-leverage trick available.
- **Fire on player-on-block, not on timer-running.** The first call has the 20s first-chiamata
  budget (§7 default), not 10s.
- **Stream, and order the schema so the number comes first.** The number renders at ~300ms; prose
  fills in behind it.
- **Pre-warm the top 40.** Per §4 the asta turns on four or five players. Keep 40 hot, refreshing
  live state every few assignments. Under random chiamata this yields a cache hit on most players
  that matter.
- **The engine number renders at t=0, always.** The LLM overlays when it lands. Never a blank box
  waiting on a socket.
- **Test the fan-out before asta night.** Hedging × pre-warming can hit concurrency limits on the
  Ollama cloud daemon. Find that ceiling in advance, not during.

## Guardrails — now the dominant risk

Network was never the real danger. This is: at 21:47, tired, the model says *"prendilo, vale 60"*,
and 60 credits go to a 40-credit player. That is how an asta is lost.

- The walkaway number stays **deterministic**. The LLM emits a **bounded multiplier, hard-clamped
  to ±20%**, and a low-confidence reading applies no multiplier at all.
- **Show both, with provenance:** `walkaway 45 · LLM +8% → 49`. Never a single fused number.
- **Log every live call** — inputs, output, and the price the player actually cleared at.
  Post-asta this is the only way to learn whether the layer helped.

Structured output, not prose. §15 requires *"leggibile a colpo d'occhio"*, and a paragraph
arriving at second 7 of a 10-second timer is unreadable. Schema roughly:

| field | type | note |
|---|---|---|
| `price_multiplier` | float | hard-clamped ±20% |
| `flag` | enum | `INFORTUNIO` / `BALLOTTAGGIO` / `RUOLO_DRIFT` / `NUOVO_RIGORISTA` / `IN_USCITA` / `NESSUNO` |
| `one_line` | string ≤60 chars | rendered under the walkaway number |
| `confidence` | low / med / high | gates whether the multiplier applies |

`agentkit`'s `output_format` json_schema is already verified to survive the shim — it honours
forced `tool_choice` (`CLAUDE.md`, measured 2026-08-26).

## Model split

| Pass | Model | Search | Budget |
|---|---|---|---|
| Precompute (night before + morning of) | large | on | hours |
| Live (during the asta) | `deepseek-v4-flash:cloud` | **off** | <1.5s |

`deepseek-v4-flash:cloud` is already verified end-to-end on this stack via the local Ollama
daemon, WebSearch and WebFetch included. Flash is the right latency tier for the live path;
search is off there because the precomputed brief already carries the facts.

---

# Build order

Steps 1–3 are pure code with no ML, and deliver most of the edge. ML enters only at step 6, and
only once step 5 has given it something to eat.

0. ✅ **Load the evening into Postgres.** `fantabot aste-load` over the landing zone. Every
   figure in L3 and L4 above was computed by streaming the file; fitting wants SQL. Cheap, and
   nothing else needs it first. *Pending as of 2026-08-28 — the landing zone is 1.19 GB on disk
   and is the durable record; Postgres is derived from it.*
1. **L1 legality matcher.** Deterministic, testable, blocking for everything else.
   **Unblocked 2026-08-28** — all three inputs are complete and verified for the first time.
   Must carry `-1*` through as its own state, not fold it into `-1`.
2. **L3 VOR + budget normalization** on top of the existing `target_price`. Yields real credit
   prices immediately and retires the ×1.7 constant. The measured role split above is the check
   it has to reproduce.
3. **L5 pacing update for `μ`** → one number on screen: *fin dove arrivo*. This is the tail regime
   only; it is useful on its own, with no simulator behind it.
4. **Fast state entry**, with LLM parsing (Part 2, #1). Protects adoption, which §18 rates as the
   top risk.
5. ✅ **Scrape real Mantra auction prices** from `Osserva Aste`. *Done 2026-08-26* —
   `scripts/{scan,collect,resolve}_aste_live.py`. First evening: 41 auctions, 871
   assignments, 198 of them in our exact 8×500 shape. **Superseded 2026-08-27/28** by the
   streaming collector (`fantabot aste-collect`), which records every raise with its bidder
   rather than merged snapshots: 1,122 auctions, 137,959 sales, 1.75M rungs in one evening. This
   step was **time-boxed, not effort-boxed**, and the box has closed — the aste are over.
6. **L4 Monte Carlo simulator**, calibrated on (5). This is Palestra mode. Also unlocks the top
   regime of L5, which cannot be built before it. **Now data-complete**; start with the 16
   auctions in the exact 8×500 shape and validate against the wider 81.
7. **L2 refinement.** Least marginal gain; do it last — and note that its ceiling is set by the
   missing presence denominator, not by the estimator.

**Conditional, and promote it if the condition holds:** §12 marks *suggerimento di chiamata* as
**[C]**, on the grounds that it only applies under chiamata libera. If the lega picks libera
(§7 currently leans random), it should be promoted well above [C]. Benoit & Krishna's result is
that **the order of sale changes outcomes drastically** — which makes nomination one of the few
levers we control directly, rather than react to. Under random chiamata it stays hidden, as §12
already says.

The `asta-brief` precompute pass can be built in parallel with any of these — it reuses
`agentkit/` and depends on nothing above.

## Open questions

- **League settings still undecided** (§7): rosa min/max, and chiamata libera vs random. §13's
  sensitivity sweep is the answer — run the simulator across the plausible range and report which
  unknowns actually move the result. Do not block on Thursday's conversation.
  **Partly answerable now.** `docs/leghe-api.md` already establishes lega 4103937 as Mantra,
  `sroles=2`, `minrl=[2,28]`, 30-man. And the collected population says **898 of 1,126 leagues use
  `chiamata`** against 187 `random` and 39 `alphabetic` — so libera is the overwhelming norm, and
  *suggerimento di chiamata*, marked **[C]** below on the grounds that §7 "leans random", should
  be promoted rather than left conditional. Benoit & Krishna's result is that sale order changes
  outcomes drastically, and nomination is one of the few levers we control rather than react to.
- ~~**`mantra_compat.json` is thin**~~ **Resolved 2026-08-28.** Full 1,452-cell table, transcribed
  from the published PDF and verified against the prose rules in two independent ways. Source PDF
  kept at `docs/sources/`. See L1.
- **Imbattibilità portiere** has never been asked (§7). It materially changes goalkeeper value —
  and the measured P share of spend is **2.7%**, roughly half the 5% the heuristic assumes, which
  makes the question cheaper to get wrong in one direction than the other.
- **`E[presenze]` has no denominator.** `voti` records only graded appearances, so absence is
  implicit and its causes are indistinguishable. See L2. This is the largest remaining *data* gap;
  everything else on this list is a decision or a paper.
- **The depletion signal is absent and unexplained.** Condition the per-decile price/QA on the
  seed's `asta_mode` before building any `budget_pressure` term. See L4.
- ~~**Nothing yet reads `Osserva Aste`.** Feasibility of scraping it is unconfirmed.~~
  **Resolved 2026-08-26** — built and running. Spectator state is a public Firebase node
  needing no auth, and it carries every raise with its bidder, not just clearing prices.
  See [`docs/fantalab/05-osserva-aste-harvest.md`](../docs/fantalab/05-osserva-aste-harvest.md).
- **The WINE 2016 constant is not in hand.** `aris.me` refused the PDF three times
  (`Socket is closed`, `ECONNRESET`) on 2026-08-26. The claim above — a constant fraction of the
  best achievable value against arbitrary opponents — comes from abstracts, not from the paper.
  The strategy's exact form and its constant are both still unread, and both matter: it would
  give a provable floor under everything else here. Retrieve via the Springer chapter or an
  institutional copy.

## What this design deliberately does not do

Carried from §16, unchanged:

- It does not **bid for us**. It advises; the human clicks.
- It does not **reimplement the FantaLab room**. The real room stays open alongside.
- It does not **predict the season**. It exists to buy well.
- It does not **depend on paid subscriptions**.

---

## Sources

Retrieved 2026-08-26. The complexity results are what retire "solve it exactly" as a goal; the
pacing papers are what L5 implements.

- Balseiro & Gur, [*Learning in Repeated Auctions with Budgets: Regret Minimization and
  Equilibrium*](https://pubsonline.informs.org/doi/10.1287/mnsc.2018.3174), Management Science
  65(9), 2019 — adaptive pacing, asymptotic optimality under arbitrary competing bids.
- Balseiro, Lu & Mirrokni, [*Dual Mirror Descent for Online Allocation
  Problems*](http://proceedings.mlr.press/v119/balseiro20a/balseiro20a.pdf), ICML 2020 — the
  update rule quoted in L5, Algorithm 1.
- Huang, Yan, Wang & Liu, [*Pacing Equilibria in Second-Price Auctions with Few
  Goods*](https://arxiv.org/pdf/2605.09332), arXiv 2605.09332, May 2026 — carries the PPAD
  results, the first- vs second-price contrast, and both tractable islands.
- Conitzer, Kroer, Sodomka & Stier-Moses, [*Multiplicative Pacing Equilibria in Auction
  Markets*](https://arxiv.org/abs/1706.07151) — the model itself; existence.
- Conitzer et al., [*Pacing Equilibrium in First-Price Auction
  Markets*](https://arxiv.org/abs/1811.07166) — the Eisenberg–Gale route that second-price loses.
- Benoit & Krishna, [*Multiple-Object Auctions with Budget Constrained
  Bidders*](https://www.ssrn.com/abstract=223868), Review of Economic Studies, 2001 — budget
  depletion as strategy; order of sale.
- Anagnostopoulos, Cavallo, Leonardi & Sviridenko, [*Bidding Strategies for Fantasy-Sports
  Auctions*](https://link.springer.com/chapter/10.1007/978-3-662-54110-4_8), WINE 2016 — our exact
  setting. **Not yet read in full** — see Open questions.
- [*Approximate Combinatorial Auctions with
  Budgets*](https://web.ics.purdue.edu/~nguye161/auctionwithbudget.pdf) — the NP-hardness and
  approximation ratios cited under the additivity gap.
