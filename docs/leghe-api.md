# leghe.fantacalcio.it — apileague API reference

Reverse-engineered from the live site on 2026-08-19 (league
`legamiallerotaie2`, competition id `177318`), **revised 2026-08-26** against
both leghe on the account. That pass corrected four claims — how per-lega tokens
are obtained, what `[2, 28]` means, that the competition id is stable, and that
the league-wide player list had never been called — and added
`GET /onboarding/v1/league/players`. The frontend (`leghe.fantacalcio.it`)
is an Angular SPA that talks to a separate API host, `apileague.fantacalcio.it`,
for almost everything — the pages themselves are mostly SSR shells + client-side
data fetches.

**Revised again 2026-09-02 (evening)** by grepping the shipped Angular bundle rather
than the live pages — 227 lazy chunks fetched and searched for the `microserviceName` /
`serviceName` / `getEndPoint` triple every service class declares. That pass found the
calendar, added five endpoints below, and closed two of this doc's own gaps: the numeric
role codes are resolved, and the whole-lega rosters were never behind a missing endpoint
at all. See "How this was found", step 4.

This doc covers the read (GET) endpoints **and** the lineup submit — the latter
captured live 2026-09-02 (see "Lineup — `gaming/v1`" below). **Lineup submission is
no longer a gap.** Auction bidding runs on FantaLab, not here (`docs/fantalab/`).
The lineup endpoints live under a **different microservice, `gaming/v1`**, not
`onboarding/v1` — which is why they never appeared in the Angular bundle's
`onboarding`/`market` service classes and had to be captured from the live page.

## Base URL

```
https://apileague.fantacalcio.it
```

A staging host exists (`apiBaseUrl` in the bundle points to
`https://apileague.stagin…`, truncated in the grep that found it) but the full
value wasn't captured — don't guess it if you need it, re-grep the bundle
(see "How this was found").

## Authentication

Every request needs **two headers**:

| Header | Value | Scope |
|---|---|---|
| `app_key` | `ICiELOObd5DF5uJEATi77CRvHiiRuMU0` | Static, public — shipped in the frontend's JS bundle. Same for every user. Not a secret; safe to hardcode. |
| `Authorization` | `Bearer <league-scoped JWT>` | Per-user **and** per-league. Must be the token described below — the account-level token does *not* work here. |

### Getting the bearer token

After a normal browser login, the Angular app stores auth state in
`localStorage["LEAGUES2024_LOCAL"]`, a JSON blob shaped like:

```jsonc
{
  "version": "...",
  "appVersion": "...",
  "current-user": 20000003,                    // numeric user id
  "current-user-20000003": {
    "id": 20000003,
    "username": "...",
    "email": "...",
    "initials": "...",
    "token": "<128-char token>",                // untested against apileague — not used below
    "jwt": "<809-char JWT>",                     // ACCOUNT-level — fails with ATH001 on apileague
    "leagues": [ /* ... */ ],
    "currentLeague": {
      "order": 2,
      "id": 4103937,                             // league id (l_id)
      "name": "Legamiallerotaie2",
      "alias": "legamiallerotaie2",
      "type": 2,
      "token": "<JWT — THIS is the one that works>"
    }
  }
}
```

**Use a `leagues[]` entry's `token`** — or `currentLeague.token`, which is the
same string. Corrected 2026-08-26: an earlier revision of this doc said
`currentLeague.token` was "the only one of the three tokens confirmed to
authenticate", and inferred that a multi-lega account had to re-capture after
switching lega in the app. Both halves were wrong.

Measured, logged in, on a two-lega account:

- **Every entry in `current-user-{userId}.leagues[]` carries its own `token`,**
  and each one authenticates: `GET /onboarding/v1/league/status` returned `200`
  using the token of the lega that was *not* current.
- Each entry's token decodes to that entry's own `l_id`.
- `currentLeague` is a **copy** of whichever `leagues[]` entry is active —
  byte-identical token, not a separate credential.
