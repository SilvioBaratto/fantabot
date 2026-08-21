# Assist Rules — Reference

Source: https://www.fantacalcio.it/regolamenti/assist

## Definition

An assist is credited for a pass that contributes to a goal — the pass must
be **intentional**.

## Intentionality

- The pass has to be something it's reasonable to presume was aimed at
  finding a teammate — not accidental, not a mistake.
- **Excluded**: a clear shot attempt that a teammate happens to intervene on
  and score from — that's not treated as an intentional pass.
- **Presumed intentional by extension**: situations where the play's
  dynamics show the passer was equally going for a shot on goal *and* a
  possible teammate tap-in — the named examples are shot-crosses and
  flick-ons toward the far post. These get the assist.

## Deflections

The source spells out five specific cases:

| # | Situation | Result |
|---|---|---|
| 2a | A defender's touch/attempted play doesn't actually stop the ball reaching its original intended recipient | Assist stands |
| 2b | A defender's touch/attempted play changes the trajectory enough that the ball reaches a *different* player than intended | No assist |
| 2c | A teammate's touch doesn't significantly change the trajectory before the ball goes in | Assist still credited to the original passer |
| 2d | A teammate's touch *significantly* changes the trajectory, sending it to another teammate who otherwise wouldn't have had a chance to shoot | No assist to either the original passer or the deflecting teammate — the deflecting player lacked passing intent too (see note) |
| 2e | Attacker and defender both contest the ball as it arrives; whoever gets the first touch, if the ball ends up with the attacker right after and they score | Assist awarded, assuming the base intentionality criteria are otherwise met |

Note on 2c/2d: "teammate's touch" here specifically means a case where it's
reasonable to presume the touching player *wasn't* trying to make their own
intentional pass — if they were, standard assist rules just apply directly
to that second pass instead (crediting them, not the original passer).

## Assist Quality Tiers ("Quality Assist")

Whether something is an assist at all is decided by the rules above; this
is a separate, optional further classification fantacalcio.it applies to
every assist it grants, by how much advantage the pass created and how
technically/aesthetically demanding it was:

- **Soft** — the advantage created was slight/limited.
- **Standard** — a concrete, tangible advantage.
- **Gold** — either the maximum possible advantage (the scorer finishes
  with extreme ease), or the pass — or the buildup action right before it —
  stood out for technical/aesthetic quality or difficulty.

**Set-piece assists ("assist da fermo") are not a separate category** —
they get sorted into Soft/Standard/Gold using the exact same criteria as
open-play ("in movimento") assists, at the editorial staff's discretion.
(An older, simpler flat-value convention for dead-ball assists is also
referenced elsewhere on the site — see the note in `leghe-private.md` — but
this dedicated page treats them as just another input to the same
three-tier system, not a 4th tier.)

## Point Values (fully configurable per league)

- To ignore the quality tiers entirely, set all three to the same value —
  **the traditional/legacy default is 1 point for all three**, and this
  remains freely changeable.
- To actually use Quality Assist, lower Soft and raise Gold — recommended
  scale: **0.5 > 1 > 1.5**.
- **Starting with the 2026/27 season, for newly created leagues
  specifically**, the default changes to a hybrid setup: Soft = 0.5,
  Standard = 1, Gold = 1 (existing leagues keep whatever they already had
  configured, and this default is — as always — freely adjustable).
