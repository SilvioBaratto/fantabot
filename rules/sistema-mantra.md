# Mantra System — Rules Reference

Source: https://www.fantacalcio.it/regolamenti/sistema-mantra

## Roles (11 codes)

Every player has a specific role and can only be fielded in a formation
slot matching it; "polivalente" (multi-role) players hold more than one.

| Code | Name | Notes |
|---|---|---|
| Por | Goalkeeper | |
| Dc | Central defender | Regardless of a back-3 or back-4 |
| B | Fullback hybrid | Mainly fullbacks adaptable to the outer slot of a back-3; not suited/accustomed to playing as a pure central defender |
| Dd | Right fullback | Back-4 only |
| Ds | Left fullback | Back-4 only |
| E | Defensive winger | Wide defender, usually completing a back-3; can also sit more permanently in midfield, but covering duties still come before attacking ones — often the one who drops back into the defensive line |
| M | Defensive midfielder | Groups pure defensive mediani (coverage, doubling up, interdiction), deep-lying playmakers stationed permanently in front of the defense, and markedly defensive mezzali |
| C | Central midfielder | Build-up + attacking support with at least adequate defensive contribution — regista (not the deep-lying kind) and most mezzali, including box-to-box types |
| T | Attacking midfielder | Offense-first, lighter defensive duty; sits on the "fantasy line," central or slightly wide, with a refine/late-run role — some markedly offensive mezzali who effectively play as a deeper fantasista also fall here |
| W | Winger | Pure wide attacker on the trequarti line or in a front three; prefers going to the byline and setting up a teammate over shooting themselves (unlike A) — some fantasisti who play consistently wide (e.g. in a 4-2-3-1) can also land here even without a natural wide-player profile |
| A | Attacking forward ("attaccante di raccordo") | Not primarily a penalty-box player — takes part in the buildup, either wide in a front three or supporting a central striker (alongside or as a false-trequartista tucked behind them); when wide, often plays on their "wrong" foot to cut inside and shoot, unlike W |
| Pc | Striker | Anyone playing more or less regularly near the box, pure target-man or mobile forward alike |

These map to fantabot's `data-filter-role-mantra` codes used across the
scraped CSVs (`por/dc/b/dd/ds/e/m/c/w/t/a/pc`).

### How roles get assigned

Not a rigid, mechanical process — fantacalcio.it staff weigh a mix of
factors per player: technical/tactical characteristics, recent history
(last 2–3 seasons), the tactical context they'll play in, and how
prominent the player is in the list (borderline calls lean less strict for
lower-profile players). **Roles are assigned around late July, right before
the season starts, and are not revisited for the rest of the year** —
mistakes happen, and a team's or player's tactical role can evolve
mid-season without the platform's role tag following along.

## Formations

- 11 tactical schemas available. Besides the fixed goalkeeper, every schema
  places players across four lines: defense, midfield, trequarti, attack.
- Every schema needs exactly **5 defensive-profile players** (Dd, Ds, Dc, B,
  E, M) and **5 offensive-profile players** (C, T, W, A, Pc) — the offensive
  side's role mix is specifically tuned so every one of the 11 schemas has
  roughly equal tactical potential on paper.
- Where a schema slot lists two roles, they're interchangeable alternatives
  — and stay interchangeable through substitutions too.

## Bench

- 12 players, at least one goalkeeper, ordered by **preferred
  substitution-entry sequence** — never lay it out Classic-style by position
  (P-D-C-A). The order is what actually drives the substitution engine, so
  generally list more attacking players, or whoever you rate as the
  higher-upside options, earlier.

## Lineup Submission

- Pick the tactical schema first, then assign eligible players to its slots
  — the official app/web UI flags valid, adaptable, and invalid choices as
  you go.

## Out-of-Position Rules

**Blocked at initial lineup submission** (even though some of these become
possible later, during forced substitutions, with a malus):
- B, Dd, or Ds playing as Dc
- Dd as Ds or vice versa
- E as M (except M/C hybrids)
- M as E (except E/W hybrids)
- W as T (except T/A hybrids)

If all 10 outfield starters end up with a vote and no substitution is
triggered, the engine doesn't run at all — the lineup is scored exactly as
submitted, malus included, for whatever out-of-position assignment the
platform did allow through.

**Important**: deliberately fielding an out-of-position player should never
be treated as a strategic choice — it's meant only for genuine emergencies
(a roster hit hard by injuries/suspensions). Doing it anyway is risky: the
very first time a substitution is needed, the algorithm hunts for
malus-free solutions first, which often restructures the lineup in ways the
manager didn't intend and can trigger substitutions they wouldn't have
chosen.

