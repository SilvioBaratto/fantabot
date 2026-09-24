# 05 — Harvesting real auction prices from Osserva Aste

Built and run on **2026-08-26**. This is the answer to the gap `00 §14` calls
*"calibrare tutto"* and `00 §18` calls the bots-too-timid risk: **prices actually
paid in real Mantra auctions**, which nothing in `data/` held.

The short version: FantaLab's spectator mode is backed by a Firebase node that
serves every bid without authentication. Polling it for the length of an evening
reconstructs the full bid ladder — not just clearing prices — for every public
Mantra auction running.

Confidence markers as in [`README`](README.md): **[observed]**, **[code]**, **[inferred]**.

---

## 1. What is public and what is not

This is the fact the whole design turns on, and `03 §3` did not have it.

| Surface | Auth | Notes |
|---|---|---|
| `GET fantalab-<db>.europe-west1.firebasedatabase.app/auction/<uuid>.json` | **none** | The live bid state. **[observed]** |
| `GET api.fantalab.it/v2/listone` | **none** | 519 players, and the id bridge. **[observed]** |
| `GET api.fantalab.it/players` | **none** | id, name, role, mantra_roles. **[observed]** |
| `GET api.fantalab.it/fantaleagues/live` — the list | **required** | `401` unauthenticated. `?asta_type=` filters; omit it for all formats. **[observed]** |
| `/aste-live` — the page that renders it | **required** | A fresh browser is bounced to `/signup`. **[observed]** |
| `.../auction.json?shallow=true` — enumeration | **denied** | `{"error":"Permission denied"}` **[observed]** |
| `POST api.fantalab.it/auction-reels/list` | premium | `403` for a free account. **[observed]** |
| *Analisi Asta*, *Prezzo Medio Aste* in the room | premium | Blurred behind `PASSA A PREMIUM`. **[observed]** |

Two consequences:

- **You can read any auction you can name, but you cannot ask which auctions exist.**
  Firebase rules grant read on `auction/$id` and deny everything above it. The uuids
  have to come from the signed-in list page.
- **The premium gating is irrelevant to us.** *Prezzo Medio Aste* and *Analisi Asta*
  are aggregations computed over the same stream we read for free. We compute our own,
  which keeps `00 §16`'s "no paid subscriptions" intact.

`03 §3` said the realtime layer lives on one database,
`fantalab-79eaa-default-rtdb`. It does not: auctions are **sharded across at least
20 namespaces**, `fantalab-0` … `fantalab-19`, and an auction's card carries the shard
id in a field named `db`. Seventeen distinct shards were seen in one evening.

## 2. The auction node

One node per auction, holding the *current* state — it is an event bus, not a store.
Every write replaces it, so polling is the only way to see history.

```
GET https://fantalab-18.europe-west1.firebasedatabase.app/auction/74098d55-…json
```

```json
{
  "fantaleague_id":   "74098d55-…",   // the auction
  "fantateam_id":     "c5fdd115-…",   // team currently holding the bid
  "user_id":          "75341945-…",   // that team's user
  "player_id":        "53e42c7e-…",   // player on the block
  "price":            15,
  "is_first":         false,
  "last_bid_time":    1787767448632,  // ms; gives ordering for free
  "last_update":      1787767448375,
  "last_update_asta": 1787760916385,
  "next_turn":        "ebe1e913-…",   // team due to nominate
  "timeToPass":       8,              // timer seconds
  "update_type":      "raise"
}
```

### Event lifecycle **[observed]**

```
first_call     price=0   player set, no team      ← player hits the block
raise          price↑    player + bidding team    ← one per bid, repeated
close_auction  final price + winning team         ← the assignment
confirm        cleared, next_turn advances
```

Other values seen: `reset` (call annulled, `00 §8`), `reopen`, `update_turn`,
`asta_db_update`, `teams_to_skip`.

The decisive detail is that **`raise` carries the bidding team**. We do not merely
get "Malen went for 192"; we get every step of the ladder with who pushed it. That
is what makes `04`'s budget-depletion bot (Benoit & Krishna) fittable rather than
invented.

Counts from the first 82 minutes, 41 auctions:

```
raise 12 629 · first_call 2 234 · close_auction 1 205 · confirm 1 141
reset 471 · update_turn 37 · asta_db_update 30 · teams_to_skip 22 · reopen 14
```

Roughly **ten raises per assignment.**

## 3. Enumeration

> **Corrected 2026-08-27 (spike S2).** This section previously claimed the list
> *"never crosses the wire as JSON"* and arrives only over the Firebase WebSocket.
> That was wrong, and wrong for a bad reason: the observation was made by watching
> a filter *toggle*, which fires nothing, rather than the initial page load.