- Every token in the blob shares one `iat`/`exp`. They are minted by a single
  auth event, so one login yields every lega's token and they all expire
  together.

The account-level `jwt` (809 chars) still fails with `ATH001`, and the 128-char
`token` beside it is still untested. That part stands.

Decoded, a league token's payload looks like — header is `RS256` with a `kid`:

```jsonc
{
  "sub": "00d7c0b133...",           // opaque subject id
  "jti": "...",
  "iss": "https://leghe.fantacalcio.it",
  "iat": 1787129066,                // issued 2026-08-19
  "exp": 1818665066,                // expires 2027-08-19 — 365-day lifetime
  "l_id": "4103937",                // league id
  "t_id": "10000003",               // this user's team id in that league
  "user_id": "20000003",
  "st_leagues": "1787129066278",
  "st_auth": "1757973450921",
  "role": "user_league",
  "token_use": "id",
  "nbf": 1787129066,                // added 2026-08-26 — was missing from this example
  "aud": "fantacalcio"
}
```

Key point: **the token is scoped to one league** (`l_id`) — but every league's
token is in the blob at once, so a multi-lega account needs no switching. Assert
the decoded `l_id` matches the `leagues[]` entry it came from; that check is the
only thing standing between a mislabelled token and acting in the wrong lega.

Lifetime is ~365 days (`exp - iat`), so one login should cover a full season.
Treat an `ATH001` response as "token invalid/expired — re-auth" rather than
assuming it always succeeds.

### Other `LEAGUES2024_LOCAL` keys

