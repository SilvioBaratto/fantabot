# Player Valuation Algorithm — Rules Reference

Source: https://www.fantacalcio.it/regolamenti/algoritmo-quotazioni

## Overview

fantacalcio.it runs a proprietary algorithm that updates player market
valuations through the season. The opening price is editorial judgment;
updates after that follow systematic rules balancing recent form against
season-long consistency.

## Key Metrics

- **QI** (Quotazione Iniziale) — opening price, based on history, potential, reputation.
- **QA** (Quotazione Attuale) — internal current valuation, tracked with decimal precision.
- **QAA** (Quotazione Attuale Arrotondata) — the publicly displayed value (QA rounded).
- **Fantamedia** — average fantavoto across the player's appearances this season.

This maps to the `qi`/`qa`/`fvm` columns already in our scraped
`quotazioni_classic.csv` / `quotazioni_mantra.csv`.

## Update Algorithm

**Step 1 — most recent performance**: the latest match rating is the
foundational input for the update, using the "expected return" principle
below (no longer the sole factor on its own).

**Step 2 — fantamedia and appearance rate** (only kicks in from the 5th
match after a player *enters the quotazioni list* — not 5 matches played in
general, which matters for anyone added mid-season): fantamedia alone isn't
enough — appearance rate matters too. Worked example from the source: two
players both averaging a 6.80 fantamedia, but one played 90% of matches and
the other 50% — the 90%-availability player should clearly be worth more.
Appearance rate is calculated from the season start, unless the last-10-
match rate is higher, in which case that's used instead (this specifically
protects a player who only became a starter partway through the season).
Purpose: this step acts as an **accelerator** — it's what lets a
cheap-but-overperforming player gain value faster, or an
expensive-but-flopping one lose it faster, than step 1 alone would.

**Exception**: if a player missed the most recent match, step 2 can only
push the valuation down, never up.

## "Expected Return" Principle

The rating-to-valuation curve isn't linear — the same fantavoto moves a
cheap player's price up more, proportionally, than it moves an expensive
player's. Elite (high-QI) players need a proportionally higher fantamedia
just to hold their valuation, let alone grow it.

## Precision & Rounding

- Internally the algorithm keeps decimal precision (e.g. 13.0 → 12.4 after
  an adjustment).
- Rounding to the public QAA: 0.01-0.50 rounds down, 0.51-0.99 rounds up.
- The next update always recalculates from the stored decimal QA, not from
  the rounded QAA — so rounding doesn't compound.

## Valuation Floor ("Paracadute")

High-priced players get downside protection based on their opening QI — not
named player-by-player, purely threshold-based. **Important cross-system
detail**: this always uses the player's *Classic* QI, even for a player
being evaluated under Mantra — there's no separate Mantra paracadute
threshold.

| Role | QI threshold | Minimum QAA |
|---|---|---|
| Goalkeeper | ≥ 14 | QI ÷ 2 |
| Defender | ≥ 12 | QI ÷ 2 |
| Midfielder | ≥ 16 | QI ÷ 2 |
| Forward | ≥ 24 | QI ÷ 2 |

For an odd QI, use (QI − 1) ÷ 2 instead. E.g. a defender with QI=14 can never
be valued below 7, no matter how they perform.

## Bounds

- QAA minimum: 1
- QAA maximum: 60

## Timing

- Updates normally follow each match week.
- A postponed match freezes the affected players' valuations until the
  reschedule, then folds the result into the next regular update — this is
  meant to avoid interfering with active transfer windows.