The list is a plain REST endpoint. **[observed]**

```
GET  api.fantalab.it/fantaleagues/live                      → 200 (authenticated)
GET  api.fantalab.it/fantaleagues/live?asta_type=mantra     → 200 (authenticated)
GET  api.fantalab.it/live-interesting-auctions/top?asta_type=mantra
```

**It requires authentication** — `401 {"error":"You are not authorized to make this
request"}` without a session. `asta_type` is an optional filter, so omitting it is
how you ask for every format at once.

This retires the React-fiber extraction below as the *primary* path. It stays
documented because it needs no credential and is the fallback if the endpoint moves.

### The fallback: reading the list out of React

Every card's props hold the auction's whole configuration:

```
admin_id · asta_mode · asta_type · auction_completed · auction_running · call_at_quotaz
counter_time · counter_time_first · credits · credits_used · db · fantacalcio_lega_id
fantaleague_id · fantaleague_name · is_live · is_private · max_player · min_player
mod · num_credits · num_teams · player_per_position · playing_mode · raise_mode
season · turn · unlimited_player_per_role · …
```

That is **most of `00 §13`'s configuration table, per auction, for free** — including
`min_player`/`max_player`, which decide the MAX cap and whether the `00 §3` unlock
exists at all.

The extraction walks every DOM node's `__reactFiber$*` chain and keeps any props
object with a `fantaleague_id` and a `db`. It is deliberately blind to class names,
which churn with every bundle build. The JavaScript lives in
`scripts/scan_aste_live.py` as `EXTRACT`, next to the merge code that consumed it
(both since replaced by `aste-scan`).

**The list paginates at 10.** The rows-per-page select offers `Tutte`, which must be
chosen before extracting or you get the first page only — a silent under-count, since
nothing errors.

## 3b. Where the session lives — and why it cannot be saved plainly

Measured 2026-08-27 (spike S3), by listing storage **key names only**. No value was
read, returned or stored.

```
localStorage   refresh_token · id_token · access_token · auth_tokens
               user_id · user_email · deviceId · persist:root
               firebase:host:fantalab-<n>   (19 shards + the default)

cookies        _ga · _fbp · _ga_PKWNHWF6NS · asta        ← analytics only, no auth
IndexedDB      fantalab · firebase-heartbeat-database · firebaseLocalStorageDb
sessionStorage (empty)
```

**The session is in `localStorage`, not in a cookie and not only in IndexedDB.**
That matters twice over:

- **Good news for automation.** Playwright's `storage_state` captures cookies *and*
  localStorage, but **not** IndexedDB. Had FantaLab relied on the Firebase SDK's own
  `firebaseLocalStorageDb` alone, a saved session would have been useless. It does not.
- **Bad news for storing it.** A `storage_state.json` written to disk would contain
  `refresh_token`, `id_token` and `access_token` **in plaintext** — precisely what
  `SPEC.md`'s token-store phase exists to prevent, and what `CLAUDE.md` forbids: *a
  bearer token is never printed, logged, `repr`'d or committed, in any form.*

So the login command must not call `context.storage_state(path=…)`. It reads the
credential in-process, encrypts it with the existing `tokens/crypto.py`, and writes it
to Postgres beside `league_tokens`. No plaintext session file is ever produced.

The ID token itself expires in ~1 h and is **not** what persists: the app performs a
full `POST /sign-in` → `POST /auth/firebase-token` → `signInWithCustomToken` on every
page load, deriving a fresh one. `refresh_token` is the durable credential. **[observed]**

### Shards, counted

Nineteen numbered namespaces — `fantalab-0` … `fantalab-19`, with 3 and 9 absent from
this account's set — plus `fantalab-79eaa-default-rtdb`, which carries app config
(`/banner`, `/infoFormazione`) rather than auctions. **[observed]**

## 3c. Authenticating the list

`GET /fantaleagues/live` takes `Authorization: Bearer <id_token>`. Measured
2026-08-27: 401 without it, 200 with, 189 auctions. **[observed]**

The credential is captured by `fantabot fantalab-login` and stored encrypted.
`asta_type` is omitted from the query so both formats come back in one call.

### The tokens are Keycloak, and they last years

> **Corrected 2026-08-27, an hour after the section above was written.** It
> claimed the `id_token` expires in about an hour and treated renewal as an open
> problem. That was wrong, and wrong for a specific reason: the login trace shows
> `signInWithCustomToken`, and the conclusion drawn was that the session is
> Firebase's. It is not — that call authenticates the *realtime database*
> connection. The API session is separate.

Decoding the stored tokens' `exp` claims — the claim only, never the token:

```
issuer          https://keycloak.auth.fantalab.it/realms/fantalab
client (azp)    fantalab-website

id_token        exp 2032-02-17    2000 days
access_token    exp 2032-02-17    2000 days
refresh_token   exp 2040-05-05    5000 days
```

