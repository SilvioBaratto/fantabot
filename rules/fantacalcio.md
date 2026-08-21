# Fantacalcio — Vote Assignment Rules

Source: https://www.fantacalcio.it/regolamenti/fantacalcio

> Note: despite the URL, this page does not cover the foundational game
> mechanics (roles, formations, squad composition, scoring formula, captain
> rules, auction). It's scoped to how individual match votes get assigned in
> edge cases. See `sistema-mantra.md` and `leghe-private.md` for the rest.

## Vote Sources

- **Fantacalcio.it Editorial ("Redazione Fantacalcio.it")** — the *only*
  source offering **live** votes; these are designed explicitly for fantasy
  game use, not journalistic match critique. Draft votes go through a
  full-staff review pass for quality/consistency before final publication,
  roughly 1 hour after the match ends.
- **Voto Statistico** — algorithm-based (branded "Alvin482"), no editorial
  subjectivity, aimed at precision/reliability over narrative judgment.
- **Redazione Italia** — a "synthesis vote" blending the best of the
  editorial sources with the Statistico/Alvin vote; the idea is that passing
  a rating through multiple independent judgments smooths out anomalies and
  extreme outliers. By construction it **cannot** offer live votes.
- Statistico and Italia data become available the day *after* each match is
  played — not live, unlike the Fantacalcio.it editorial source.

## Why Votes Aren't Compared

Votes are never meant to be compared — not across sources, not across
players, not across matches — because match context (opponent, game state,
timing of the action) always differs. What actually normalizes outcomes
across all of that is the bonus/malus system: every goal is worth +3, every
red card -1, every yellow card -0.5, independent of the base vote given.

## "No Vote" (S.V.)

There is no rule requiring a minimum number of minutes played for the
Fantacalcio.it editorial source to assign a vote — it's always the editor's
call whether a player's involvement in the match earned a rating. (The
Statistico source handles this differently/more mechanically.) In practice
this means a player can get rated after 5 minutes on the pitch, or stay S.V.
after 20 — different editors, working independently, apply their own bar for
when a S.V. is warranted.

### S.V. + bonus/malus interaction

| Scenario | Outcome |
|---|---|
| Booked player receiving S.V. | League-configurable: either stays S.V. (auto-subbed), or auto-assigned a preset score (recommended 5,5, which already bakes in the yellow-card malus) — if you use formation modifiers, that preset score is treated as a normal vote for modifier purposes too |
| Sent off mid-match, receiving S.V. | Fantavoto is automatically 4 |
| Sent off after the match has already ended, receiving S.V. | Stays S.V. |
| Any other bonus/malus event alongside S.V. | 6 + that bonus/malus value |
| Goalkeeper S.V. after playing only a few minutes | Treated as a normal substitution; the clean-sheet bonus (if used) does **not** apply |

## Card Penalties

- Two yellows, a direct red, or yellow+direct red are all scored identically.
- Max card-related malus is -1 (there's no -1.5 tier) — this has never been
  otherwise.
- Cards shown off the pitch (bench, tunnel, etc.) count if the player was on
  the pitch for that match at some point — including if the card comes
  *after* the match has already ended. Only an unused substitute who never
  entered the match is excluded.

## Postponed / Rescheduled Matches

- **Within the normal gameweek window** (the stretch between the end of the
  previous round and the start of the next one): treated as a completely
  normal match — votes count for that round as usual.
- **Outside that window**: league admins choose, per league rule set ahead of
  time, between:
  - (a) waiting for the makeup match (or including an early/anticipated one), or
  - (b) assigning a "political 6" to **every player on the roster** for the
    teams involved — starters, bench, injured, and suspended alike. If the
    league uses formation modifiers, that political 6 is treated as a fully
    normal vote for modifier purposes (it is *not* exempt from modifiers).
- **Transfer-window edge case**: if a player switches clubs and a postponed
  match lands after the move, two outcomes are possible:
  - If the player had *already* played (got a vote or an S.V.) for their old
    club in that same round, that earlier performance stands and the makeup
    appearance for the new club is voided — a player can't hold two
    performances for the same round.
  - If the player did **not** actually appear for the old club in the first
    match that round (being an unused substitute doesn't count as
    appearing), then the makeup-match performance for the new club *is* the
    valid one for that round.

## Suspended-and-Resumed Matches

If a match starts, gets suspended mid-play (weather, etc.), and its
resumption is scheduled *past* the start of the next gameweek, leagues choose
between:

- **(a) Close out the round with political 6's**: assigned to every player
  on both teams' rosters — including anyone not on the pitch at the moment
  of suspension for tactical reasons, suspension, or injury. No bonus/malus
  attached.
- **(b) Wait for the match to actually finish**, which comes with several
  consequences:
  - The final fantasy match report covers both playing segments combined,
    and will likely include more than the usual 11 (up to 14 with subs)
    players per team, since the real coaches may field different lineups
    upon resumption.
  - All bonus/malus from both segments count as normal (including for
    players who end up with no vote but did pick up a bonus/malus).
  - No partial votes are published after the suspension — everything (votes
    and any S.V.s for players who took the pitch across either segment) is
    released only once the match fully concludes.
  - Choosing this path means accepting its full mechanics regardless of
    which vote source the league uses.
  - **Transfer-window exception**: if the suspension-to-resumption gap
    straddles a transfer window, and a team involved in the resumption
    signs a player who *already played their match with a different team*
    that same round, that player's presence in the second playing segment —
    vote and any bonus/malus — is voided entirely; the performance already
    recorded with the other team for that round stays valid.
- If the resumption instead happens **before** the next gameweek starts, the
  political-6 shortcut isn't available at all — the round must be scored
  from the actual resumed result.

## VAR-Annulled Actions

Whatever the action — a goal, a save, an error, a penalty call, anything —
if VAR overturns it, it has zero effect on the final vote. If an editor had
already adjusted a vote in reaction to the (later-overturned) action, that
vote is reverted to what it was immediately before the action.
