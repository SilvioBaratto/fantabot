# Private Leagues (Leghe Private) — Rules Reference

Source: https://www.fantacalcio.it/regolamenti/leghe-private

There's no single mandatory rulebook — the platform is deliberately built to
be customized, and this page is fantacalcio.it's own guide to the choices a
private league needs to make, not a fixed ruleset. Two independent axes:
**game system** (Classic vs Mantra) and **game type** (head-to-head leagues
vs 1-vs-all).

## Player Availability

- **Multiple availability** (a player can be owned by more than one team) is
  effectively required for 1-vs-all leagues, optional otherwise.
- **Single availability** is recommended for friend leagues up to ~12 teams
  (Classic or Mantra, doesn't matter) — beyond that, lean on frequent market
  sessions instead of relaxing to multiple availability.

## Roster Configuration

Roster size should generally scale with how infrequently repair markets run
— fewer repair sessions → bigger rosters are more useful.

### Classic
- Per-role counts can be fixed or a flexible range (or a hybrid: some roles
  fixed, others ranged). There's no single "roster size" setting — the total
  is just the sum of the role sub-counts.
- Hard system min/max per role: Goalkeepers 1–15, Defenders 6–25, Midfielders
  6–25, Forwards 4–25. That puts the theoretical roster size at 17–90.
- Recommendation: given how much modern football rotates squads, lean toward
  bigger, more flexible rosters — it widens tactical options and makes
  trades much easier since you're not locked into role-for-role swaps.

### Mantra
- Minimum 23 players including 2 goalkeepers, no per-role slot constraints
  at all — the manager builds the squad like a real-world coach would (max
  90: 15 GK / 75 outfield).
- Recommendation: 28–30 is the sweet spot; alternatively give each manager a
  free range (e.g. 25–32) instead of a fixed number.

## Budget

- Standard allocations when using official market valuations (quotazioni):
  250 credits for a 25-player Classic roster, 280 for a 28-player Mantra
  roster — adjust proportionally for non-standard roster sizes.
- For a from-scratch friend auction (not tied to quotazioni), the credit
  pool can be anything, as long as it's identical for every team — a bigger
  pool just inflates prices without changing the underlying balance.

## Valid Formations (Classic only — Mantra's formation grid is fixed)

Recommended standard set: **3-4-3, 3-5-2, 4-3-3, 4-4-2, 4-5-1, 5-3-2, 5-4-1**
(these are fantabot's `VALID_FORMATIONS` in `models.py`). Leagues can
optionally extend this with emergency-only formations like 3-6-1 or 6-3-1.

## Valuation Reference

Two paths: rely on the official quotazioni, or let a friend auction set
prices freely. Either way, fantacalcio.it also publishes an **FVM** (Fanta
Valore di Mercato, expressed in thousandths — this is the `fvm` column in
our scraped quotazioni CSVs) as a reference indicator of a player's current
auction-worthy value. 1-vs-all leagues are effectively forced to use the
official quotazioni. Quotazioni themselves are maintained by a dedicated
staff and **updated weekly** from two combined algorithms evaluating
appearances/performance (see `algoritmo-quotazioni.md` for the detailed
mechanics) — but for a from-scratch friend auction, the recommendation is to
*not* anchor to quotazioni and let the market set the price, using FVM only
as a loose reference point.

## Data Sources (3 independent choices)

### Role source
- **Classic**: defaults to fantacalcio.it's own role list. For a small
  number of genuinely borderline ("al limite") players, an in-platform
  editor can override the assignment — any such override must be clearly
  communicated to every participant before the pre-season auction.
- **Mantra**: roles and quotations share a single fixed source; there's
  nothing to choose.

### Vote source
Choice of: traditional editorial (**Fantacalcio**), algorithm-based
(**Voto Statistico FG**, built on the "Alvin482" algorithm), or a synthesis
of multiple sources (**Redazione Italia**). See `fantacalcio.md` for how
each behaves.

### Bonus/malus source
Always fantacalcio.it — not a choice. What *is* configurable:
- Assist bonus: each of the three assist categories (gold/standard/soft) can
  be excluded or given its own value. This page states the baseline default
  is 1 for all three — that's accurate for existing leagues (see
  `assist.md`: 1-for-all is the long-standing traditional default, and it's
  only newly created leagues from 2026/27 on that default instead to
  soft=0.5/standard=1/gold=1).
  - This page separately calls out a set-piece assist ("assist da fermo")
    getting +0.5 by default elsewhere on the site, with many leagues
    equalizing it to a normal assist's value — `assist.md` clarifies
    set-piece assists aren't actually a 4th category, they just get sorted
    into Soft/Standard/Gold like any other assist. The +0.5 figure here
    likely reflects an older, simpler flat-value convention that predates
    the Quality Assist system.
- "Contributo al gol" (assist-chain contribution) toggle — off by default,
  can be turned on and given its own value.

## Bonus/Malus Table

Values and role-grouping are both fully configurable. Role grouping differs
by system: **Classic** splits by P/D/C/A; **Mantra** only splits goalkeeper
vs. outfield player (not the 11 granular Mantra roles).

Recommended baseline (same as `leghe-private`'s own suggested defaults):

| Event | Points |
|---|---|
| Goal scored (any type, incl. penalties) | +3 |
| Goal conceded | -1 each |
| Yellow card | -0.5 |
| Red card | -1 |
| Missed penalty | -3 |
| Saved penalty | +3 |
| Assist | +1 |
| Dead-ball assist ("assist da fermo") | +0.5 (many leagues equalize this to a normal assist instead) |

Optional, off by default: own goal (-2), goalkeeper clean sheet (+1),
decisive-draw goal (+1), decisive-win goal (+1), Panini "player of the
match" (+0.5).

## Bench & Substitutions

### Classic
- **Structure**: bench size and role order/distribution are fully
  league-defined — free-form or locked to a specific pattern, your call, but
  define it clearly up front to head off disputes.
- **Substitution modes** (all evaluated in bench order):
  - **Traditional** — same-role substitutes only.
  - **Hybrid** — same-role substitutes first; a formation change only as a
    last resort when no same-role sub is available.
  - **Dynamic** — a formation change is tried *before* falling back to a
    same-role sub, whenever both are possible.
  - A formation-change substitution is only allowed if the resulting
    formation is itself one of the league's allowed formations.
- **Max substitutions**: leagues must define a cap on how many starters can
  be replaced by bench reserves per match (for absence or no-vote).

### Mantra
See `sistema-mantra.md` for the full BASIC/EASY/MASTER breakdown — that
content lives there rather than duplicated here.

### Shared options (both systems)
- **Booked (yellow-carded) player with no vote**: either treat as an
  ordinary no-show (auto-subbed), or keep them "on the pitch" with an
  official 5.5.
- **"Riserve d'ufficio" (official standby reserves)**: a fictional player
  who comes on with an admin-set default score (typical: 4 outfield, a
  separate — usually lower — value for goalkeepers, e.g. 2) whenever you
  can't reach 11 rated players. The idea mirrors what a real coach does when
  short on fit players: field a youth-team kid rather than play a man down.
  Configurable to: only kick in up to the normal substitution cap, or be set
  to *always* guarantee reaching 11 rated players (even via a hypothetical
  11 standby call-ups if literally no starter gets a vote), or be limited to
  covering a single missing player only.

### Recommendations
- Fixed-size, reasonably deep bench — the source explicitly calls **7 too
  few**, i.e. go deeper than that.
- Free bench order, but lean toward allowing the formation-change fallback
  (Hybrid-style) over pure same-role-only substitution.
- Minimum 5 substitutions, with a recommendation to go higher — a real coach
  never has to worry about a starter not getting on the pitch the way a
  fantasy manager does, so a deeper cushion offsets that gap. For the same
  reason, the guide leans favorably toward "riserve d'ufficio," including
  the always-reach-11 option — playing with a rated 10 or fewer is called
  out as something that never happens in real football and mostly just
  causes arguments.
- For Mantra specifically, the guide recommends **MASTER** mode as giving
  the best experience.

## Lineup Submission

- **Deadline**: a specific number of minutes before the day's first kickoff
  — recommendation is 5 minutes.
- **Submission channel**: direct platform entry is the baseline; leagues can
  optionally allow emergency backup channels (SMS, chat) within the same
  deadline — recommended only for genuinely exceptional cases.
- **No lineup submitted**: three options — (a) re-field the manager's last
  valid lineup from that competition, if one exists (recommended — "if the
  point is to have fun, matches should get played"), (b) assign a political
  score/forfeit, or (c) an automatic loss.

### SWITCH (optional, BASIC or PLUS)

Important mechanical distinction: **SWITCH is not a substitution** — it's a
change applied to the *submitted lineup itself*, resolved *before* any
normal substitution logic runs. It triggers purely off one condition: was
the player in your starting XI actually a real-world starter for their
club or not? Nothing else (not their vote, not an in-match injury) affects
whether SWITCH activates.

Worked example: you field Tizio with Caio (on your bench) set as his
SWITCH cover. If Tizio does *not* start in reality, Caio replaces him in the
lineup outright — regardless of what happens to Caio afterward (starts,
comes on later with a vote, ends up with no vote, or isn't used at all).
Only after this swap does the platform run its normal bench-order
substitution process on top of the resulting lineup.

PLUS additionally allows a formation change at the moment SWITCH activates;
BASIC keeps the original formation.

## Optional Modifiers

- **Defense modifier** (Classic only) — a family of mathematical tools
  meant to counter Classic's built-in bias toward always fielding the most
  attacking formation available. Many variants exist for every department;
  the free Leghe di Fantacalcio platform ships the most common variant per
  department and lets you tune the bonus/malus *output*, but not the
  underlying formula. Entirely optional.
- **D-Factor** (Mantra only) — mechanically similar in spirit to the
  Classic defense modifier, but a different goal: it doesn't push toward any
  particular tactical shape, it just rewards investing credits in the
  defensive part of the roster rather than stacking attacking talent.
- **Performance factor ("Rendimento")** — available in *both* systems.
  Rewards/penalizes the squad's overall performance, valuing players who do
  unglamorous defensive work and those who perform consistently even
  without a goal/assist bonus attached.
- **Fair-play bonus** — available in *both* systems. Rewards a team that
  finishes a match with no player receiving any card. (This page doesn't
  actually state a point value for it — don't assume +1 without checking a
  league's own settings.)
- Both Performance and Fair-play require **11 rated players** as a
  prerequisite — neither activates if the team is forced to play short.
- **Captain** (optional, both systems) — pick a captain and vice-captain,
  apply an extra bonus/malus multiplier based on their vote. Choose between
  one fixed captain for the whole season, or a new pick every match.

## Head-to-Head Scoring (only relevant for head-to-head formats, irrelevant for 1-vs-all)

- A first "goal" is awarded once a team crosses a **point threshold**
  (recommended: 66), then one more goal per additional **band ("fascia")**
  of points (recommended width: 4–6 points, constant or individually
  customized).
- Optional correction rules, recommended only if every participant
  understands them:
  - **Limita Vittoria**: two teams land in the same band, but the higher
    raw score still wins outright once the point gap exceeds a set value
    (overrides the "same band → draw" default).
  - **Limita Pareggio**: two teams land in different bands, but the result
    still stays a draw as long as the point gap stays under a set value
    (overrides the "different band → someone wins" default).
  - **Autogoal rule**: a team scoring below a set floor (example given: 55)
    gives its opponent a bonus goal.

### Tiebreakers
- **Head-to-head leagues**, in order: total fantasy points → goals scored →
  goals conceded → goal differential → head-to-head-only standings (points
  earned in mutual meetings, goals not counted).
- **1-vs-all or Formula 1 formats**: a reasonable criterion instead is
  rewarding whoever won the most individual matchdays.

### Home advantage
Optional +2 per match, described as one of the traditional pillars of any
head-to-head ruleset — does **not** apply in Battle Royale.

## Competition Formats

1. **Campionato (calendar league)** — round-robin, home/away (possibly
   multiple round-trips); calendar styles: *all'italiana* (return leg keeps
   the same fixture order), *a specchio* (return leg exactly mirrors/reverses
   the order), or fully *asimmetrica* (custom).
2. **Old Champions (group → knockout)** — mimics the old Champions League
   format: a group stage with its own calendar, whose qualifiers move into a
   second head-to-head knockout phase.
3. **Tabellone (single-elimination cup)** — Coppa Italia-style tennis
   bracket, round by round down to a winner.
4. **Somma Punti (1-vs-all)** — no opponent; matchday points accumulate
   directly into one overall standings table.
5. **Formula 1 style** — same shape as 1-vs-all, but the standings are built
   from grid-style points awarded for each matchday's *placement/ranking*
   among all managers, not from raw accumulated fantapoints.
6. **Battle Royale** — every matchday, each participant plays a head-to-head
   fixture against *every other* participant that round, earning standard
   win/draw/loss points from each. Worked example from the source: 10
   participants → 9 fixtures each per matchday; 4 wins + 3 draws + 2 losses
   → 15 standings points that round (consistent with 3 points/win, 1/draw).
   Purpose: sharply reduces the impact of good/bad luck in calendar pairings
   that a normal round-robin has.
7. **Highlander** — progressive elimination of one or more participants each
   round until one manager remains. Different logic from every other
   format: except on the very last matchday, surviving matters, not
   winning. Recommended as a secondary competition alongside a main one,
   not as the only competition in a league.

### Odd number of teams
Choose between a bye round, or an automated neutral "ghost team" opponent —
the source doesn't state what score the ghost team is assigned, so don't
assume it matches the 66-point goal threshold without checking a league's
own settings.

## New Mid-Season Entrant (1-vs-all only)

Recommended: give the new participant 66 political points for every
matchday already played in that competition.

## Market Rules

### Scheduling
- Fixed dates, set in advance for the season. The number of sessions is up
  to the league, but session dates must never overlap — **except** swap/
  exchange sessions, which can always run concurrently with anything, and
  "mercati a quotazione" (see below), which can have at most 2 overlapping
  sessions.

### Market Types
- **Off-line auction** (12.2a) — run outside the platform (in person or
  remote, optionally via dedicated software like Fanta Asta Live). Common
  formats: *a chiamata* (nomination-style, players called out one at a
  time), *random* (whole-pool or per-role), *draft*, or *sequential
  ordering* (alphabetical, by role, by price, etc.).
- **Mercati a quotazione** ("quotation markets", 12.2b) — near-mandatory for
  1-vs-all leagues, exclusive to multiple-availability setups: not a live
  auction at all, it's a continuous buy/sell market where the price is
  pegged to the player's official quotazione.
- **Asta Smart** ("Smart Auction", 12.2c) — a public-auction system with the
  mechanics of well-known online-auction sites, usable straight from a
  phone, bidding against league-mates in real time.
- **Offerte in Busta Chiusa** ("sealed-bid", 12.2d) — a blind-offer format:
  no one sees what anyone else bid, and depending on configuration you may
  not even know whether anyone else bid on a given player at all.
- A league can mix formats — using one type pre-season doesn't prevent using
  a different one for repair sessions.

### Purchases & Releases
- **Purchase cap**: each repair session should define a max number of buys
  per team (or leave it unlimited). A player no longer in Serie A
  ("astericato"/asterisked) can be replaced without counting against that
  cap.
- **Trades/swaps**: leagues must separately decide *whether* trades between
  teams are permitted, in which sessions, and under what constraints (this
  is a permission rule, distinct from the scheduling-overlap allowance
  above).
- **Credit recovery ("Fantamilioni") from releases**: first decide whether
  leftover pre-season credits roll over into later repair sessions. Then
  define what a release is worth (flat value, % of purchase price, % of
  FVM, % of current quotazione, or some other formula).
  - **Voluntary release** — dropping a player who's still active in Serie A.
  - **Mandatory release** — forced when a rostered player leaves Serie A
    (transfer or contract end); decide whether/how the manager can replace
    them immediately vs. having to wait for the next session, and what
    happens if no further session exists that season.
  - **Release pledge ("promessa di svincolo", optional)** — in an
    auction/sealed-bid market, a manager can pre-pledge to release a named
    roster player *if and only if* they win a given bid. The pledge only
    triggers (and its credit value is only credited) on an actual win — the
    pledge itself does **not** free up credits for the current bid, it only
    reserves the roster slot. In the **PLUS** variant, a manager can
    additionally apply the credits the pledge *would* free up toward
    raising their current bid on the player they're trying to buy.

### Divisions
Only available when the league uses single availability. Splits one league
into up to 5 tiers (e.g. Serie A/B/C), each with its own fully independent
market — no cross-division market interaction — while cross-division
*competitions* (not markets) are allowed.

### Recommendations
- Keep the market structure simple and transparent, covering every point
  above; complicated rules mostly just create confusion.
- Pick a market type every participant genuinely understands, especially
  for auctions and sealed-bids.
- Avoid trade windows late in the season — it lowers the risk of collusive/
  suspicious activity.
- Don't build replacement rules keyed to injury duration — recovery
  timelines are inherently uncertain and this mostly just generates
  disputes.

## Postponed / Rescheduled Matches (league-competition angle)

Two options: wait for the makeup match, or assign political 6's to the
players on the affected rosters. Matches played within the normal window
between the end of the previous round and the start of the next one are
*not* treated as anomalous postponements — the political-6 option doesn't
apply to those. Recommendation: use political 6's when the situation calls
for it — open-ended waiting is particularly discouraged for cup-style
competitions, since one suspended round can end up blocking rounds that were
already scheduled before the makeup match happens.

(See `fantacalcio.md` for the more detailed vote-assignment mechanics around
postponed and suspended matches.)

## Code of Conduct & Penalties

Leagues are expected to write their own "small penal code" — what counts as
misconduct (collusion, forfeit abuse, market manipulation, etc.) and what
sanction applies. fantacalcio.it explicitly stays out of adjudicating
disputes between league members — a clearly written rule up front is what
head off arguments, not an appeal to the platform.

## Participation Fees & Prizes

Entirely at the organizer's discretion. Recommendation: keep the game
recreational — avoid cash stakes where possible (also generally not legally
regulated for this use), or keep them symbolic (pizza-and-beer money) at
most; trash talk, on the other hand, is free and encouraged.