**Renewal is not a problem this project has.** A scan on cron will keep
authenticating until 2032 on the token captured today.

For completeness, since it was chased before the expiry was checked:

| Attempt | Result |
|---|---|
| `securetoken.googleapis.com/v1/token` | `INVALID_REFRESH_TOKEN` — not a Firebase token |
| Keycloak's own `/protocol/openid-connect/token`, `grant_type=refresh_token` | `unauthorized_client` — `fantalab-website` is confidential and needs a client secret |
| `POST <base>/auth/token/refresh`, the call the bundle contains | `404` on `api`, `manager`, `falsesoftware`, `api-cdn` and the Cloud Run host |

The last row is the coherent ending: with tokens valid for 2000 days nobody needs
that route, so nobody deployed it. It is dead client code.

**Do not go looking for the client secret.** The refresh a confidential client
performs is the backend's to do, and a 5,000-day refresh token makes the whole
question moot.

Also from the bundle, and worth never calling by accident:
`POST <base>/auth/logout` with the refresh token ends the session.

`aste-scan` still raises `AuthExpired` on a 401. Not because expiry is expected —
because a revoked or rotated session must not read as a quiet night.

## 4. The id bridge

`GET api.fantalab.it/v2/listone` returns 519 players carrying **`fantacalcio_id`** —
the same integer key as `quotazioni.player_id` and our `players.id`. So the join is
exact; no fuzzy name matching. **[observed]** 407/407 auctioned players resolved.

The response also carries, per player: `quotazione`, `quotazione_mantra`,
`fvm_classic`, `fvm_mantra`, `rating_classic`, `rating_mantra`, `mantra_roles`,
`injured`, `starts_eleven`, `min_playing_time`, `presenze`, `mv`, `fmv`,
`gol_fatti`, `assist`, `rigori_*`, `next_match`, `owner`.

`starts_eleven` + `min_playing_time` + `injured` cover much of the *titolarità* input
`04` wants, with no LLM and no scraping.

> Note the season label lags: the endpoint reports `s_2025_2026` while serving the
> 2026/27 listone, and auction records say `s_24_25`. Do not key on it.

## 5. Procedure

> **Superseded 2026-08-27.** The three `scripts/*_aste_live.py` below were the
> polling collector. They still work and stay in git as a fallback, but they read
> *merged snapshots* — two raises inside one interval collapse into one — and the
> shadow run measured the cost: on the same 23 rooms, 105 shared sales and **224
> rungs the poller could not see**. Use the commands.

```bash
fantabot fantalab-login                  # once; headed, manual, session encrypted
fantabot aste-scan --seed seed.json      # which auctions are live, both formats
fantabot aste-collect --seed seed.json --out landing.jsonl
fantabot aste-load  landing.jsonl --seed seed.json --follow
```

`aste-collect` subscribes over SSE and appends to disk; `aste-load` carries the
landing zone into Postgres and is the only thing that touches the database.
Stopping Postgres mid-run costs catch-up time and never a record.

Leave the collector running and keep `aste-scan` on a timer beside it: with a
`--seed`, `aste-collect` re-reads that file every 60 seconds and starts
following any auction that has appeared since, so it runs until you interrupt
it. `aste-load` re-reads the same seed every pass, so it recognises what the
collector adopts. Turnover was 20–30% of the live population per 15–20 minutes
on 2026-08-26, which is what reading the seed once used to cost.
`--reload-seed 0` restores the read-once behaviour; with `--one` there is
nothing to re-read.

**The load can wait until the evening is over.** The landing zone is the record;
Postgres is derived from it. `aste-load` checkpoints its byte offset and every
write is an upsert, so stopping it and running it later costs nothing but
catch-up time — and running it after the aste finish rather than beside them
frees its memory and takes the database off a live evening entirely:

```bash
# during: collector only
fantabot aste-collect --seed data/aste_live/seed.json --out data/aste_live/live.jsonl

# after: one pass over everything, no --follow
fantabot aste-load data/aste_live/live.jsonl --seed data/aste_live/seed.json
```

Budget for the file, not for RAM: it grows about 3–4 MB a minute across a full
live population, so a six-hour evening is roughly 1.5 GB on disk. The loader
itself streams, and peaks at about 90 MB regardless of how long the evening ran.

**Watch the heartbeat, and watch the pool.** The collector prints
`live / expected` on every reload cycle. They must be equal:

```
649/649 live · ended 0 · unreachable 0 · crashed 0 · 38395 states written
```