The blob's top level also holds **`current-user-team-{userId}-{leagueId}`**, one
per lega — undocumented here until 2026-08-26. Each carries `id` (the team id,
matching that lega's `t_id` claim), `name`, `budget`, `credits`, `roster[]`,
`rosterComplete`, plus kit/logo metadata. It is the cheapest confirmation that a
token's `t_id` is the team you think it is.

### How this maps onto `fantabot auth login`

`fantabot auth login` (which replaced the old `auth` command, see `SPEC.md`) runs a headed
Playwright login and calls `storage_state()` into `data/storage_state.json`.
`storage_state()` snapshots **both cookies and `localStorage` per origin**, so
`LEAGUES2024_LOCAL` is in that file — and in the live context object — the
moment the human finishes signing in. The capture is:

    storage_state → origin leghe.fantacalcio.it
                  → localStorage["LEAGUES2024_LOCAL"] → JSON.parse
                  → current-user-{current-user}.leagues[]
                  → for each: decode token, assert l_id == entry.id, store

No page interaction, no lega switching, no selectors.

The token is then encrypted and written to Postgres keyed by `l_id`; nothing
reads it from disk. The read-only endpoints below are callable directly with
`httpx` from Python for any run after that login. Write actions (submit lineup,
place bid) still need the DOM or newly-captured POST endpoints.

## Error codes observed

| HTTP | code | message | Meaning |
|---|---|---|---|
| 401 | `ATH007` | Application key is missing | No `app_key` header sent |
| 401 | `ATH001` | Not authorized to access the services | `app_key` present but bearer token missing/invalid/wrong-scope (e.g. used the account `jwt` instead of `currentLeague.token`) |
| 400 | *(empty body)* | — | Seen on `/onboarding/v1/invitation/participants` called with no extra params — endpoint needs query params not yet identified |

## Endpoints

All confirmed via direct `fetch()` replay with the headers above, from
league `legamiallerotaie2` / competition `177318` / team `10000003`.

### `GET /onboarding/v1/league/status`

```json
{
  "sto": false,
  "activ": true,
  "sId": 21,
  "mday": 1,
  "mstr": "2026-08-22T16:30:00"
}
```
`sto` = stopped, `activ` = active, `sId` = season id, `mday` = current
matchday number, `mstr` = that matchday's first kickoff, **in UTC** — and it **is** the lineup
deadline. Confirmed 2026-09-18 against giornata 5: `mstr` 18:45 = 20:45 Rome =
Monza-Sassuolo, the published first match; a lineup saved at 19:13 Rome was
accepted; and `sto` (below) flipped to `True` only once that match kicked off.
Giornate 1 and 3 fit the same offset (`16:30`/18:30, `18:45`/20:45).
`sto` is the lock flag: `False` while the lineup is open, `True` once the
giornata has started — read it rather than comparing clocks when you only need
to know whether it is too late.

### `GET /onboarding/v1/league/competitions`

```json
[
  {
    "del": false,
    "sDay": 3,
    "eDay": 38,
    "id": 177318,
    "lid": 4103937,
    "type": 1,
    "name": "Fanta26-27",
    "win": "",
    "tmids": [10000001, 10000002, 10000003, 10000004, 10000005]
  }
]
```
`id` matches the competition id in the site's URL
(`/view/competition/177318/...`). `sDay`/`eDay` = start/end matchday of this
competition. `tmids` = team ids competing in it.

**The array grows, and the id in this example is already stale.** As of
2026-08-26 lega `4103937` returns *two* competitions — `311681` "Fanta 26-27"
with 8 teams, and this `177318` "Fanta26-27" with 5 — and lega `3584692`
returns an empty array. Resolve the competition at runtime; never pin an id.
Resolve the competition at runtime rather than pinning an id.

### `GET /onboarding/v1/league/teams/my`

Current user's team. Same shape as one item of `/league/teams` below.

### `GET /onboarding/v1/league/teams`

```json
{
  "nextPage": false,
  "prevPage": false,
  "page": 1,
  "item": 6,
  "pages": 1,
  "data": [
    {
      "d": "A",
      "c": false,
      "id": 10000001,
      "idu": 20000001,
      "cri": 500,
      "crs": 0,
      "bm": 0,
      "cr": 500,
      "n": "Team A",
      "nu": "Owner A",
      "l": "10000001_00378527.png",
      "cal": "",
      "cs": "",
      "m": "10000001_...",
      "ms": "s_10000001_...",
      "st": "1;1;1",
      "pl": null,
      "lm": { "sponsor_id": 0, "ver": 0, "forma": "00", "motivo": "00", "simbolo": "00", "sponsor": "00", "colori": ["#ffffff"], "badge": ["00"] },
      "mm": { "sponsor_id": 55, "ver": 1, "forma": "23", "motivo": "00", "simbolo": "00", "sponsor": "03", "colori": ["#1B1464", "#BEAB5B", "#BEAB5B"], "badge": ["01", "04"] }
    }
    // ...one entry per team in the league
  ]
}
```
`id` = team id, `idu` = owning user id, `n`/`nu` = team name / owner
username, `cri`/`crs`/`cr` = credits initial/spent/remaining, `l` = logo
filename, `lm`/`mm` = home/away kit config (colors/badges/sponsor), `d` =
divisione. Field names are all abbreviated (`c`, `bm`, `st`, `pl` meanings
unconfirmed — don't guess beyond what's testable).

**`cal` and `cs` are the rosa** — established 2026-09-02, and the single most
valuable thing in this document. `cal` is a `;`-joined list of 30 **player ids**
and `cs` the 30 **costs paid**, positionally paired:

```
"cal": "7071;6966;6827;...",   // 30 fantacalcio player ids
"cs":  "72;47;3;..."           // 30 credit prices, same order
```

Every team's, not just ours, with no admin rights. Verified by summing `cs` per
team against that team's own `crs`: six of eight matched to the credit, and the
two that did not disagreed by exactly -12 and +12 — a completed trade between
them, which is a confirmation and not a contradiction. `sum(cs)` is therefore
*acquisition* cost and `crs` is the running balance; when they differ, credits
have moved since the asta.

Two parallel strings with nothing but position tying a price to a player, so
`domain/lega/parse.parse_team_roster` **refuses** a pair whose lengths differ
rather than zipping the shorter. A mismatch is unrecoverable and the result
would still look plausible: 29 players and credits that nearly add up.

This is what `view/rosters/:teamId` renders. There is **no per-team roster
endpoint** — searching the bundle for one found only `/{market}/action/roster/…`,
which needs a live market resource. The Rosters component reads `team.roster`
off the team object it already has. An earlier draft of this doc listed rival
rosters as an undiscovered gap; the gap was in the reading, not the API.

Query parameters (from the bundle's `getPage`): `?page=`, `&pageSize=`,
`&division=` (upper-case), `&competitionId=`. The response's `pages` is
authoritative for the loop.

### `GET /onboarding/v1/league/settings/rosters`

```json
{
  "mplys": false,
  "hdslt": false,
  "msltc": 30,
  "xsltc": 30,
  "fsltc": 0,
  "tcap": 0,
  "budg": 500,
  "count": 2,
  "cmod": 0,
  "version": "v3",
  "sroles": 2,
  "minrl": [2, 28],
  "maxrl": [2, 28],
  "under": null,
  "cteam": null,
  "crole": null
}
```
League roster rules: `budg` = starting credit budget (500), `minrl`/`maxrl`
= min/max roster size per role-group.

**`sroles` says how many groups those arrays have — and it differs per lega.**
Both of this account's leghe, fetched side by side 2026-08-26:

| | `3584692` *Legamiallerotaie* | `4103937` *Legamiallerotaie2* |
|---|---|---|
| lega `type` (from `leagues[]`) | `1` | `2` |
| `version` | `v2` | `v3` |
| `sroles` | `1` | `2` |
| `minrl` = `maxrl` | `[3, 8, 8, 6]` | `[2, 28]` |
| roster size (`msltc`/`xsltc`) | 25 | 30 |
| `budg` | 500 | 500 |
| competitions | 0 | 2 |

`[3, 8, 8, 6]` is P/D/C/A — the textbook 25-man **Classic** roster, with real
per-role limits. `[2, 28]` cannot express Classic role limits at all: it is
2 goalkeepers and 28 everyone-else. Given the account plays one Classic and one
Mantra league (`CLAUDE.md`), **`3584692` is the Classic lega and `4103937` is the
Mantra one** — by elimination from a measured structure, not from the field
names. An earlier revision of this doc read `[2, 28]` as "matching classic
mode"; that was a guess and it was backwards.

Confirm before a Mantra lineup depends on it: the clean test is whether
`sroles`/`minrl` change shape on a league whose mode is independently known.

For `strategy.allocate_auction_budget`, the Classic lega's `[3, 8, 8, 6]` is
exactly the role-level min/max the hardcoded 5/15/35/45 split was standing in
for — it is available now, not "once role-level min/max is found".

### `GET /onboarding/v1/league/players`

The league-wide player pool. No parameters. Confirmed 2026-08-26 against lega
`4103937`; **547 entries** that day.

```jsonc
{
  "players": [ /* one object per player */ ],
  "timestamp": 1787760225863
}
```

**It is an object with a `players` key, not a bare array.** Code written against
a top-level list gets `KeyError: 0` — which is exactly what happened the first
time this was called from Python.

Each entry carries 41 fields. The ones whose meaning is established:

| Field | Example | Meaning |
|---|---|---|
| `id` | `5585` | Player id — the same id the scraped CSVs and `players` use |
| `name` | `"Malen"` | Player name |
| `tname` / `stnme` | `"Roma"` / `"ROM"` | Serie A club, long and 3-letter. 20 distinct. |
| `tid` | `15` | Serie A club id — **not** a fanta-team id |
| `quotd` | `1` | Quotazione. Observed `1` for 536 of 547 and `2` for the rest on 2026-08-26 — before the asta. Whether that is a pre-asta reset or something else is **not** established. |
| `fvmfc` / `fvmma` | `207` / `207` | FVM classic / Mantra. They differ for 160 of 547, so do not treat one as a fallback for the other. |
| `marle` | `[16]` | Role codes, **as integers**. 1–3 per player (278 / 248 / 21 of 547). |
| `age` | `27` | Age |
| `naty` | `"Olanda;Suriname"` | Nationality, `;`-joined for dual nationals |

**`marle`'s integers are resolved** (2026-09-02), and the resolution was measured,
not guessed. Join this endpoint against the `quotazioni` Mantra listone on
`player_id` and pair the two role lists positionally: 571 of 588 players are in
both, and **every integer maps to exactly one letter code with no runner-up.**

| `marle` | 6 | 7 | 8 | 9 | 10 | 11 | 12 | 13 | 14 | 15 | 16 | 19 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| code | Por | Dd | Ds | Dc | E | M | C | T | W | A | Pc | B |

`17` and `18` were never observed and are absent rather than invented; an
unmapped integer is stored as its own digits, because dropping one would make a
30-man rosa silently 29. The map lives in `domain/lega/parse.MARLE_TO_CODE`.

The remaining fields — `aagr acsfc acsma agit agrd bmcsh faagr fagit fagrd
fcrle icsfc icsma img l5fral l5frfc l5frit l5ral l5rfc l5rit l5sub leag lid
mspv mspva mspvi shtnu trnsf trsfd vers wid` — are left unexplained rather than
guessed, per this document's standing rule. Two observations worth recording:
`lid` was `21`, equal to `/league/status`'s `sId`, so it looks like a *season*
id rather than a lega id despite the name; and `trnsf`/`trsfd` plausibly concern
transfers. Neither is confirmed.

This is the endpoint behind `league_player_pool` in the schema, which keeps four
of these fields: `quotd`, `fvmfc`, `fvmma`, `marle`.

### `GET /onboarding/v1/league/competition/calendar/{competitionId}`

**The calendar, the results and everything needed to compute the standings.** Found
in the bundle, not on a page: the Fixtures view's `getFixtureMatches` calls
`getEndPoint(`/${i}`, "league/competition/calendar", "onboarding")`. An array with
one entry per round:

```jsonc
[
  {
    "matchDay": 1,                 // the COMPETITION's numbering
    "championshipMatchDay": 3,     // Serie A's. They differ from round one.
    "calculated": false,
    "matches": [
      { "tIdH": 10000005, "tIdA": 10000003,
        "ptH": 0.0, "ptA": 0.0,          // fantapunti
        "standingPtH": 0, "standingPtA": 0,   // league points (3/1/0)
        "result": "-", "resultSR": "" }       // "-" and "" both mean not played
    ]
  }
]
```

36 rounds x 4 matches for this 8-team lega, all `calculated: false` on 2026-09-02 —
the lega starts at Serie A matchday 3, so there is no history to backfill, only a
weekly sync to keep.

### `GET /onboarding/v1/league/settings/calculate`

The scoring rules: `bnMls` (bonus/malus per event), `step` (the goal ladder),
`subst` (automatic-substitution config, `sstype` naming the mode), `stbdf`. Not
parsed into columns — the field names are dense and mostly unconfirmed, and
guessing at them is what this document refuses to do.

### `GET /onboarding/v1/league/custom-roles`

Players this lega re-tagged. 27 entries on 2026-09-02:

```json
{ "originalRole": 3, "role": 4, "id": 632, "name": "Zaccagni", "team": "Lazio", "teamId": 11 }
```

**The integers are the Classic P/D/C/A scale, not `marle`.** Measured the same way:
all 27 joined against the Classic listone give `2 -> D` (3 rows), `3 -> C` (20),
`4 -> A` (4), no disagreement. `1` was not observed and is included only because a
four-value scale with three values pinned leaves it nothing else to be.

This matters because the two scales overlap numerically and mean different things —
reading an override through `MARLE_TO_CODE` turns Zaccagni's C -> A into a Mantra
`C -> W`, which is a real code and the wrong one. For a Mantra lega the endpoint is
informational: every override here is a move the Mantra listone already expresses
(`W`/`A` for Zaccagni), so it is stored and deliberately **not** wired into L1.

### `GET /onboarding/v1/league/profile`

**Do not wrap this one.** It returns `lega.parola`, the league's join password, in
cleartext alongside the name, alias, founding year and admin list. Everything useful
in it is available from `/league/teams` without handling a shared secret, and a
wrapper is an invitation to log or store one. `adapters/http/apileague` says the same
thing in a comment so the next person does not re-add it.

### `GET /gaming/v1/teamLineup/{compId}/{mday}/{cmday}/{teamHome}/{teamAway}`

One match's detail: `{cal, mday, cmday, idcomp, sign, res, resr, home, away}`. Wrapped
as `apileague.match_detail` (T12). Each side carries `tid`, `points` (league points for the
match), `tot`, `mdl`/`nmdl`, `starts[11]`, `bench[12]`, `swtc` (`"0;0;0;0;0"`), `tbon`
(eleven `;` zeros, presumably team modifiers, off in this lega), `ldate`/`cdate`, `useId`
(the manager's platform user id), `lucnt`, `act`, `allComp`, `visb`, `capt`, `rg`, `tkply`,
and `lply`.

**`lply` stays null even on a calculated round** (all 12 matches of rounds 1-3, captured
2026-09-22). The per-player scores are the `starts`/`bench` lines instead:

```json
{"pid": 2521, "scr": 6, "cscr": 4, "b": "0;0;0;2;0;0;0;0;0;0;0;0;0;0;0;0", "m": 0, "ptype": "-"}
```

* `scr` — the vote, and it **is `voto_fc`** (`sourcev: 1`; 153 of 153 in round 1). `55` or
  `56` means no vote, and `cscr` is then `100`; the two codes' difference is unknown.
* `cscr` — the platform's fantavoto: exactly `scr + Σ b[i]·weight(i) − m` on all 454
  voted lines, with the lega's `bnMls` weights.
* `b` — sixteen event counts. Solved from those 454 lines and cross-checked against
  `match_grain` for round 1:

  | pos | event | `bnMls` | | pos | event | `bnMls` |
  |---|---|---|---|---|---|---|
  | 0 | yellow | `bmyc` −0.5 | | 8 | decisive goal | `bmdg` +1 |
  | 1 | red | `bmrc` −1 | | 10 | keeper's clean sheet | `bmcsh` +1 |
  | 2 | goal (not penalties) | `bmgs` +3 | | 12, 13, 14 | the three assist kinds | `bmas*` +1 |
  | 3 | goal conceded | `bmgc` −1 | | 15 | man of the match | `motm` +1 |
  | 4 | penalty saved | `bmpsa` +3 | | 7, 9, 11 | never fired; one is the own goal | ? |
  | 5 | penalty missed | `bmpns` −3 | | | | |
  | 6 | penalty scored | `bmpsc` +3 | | | | |

  12+13+14 equals `match_grain.assist`. The decisive goal has **no `match_grain` column**,
  so a score recomputed from history is short of it (5 of 153 players in round 1).
* `m` — the positional malus flag: weighs exactly −1. Three starters had it in rounds
  1-3, none of them ours. This is what `scoring.malus_starters` reads.
* `ptype` — `-`, `U` (substituted out; with no vote), `E` (came on).

### `GET /market/v1/time`

```json
{ "secs": "20260819085317", "mins": "202608190853" }
```
Server clock, `YYYYMMDDHHMMSS` / `YYYYMMDDHHMM`. Different service prefix
(`/market/v1/...` vs `/onboarding/v1/...`) — the auction endpoints are
presumably also under `/market/v1/...`, e.g. something like
`/market/v1/session`, `/market/v1/listing`, `/market/v1/bid` — unconfirmed,
would need to be captured live during an actual asta.

### `GET /onboarding/v2/profile/{userId}`

Not fetched during this session (low value — it's the logged-in user's own
account profile, `{userId}` = the `user_id` JWT claim). Same auth as above.

### `GET /onboarding/v1/adv`

Not fetched — ad/banner config for the client, not useful for fantabot.

### `GET /onboarding/v1/invitation/participants`

Returns `400` with empty body when called with no query params. Needs
parameters (likely `leagueId` and/or `teamId`) not yet identified — capture
this one live from the "Partecipanti" page if it's ever needed.

## Lineup — `gaming/v1` (captured live 2026-09-02)

A separate microservice: `https://apileague.fantacalcio.it/gaming/v1/...`, same two
auth headers (`app_key` + league `Bearer`). Captured by driving the real Formazione
page in a headed browser (Playwright/Edge), hooking `fetch`/`XHR`, and clicking
"Salva formazione". Confirmed against lega `4103937`, competition `311681`, team
`10000003`, division `A`, matchday 3.

### `GET /gaming/v1/teamLineup/visualizza/{division}/{competitionId}`

The current lineup for a competition. `{division}` is `A` here (the same divisione
tag the market endpoints use). Returns:

```jsonc
{
  "teamLineupDto": {
    "visb": true,          // visible (not a hidden lineup)
    "allComp": false,      // apply to every competition at once
    "idcomp": 311681,      // competition id
    "tid": 10000003,       // our team id
    "mday": 1,             // LEAGUE matchday (competition-relative)
    "cmday": 3,            // Serie A calendar matchday — the two differ
    "act": 0,
    "useId": 20000003,     // user id
    "swtcA": 0, "swtcB": 0, "swtc": 0, "pos": 0, "lucnt": 1,
    "mdl": "3412",         // module actually fielded (one of settings/lineup `mods`)
    "swtcMdl": null,       // alternate module for DYNAMIC cambio-modulo subs
    "ldate": "20260902132906495",  // last-saved timestamp YYYYMMDDHHMMSSmmm
    "starts": [ /* 11 player ids, ordered by pitch slot: GK, def, mid, T, att */ ],
    "bench":  [ /* 12 player ids, in sub-priority order; first is the GK slot */ ],
    "capt": null           // captain id(s), null/empty if none
  },
  "lineUpInfo": [ /* per-player detail rows */ ]
}
```

Player ids are the fantacalcio ids — the same `id` as `/league/players` and the
scraped tables.

### `POST /gaming/v1/teamLineup/{division}`  — submit / "Salva formazione"

The write. `200 OK` on success (verified: the `visualizza` GET immediately reflected
the new `starts`/`bench` and a fresh `ldate`). Body captured verbatim:

```jsonc
{
  "starts": [6482,2788,7564,7274,7181,1850,5504,5678,5620,6875,4179],  // 11, slot order
  "bench":  [4360,5750,4137,4998,2194,5680,4459,6898,7198,4947,5319,7126],  // 12, sub order
  "capt": [],            // captain id(s); empty = none (league had lcap=3 but optional)
  "mdl": "3412",         // chosen module — must be in settings/lineup `mods`
  "idcomp": 311681,
  "mday": 1,             // league matchday
  "cmday": 3,            // Serie A calendar matchday
  "tid": 10000003,
  "allComp": false,
  "visb": true,
  "swtcA": 0, "swtcB": 0, "swtc": 0,  // switch/substitution config ints
  "swtcMdl": "3412"      // alternate module for cambio-modulo (DYNAMIC subs)
}
```

Slot count and role eligibility come from `settings/lineup` (`mods`, bench `tbench`,
first bench slot is GK) and `settings/rosters`. The bench **order is load-bearing** —
it drives the automatic-substitution engine (sub type in `settings/calculate.subst`).
There is no dry-run: a valid XI + full bench enables the button and the click submits
for real.

## Gaps — not yet discovered

- ~~**Lineup submission**~~ **Resolved 2026-09-02** — `POST /gaming/v1/teamLineup/{div}`,
  see above.
- **Auction bid placement** (asta iniziale / riparazione) — same story.
  `/market/v1/time` is the only `/market/v1/...` endpoint seen; the rest of
  that service (session state, current listing, place-bid) needs a live
  capture during an actual auction.
- ~~**Matchday roster / probable lineup**~~ **Resolved 2026-09-02.** There is no
  "Rose" endpoint: `/league/teams` carries every team's rosa in `cal`/`cs`. See above.
- ~~**`marle`'s numeric role codes**~~ **Resolved 2026-09-02** by the join described
  under `/league/players`. Twelve integers, twelve codes, zero ambiguity.
- ~~**Per-player match scores (`lply`)**~~ **Resolved 2026-09-22 (T12).** `lply` stays null;
  the scores are the `starts`/`bench` lines of the match detail — see that section.
- **`/gaming/v1/calculationday/{id}` and `/gaming/v1/lineup/notcalculated/{id}`** —
  both in the bundle, both `400` with the competition id alone. They are admin
  ("Calcola giornata") reads and probably need another parameter.
- **`/onboarding/v1/invitation/participants`** — needs its query params.
- Field name meanings that are genuinely ambiguous (`d`, `c`, `bm`, `st`,
  `pl` on team objects; `cmod` on roster settings) are left
  unexplained above rather than guessed — confirm by diffing responses
  before/after known actions (e.g. watch `st` change when a lineup is
  submitted) rather than assuming from field names.

## How this was found (for re-discovery after a frontend redeploy)

1. The Chrome extension's network-request logger did **not** capture these
   calls (cross-origin fetch/XHR gap) — use
   `performance.getEntriesByType('resource')` from the page's own JS
   console/`javascript_tool` instead to list every host/path actually
   fetched:
   ```js
   [...new Set(performance.getEntriesByType('resource')
     .map(r => { const u = new URL(r.name); return u.origin + u.pathname; })
     .filter(u => u.includes('apileague.fantacalcio.it')))]
   ```
2. `app_key` + header names came from grepping the shipped Angular bundle
   (`https://leghe.fantacalcio.it/resources/main-*.js`, which re-exports a
   content-hashed `main-*.js` — fetch the re-export target too) for
   `apileague.fantacalcio.it` and `appKey`. The interceptor that attaches
   auth looked like:
   ```js
   r.clone({ setHeaders: { app_key: v$7.appKey, Authorization: `Bearer ${s}` } })
   ```
3. The prod config block (search for `production:!0` near `apiBaseUrl`) has
   `appKey`, `embedApiKey`, and `apiBaseUrl` together — re-run this grep
   after any frontend deploy if `app_key` ever stops working (`ATH007`
   despite sending it would mean the key rotated). Confirmed unchanged
   2026-09-02: `ICiELOObd5DF5uJEATi77CRvHiiRuMU0`, with
   `https://apileague.staging.fantacalcio.it` as the staging host this doc
   previously had only truncated.
4. **The whole endpoint surface, without visiting a page.** Better than (1) —
   it finds endpoints no page in the session happened to load. The SPA is at
   `<base href="/resources/">`; a 404 under `leghe.fantacalcio.it/<alias>`
   still ships the shell, so any URL gives up the bundle names. Fetch
   `resources/main-*.js`, collect every `chunk-XXXXXXXX` token in it, fetch
   `resources/<token>.js` for each, and repeat until the set stops growing —
   227 chunks on 2026-09-02. Then grep the lot for the triple every service
   class declares:

   ```
   this.microserviceName="onboarding"; this.serviceName="league/teams";
   ...this.getEndPoint(`/action/roster/${t}`)
   ```

   `getEndPoint` also takes overrides — `getEndPoint(path, serviceName,
   microserviceName)` — which is how the calendar hides inside the lineup
   service. A regex for the two-and-three-argument forms is what turned it up.
   Attribute each path by walking back to the nearest preceding
   `microserviceName`/`serviceName` assignment.
