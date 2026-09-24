# Lineup optimizer — theory (inputs, data, output)

	⁠Theoretical spec only: the mathematics and algorithmic state of the art for the
	⁠weekly Mantra formation optimizer. No implementation, no auction/bidding, no betting.
	⁠Produced from a deep-research pass (2026-09-02): 23 sources, 24/25 claims confirmed by
	⁠3-vote adversarial verification, 1 refuted. Confidence tags and vote counts inline.

## Verdict (read first)

*Scope caveat, load-bearing.* All surviving primary literature (DFS: NFL/NHL/MLB, plus
general soccer xG) models a salary cap → the selection problem is a 0-1 / multi-dimensional
*knapsack, which is **NP-hard. **Mantra has no salary cap.* Its binding constraint is
role→slot eligibility over the 11 Mantra schemi. So the Mantra problem is *not* knapsack —
it is *max-weight bipartite matching / assignment*, solved exactly by the Hungarian
algorithm in *O(n³) polynomial* time. That is exactly what ⁠ domain/asta/legality.py ⁠
already implements. The Mantra case is easier than the DFS case the literature studies.

And: a single weekly lineup versus one league opponent is a *head-to-head / cash game*,
NOT a large-field top-heavy (GPP) tournament. The operative objective is Haugh & Singal's
mean-variance sign-flip (§4), not the variance-maximization / stacking strategy that the
tournament papers derive.

## 0. Mathematical framing

0-1 binary integer program: ⁠ x_i ∈ {0,1} ⁠ selects players, maximize an objective under
linear feasibility constraints (roster size, per-role counts, slot eligibility). [high, 3-0]

| Case | Mathematical name | Complexity | Algorithm |
|------|-------------------|------------|-----------|
| DFS with salary cap | 0-1 / multi-dim knapsack | *NP-hard* | ILP solver |
| *Mantra (role→slot only)* | *max-weight bipartite matching / assignment* | *O(n³) poly* | *Hungarian, exact* |
| Objective with variance/covariance | MIQP (quadratic) | NP-hard | solver / enumerate |
| Multi-entry top-heavy portfolio | submodular set function | NP-hard, but *greedy (1−1/e)≈0.63* | greedy |

Central point: **while the objective is linear (expected points), bipartite matching solves
it exactly in polynomial time.** No Monte Carlo, no ILP. Enumerate the 11 schemi × solve a
matching each = trivial. [3-0]

## 1. INPUTS to the algorithm

Not point estimates — *distributions*. Minimum: [3-0]

1.⁠ ⁠*Mean vector* ⁠ μ_i ⁠ — expected fantavoto per player per matchday.
2.⁠ ⁠*Covariance matrix* ⁠ Σ ⁠ — variances ⁠ σ_i² ⁠ + pairwise correlations ⁠ ρ_ij ⁠.
3.⁠ ⁠*Probability of starting / titolarità* ⁠ p_i ⁠.
4.⁠ ⁠*Role→slot eligibility* — which role code fills which slot across the 11 schemi (already
   in ⁠ mantra_schemi.json ⁠ + ⁠ mantra_compat.json ⁠, including the ⁠ -1 ⁠ / ⁠ -1* ⁠ distinction).

Modeling scores as *jointly Gaussian* gives a closed-form win-probability from only
⁠ μ_i, σ_i², ρ_ij ⁠ — the full distribution is not required for the win-prob objective. [3-0]

## 2. DATA needed to produce the inputs

Distributions come from *projection models* (ML / statistical): [3-0, one 2-1]

•⁠  ⁠*Supervised neural nets* on history → expected points (feed the optimizer).
•⁠  ⁠*Ensembles of generative models* emitting the full distribution (enables the
  variance/covariance objective).
•⁠  ⁠*Bayesian hierarchical xG models* on event data (StatsBomb): hierarchical logistic
  regression, role/player as group-level random effects.

Raw features/data required:
•⁠  ⁠Fixture difficulty / opponent defensive strength.
•⁠  ⁠Minutes-played / rotation-risk model.
•⁠  ⁠Injury + probable-lineup (news) signals → feed ⁠ p_i ⁠. **This is what ⁠ news fetch ⁠ already
  provides** (opinion + availability), not projected points.

Why *hierarchical / partial-pooling* rather than a simple per-player average: player
identity has a *persistent* effect on xG even after controlling for shot context; naive
fixed effects overfit low-shot players. [3-0]

⚠ *Refuted (0-3):* "SOTA is the median of 50 tree-based regressors (XGBoost+RF)" — NOT
established state of the art. Do not treat it as such.

*Repo gap confirmed:* the stats source is still unchosen (CLAUDE.md:209). Without
per-matchday ⁠ μ_i, σ_i² ⁠ the optimizer has no input. *This is blocker #1.*

## 3. OUTPUT

1.⁠ ⁠*Chosen legal lineup* — the optimal binary vector: a starting XI satisfying one of the
   11 schemi.