A gap means `--pool` is below the population, and on a live evening it is a
permanent gap — a watcher does not finish, so a queued auction never gets a
permit and never connects. On 2026-08-27 a default of 250 against 649 live
auctions collected 250 of them and said nothing; the command now refuses to be
quiet about it, but the number to raise is `--pool`. The loader is healthy when
it prints no dropped-record line at all; `unknown auction N` means its seed is
behind the collector's.

To judge a change to the reducer, diff two captures through
`fantabot.aste.compare`. The criterion is **strict superset**, and equality is a
failure — streaming reproducing exactly what polling produced is also what a
broken reducer looks like.

The CLI that drove that comparison, `scripts/compare_collectors.py`, was deleted
on 2026-08-30 along with the poller it measured; `aste/compare.py` and its tests
survive, and become the oracle for the incremental reducer.

### The retired path, for reference

`scan_aste_live.py` and `collect_aste_live.py` are gone — superseded by
`aste-scan` and `aste-collect`, which the 2026-08-27 shadow run measured seeing
224 rungs the poller could not. `resolve_aste_live.py` is kept, for reasons that
are not about polling: it is the independent oracle behind
`tests/test_aste_reconstruct.py`, and it holds the `GET /v2/listone`
UUID → `fantacalcio_id` bridge the asta engine still lacks.

```bash
python scripts/resolve_aste_live.py --events events.jsonl --seed seed.json
```

Design choices that carried over into the new path, and why:

- **The collector interprets nothing.** It appends every changed state and stops
  there; assignments are reconstructed offline. A parser bug must never cost data
  that cannot be collected twice.
- **Append-and-close per record**, not a buffered handle. A `kill -9` loses at
  most the line in flight — and the collector was killed eleven times in eight
  hours on 2026-08-26.
- **A watcher that raises is respawned.** The catch is deliberately broad:
  `r.json()` on a gateway error page raises `JSONDecodeError`, which is not an
  `httpx.HTTPError`, and an unhandled raise silently killed one auction's watch
  for a night while everything else looked healthy.

## 6. What one evening yielded

First 82 minutes, still running at time of writing:

```
auctions   41 Mantra          events  17 783 (10.2 MB)
assignments   871             shards used  17 of 20
```

Formats, which is the point — prices can be rescaled across credit sizes rather than
assumed proportional:

```
 8x500  11    10x500  10    12x500  5    8x1000  4    10x1000  3
10x504   1    14x757   1   12x1000  1   12x400  1    10x600   1
10x350   1    10x300   1    10x250   1
```

**Restricted to 8×500 — our exact shape — 198 assignments, median 3, max 219.** The
same player across three different 8×500 leagues:

```
Malen   219 · 183 · 174
```

`00 §4` cited *"Malen 192 e Martinez L. 191"* from one observed auction as the anchor
the whole price model was to be calibrated against. Both were reproduced independently
the same evening (Malen 211/219, Martinez L. 195/159). They were not anecdotes; they
are the level. And three observations of one player in one format give the **spread**,
which is precisely the `exp(ε)` term in `tasks/asta-plan.md`'s opponent model —
now estimable instead of invented.

Aggregate shape, 871 assignments: median 5, mean 19.3, max 289. **37% clear at 0–1
credits, 6% above 100.** `00 §4`'s claim that the auction turns on four or five
players is no longer an assertion.

## 7. Limits

- **It is a stream, not an archive.** Nothing stores finished auctions. An auction not
  being watched while it runs is lost permanently. Serie A auctions cluster in the last
  ten days of August; that is the window.
- **Turnover is fast.** Measured at 20–30% of live auctions ending per 15–20 minutes,
  with new ones appearing at a similar rate. Without a periodic re-scan, coverage decays
  quickly.
- **The list needs a human-authenticated browser**, so the enumeration step cannot run
  headless or on cron. Only the collector can.
- **`data/quotazioni_mantra.csv` is stale** for this purpose: 21 of 407 auctioned
  players are missing from it (the transfer-window additions `data/README.md` already
  documents). The **database** covers 405/407 — only `7396 Macchioni` and
  `7581 Konaté A.` are absent, both signed after the last scrape. Join against Postgres,
  not the CSV.
- **Team identity is per-auction.** `fantateam_id` is a uuid scoped to its auction; there
  is no cross-auction notion of a manager, so bidder behaviour cannot be tracked between
  rooms.

## 8. Rate and etiquette

Spectator mode is opt-in: a league must enable *"Asta Visibile In Osserva Aste"* before
strangers can watch. Reading it is what the feature is for.

Polling many rooms is nonetheless heavier than a person watching one. At 3 s and 41
auctions this is ~14 requests/second of small JSON GETs against Firebase. Keep the
interval no tighter than the shortest bid timer observed (5 s), poll only Mantra, and
drop auctions once they end — the collector already does the last of these.