**Formation-specific exception**: W and T are normally interchangeable
(with the usual -1 malus), *except* in the 4-1-4-1 formation, where W can
never sub in for T (or vice versa), not even with a malus. fantacalcio.it
publishes a full per-formation compatibility table as a separate download
that isn't captured here — treat the above as the general rule, not the
complete matrix.

## Substitution System (Three Modes)

All three modes replace missing/no-vote outfield players **as one combined
block**, not one at a time — see the combination-search mechanic below.

### BASIC (default)

1. **Optimal** — a solution matching the original schema exactly, no
   out-of-position malus.
2. If none exists: **Efficient** — a malus-free solution under a *different*
   schema (a formation change).
3. If neither exists: **Adapted** — a solution with one or more -1
   out-of-position penalties; here there's no preference for the original
   schema over any other available one.

### EASY

Formation never changes, full stop:
1. **Optimal** within the chosen schema.
2. **Adapted** (penalties) — but still only within that same schema; a
   formation change is never on the table.

### MASTER

Bench order dominates over keeping the original formation from the start:
1. **Optimal/Efficient** (no malus) in any schema — whether or not a
   formation change is needed, the engine always tries hardest to get the
   earliest-listed bench players onto the pitch.
2. **Adapted** (penalties) otherwise — again, no schema is preferred over
   another.

### The combination-search mechanic (applies to all three modes)

When N outfield starters need replacing, the engine doesn't test bench
players one at a time — it tests *combinations* of N bench players, in an
order driven by bench position. Worked example from the source: 3 starters
need replacing, 5 rated bench outfield players in order A-B-C-D-E — the
engine tries combinations in this exact priority: `ABC, ABD, ABE, ACD, ACE,
ADE, BCD, BCE, BDE, CDE`. For each combination, it checks whether an Optimal
fit exists (using multi-role flexibility where relevant); if no combination
yields one, it restarts the same combination order looking for an Efficient
fit; failing that, restarts again looking for the least-total-malus Adapted
fit (ties broken by bench order once more). If literally nothing works even
then, it restarts the whole search considering one fewer player to replace
— i.e., the team plays a man short.

**Gotcha**: because the engine reallocates all 11 positions (starters +
subs) together, a same-role swap can happen during this reallocation, which
can make the malus land on a *different* same-role player than you'd
expect (e.g. two Dc starters plus an incoming Dc sub — the engine may
assign the penalty to any of the three, picked in no particular order,
since they're interchangeable for scoring purposes). The point is: a
required malus will always be applied *somewhere* in that group, just not
necessarily to the specific player you'd have guessed.

### Goalkeeper substitution

Always the first substitution when the starter is missing — reserve GK
priority follows bench order if more than one is available. This uses up
one of the substitution allowance's slots, in leagues that cap the total
number of subs (irrelevant if unlimited).

## Modifiers and Optional Factors

**Why Mantra doesn't use Classic's modifiers**: Classic's traditional
modifiers (especially defensive ones) exist to curb the dominance of 3-4-3-
style formations, rewarding less extreme tactical choices and players who
perform well without scoring often — a mathematical patch for a structural
imbalance. Mantra doesn't have that imbalance to begin with, since its 11
schemas are pre-balanced by design — layering a Classic-style modifier on
top would introduce distortion rather than fix anything.

Compatible optional factors instead:

- **R-Factor (Rendimento)** — rewards/penalizes a squad's overall quality by
  counting how many players got at least a "sufficient" base vote (≥6, a
  passing grade in the Italian school-grading convention these votes
  follow). More such players → a better-performing squad, reinforcing
  Mantra's "build a genuinely solid team" framing.
- **D-Factor (Difensivo)** — bonus points (or a malus to the opponent) for
  the defensive unit's performance, measured by average vote. Eligible pool
  is the 5 defensive-profile players in the schema (Dc, B, Dd, Ds, E, M); if
  more than 5 hold those roles, the 5 with the best votes are used — but at
  least 3 of those 5 must specifically be Dc/B/Dd/Ds (E and M can only fill
  the remaining up to 2 slots). A **5+1** variant additionally folds in the
  goalkeeper (average taken over 6 players total, not a swap — it can't be
  configured as "GK + only 4 outfield defenders"). Like the Performance/
  Fair-play factors elsewhere, D-Factor simply doesn't activate if the
  required player count isn't reached (only possible when playing
  short-handed) — no partial-average fallback.
- **Fair-play** and **Captain** bonuses are also available, shared with
  Classic — see `leghe-private.md`.

R-Factor and D-Factor are mutually exclusive (their logic overlaps too much
to combine) — pick one or neither, both optional.