2.⁠ ⁠*Ordered bench* for automatic substitutions (who enters if a starter does not play).
3.⁠ ⁠(optional) *Predicted score distribution* of the lineup (mean + variance), to estimate
   the probability of beating the opponent.

⚠ No source covers *bench ordering* — open question.

## 4. Objective: which one, and when Monte Carlo is justified

*Expected points (linear)* → exact via matching. Default. No MC. [3-0]

*Probability of winning the h2h (nonlinear)* → Markowitz mean-variance, as an MIQP.
Operative result (Haugh & Singal, Management Science 2021), the *sign-flip*: [3-0]
•⁠  ⁠If *behind* → maximize ⁠ μ + λ·Var ⁠ (high mean, *high* variance — gamble).
•⁠  ⁠If *ahead* → maximize ⁠ μ − λ·Var ⁠ (high mean, *low* variance — protect).

Note: ⁠ μ − λ·Var ⁠ is *exactly the form already used* by the auction optimizer
(⁠ sum(mu) - lam*Var ⁠ in ⁠ domain/asta/sentiment.py ⁠). Same family → reusable.

*MC justified ONLY for the nonlinear scoring pipeline* that closed form cannot capture:
•⁠  ⁠*Defense modifier (modificatore difesa)* — bonus on a threshold of the defenders' mean
  vote (nonlinear).
•⁠  ⁠*Automatic substitutions* — bench value depends on ⁠ P(starter does not play) ⁠
  (combinatorial).
•⁠  ⁠*Opponent score distribution* for the win-prob.

Covariance breaks the matching structure (it couples pairs → quadratic → MIQP). But at the
11-slot single-lineup scale it stays small → exact or enumerable.

## 5. Mapping onto fantabot


domain/lineup/
  projection.py   # μ_i, σ_i² per matchday      <- BLOCKER: needs a stats source
  fixtures.py     # opponent strength            <- check docs/leghe-api.md
  scoring.py      # modifier, substitutions      (nonlinear pipeline)
  simulate.py     # MC: evaluate ONE lineup      [inner loop, only if nonlinear]
  optimize.py     # matching over the 11 schemi  [outer loop]
  legality.py     # REUSE domain/asta/legality.py (already bipartite matching)


Flow: news (⁠ p_i ⁠, availability) → fixtures (opponent) → stats (⁠ μ, σ ⁠ — *missing*) →
for each schema: max-weight matching → argmax → submit POST (**undocumented, needs a live
Network capture**).

Two loops, do not conflate:
•⁠  ⁠*Outer* = choose the lineup (matching / enumeration over schemi). Exact.
•⁠  ⁠*Inner* = score a candidate lineup (closed form if linear; MC only for the nonlinear
  pipeline above).

Random-searching lineups via MC is brute-forcing the outer loop — the outer loop has an
exact polynomial solution.

## Open questions (from the research)

1.⁠ ⁠When the risk-adjusted objective (variance/covariance) *breaks* polynomial matching and
   forces an ILP/MIQP.
2.⁠ ⁠How to optimize the *bench order* for auto-substitutions — no source addresses it.
3.⁠ ⁠How to estimate the *opponent's distribution* in h2h and which regime of the sign-flip
   applies in practice.
4.⁠ ⁠How to estimate the *covariance matrix* at fantacalcio scale (same-match / same-team,
   minutes coupling).

## Ordering — resolve before building

1.⁠ ⁠*Stats source* (blocks §2 — the whole thing).
2.⁠ ⁠*Objective*: expected points (linear, no MC) OR win-prob (MC). Decides whether MC is
   even needed.
3.⁠ ⁠*Fixtures endpoint* (opponent data).
4.⁠ ⁠*Submit POST* capture.

## Primary sources

•⁠  ⁠Hunter, Vielma & Zaman, Picking Winners in Daily Fantasy Sports Using Integer Programming
  (INFORMS J. on Optimization) — https://arxiv.org/abs/1604.01455
•⁠  ⁠Haugh & Singal, How to Play Fantasy Sports Strategically (and Win) (Management Science
  67(1), 2021) — http://www.columbia.edu/~mh2078/DFS_Revision_1_May2019.pdf
•⁠  ⁠Mlcoch & Hubáček 2024 (ITOR) — MIQP mean-variance-covariance —
  https://onlinelibrary.wiley.com/doi/10.1111/itor.13344
•⁠  ⁠Bayes-xG (Frontiers in Sports & Active Living 2024) — hierarchical, StatsBomb —
  https://arxiv.org/abs/2311.13707
•⁠  ⁠Matthews, Ramchurn & Chalkiadakis, *Competing with Humans at Fantasy Football: Team
  Formation in Large Partially-Observable Domains* (AAAI) — FPL as a belief-state MDP —
  https://eprints.soton.ac.uk/340382/1/fantasyFootball2012cr.pdf
•⁠  ⁠Vielma, MIT lecture (ILP DFS) —
  https://juan-pablo-vielma.github.io/lectures/AMES.121_2016.pdf
•⁠  ⁠OpenFPL (per-player expected-points projection) — https://arxiv.org/html/2508.09992v1