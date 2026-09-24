# 06 — The write path: joining an asta and bidding from a CLI

Measured on **2026-08-28**, in a throwaway 4-team Mantra auction created for the purpose.
[`05`](05-osserva-aste-harvest.md) established that an auction can be *read* by anyone who
can name it. This file is the other half: **what it takes to sit at the table and raise.**

The short version: the asta is Firebase Realtime Database and nothing else. There is no
REST endpoint that places a bid, and there is no websocket requirement either — a single
authenticated `PATCH` over plain HTTPS is a complete bid. That was executed end-to-end
against a live room and the browser UI rendered it.

Confidence markers as in [`README`](README.md): **[observed]** means it was seen on the wire
on 2026-08-28; **[code]** means it was read out of the shipped bundle (`main.e5a74a05.js`
plus its 317 route chunks) and not exercised; **[inferred]** is reasoned from those.

Method, for anyone repeating it: the bundle was pulled from `asset-manifest.json` and its
endpoint registry (module `32313`, 214 entries) extracted whole; then `WebSocket.prototype.send`,
`fetch` and `XMLHttpRequest.prototype.send` were wrapped in the live room so every frame the
client emitted could be read while the auction ran.

---

## 1. What is authenticated and what is not

Extends the table in [`05 §1`](05-osserva-aste-harvest.md).

Extends the table in [`05 §1`](05-osserva-aste-harvest.md). **Corrected 2026-08-28** — an
earlier draft of this table read a `401` on a partial write as "auth required". It is not: see
[§10](#10-live-verification-2026-08-28--the-rules-are-validation-not-auth).

| Surface | Auth | Notes |
|---|---|---|
| `GET  fantalab-<db>.…firebasedatabase.app/auction/<fantaleague_id>.json` | **none** | Unchanged from `05`. **[observed]** |
| `GET  api-cdn.falsesoftware.com/v2/players/list` | **none** | The `player_id` the auction speaks. Same uuids. **[observed]** |
| `PATCH …/auction/<fantaleague_id>.json` — **valid** raise, **no token** | **allowed** | `200`, echoed with `.sv` resolved. **A bid needs no authentication at all.** **[observed 2026-08-28, headless]** |
| `PATCH …/auction/<fantaleague_id>.json` — below-price / wrong-player / partial | **denied** | `401 {"error":"Permission denied"}` — a **validation** failure (bad *values*), whether or not a token is sent. **[observed]** |
| `PATCH …/auction/<fantaleague_id>.json` — `close_auction` from a non-admin | **denied** | `401` — this rule *does* check `auth.uid == admin_id`, so **settling needs the admin token**. **[observed]** |
| `PUT   …/offset/<uid>/<key>.json?auth=<idToken>` | **allowed** | Per-client clock offset. **[observed]** |
| `POST  api.fantalab.it/fantaleague/fetchByInvitation` | **required** | Resolves an invite link (REST, needs a Bearer). **[observed]** |

**The 401-means-validation trap.** A malformed or below-price write to `auction/` fails
`401 Permission denied` — the same message an auth failure gives. It is **not** an auth wall:
a *complete, valid* raise with **no token** succeeds (§10). Anything probing write access must
send a well-formed, rule-satisfying payload or it will conclude, wrongly, that a token is
required. It is not — for bidding.

---

## 2. Auth — one identity, two layers

FantaLab's identity provider is **not** Firebase and **not** `api.fantalab.it`. It is an
AWS API Gateway host; Firebase identity is minted from it afterwards by custom token.

```
1.  POST https://1ksjqb123i.execute-api.us-east-2.amazonaws.com/prod/auth/email/login
        {"email": "...", "password": "..."}
    → {access_token, id_token, refresh_token}                              [code]

2.  POST https://api.fantalab.it/auth/firebase-token
        Authorization: Bearer <id_token>
    → {"firebase_token": "<custom token>"}                                 [code]

3.  signInWithCustomToken(<custom token>) against Identity Toolkit, apiKey
        AIzaSyAUil-4mmWcJc-eaTBhZdxd43EHLmVtcds     (public web config)    [code]
    → a Firebase idToken, aud/iss = project fantalab-79eaa                 [observed]

4.  refresh: POST …/prod/auth/token/refresh {"refresh_token": …}           [code]
```

- **`POST /create-token` (`GET_FIREBASE_TOKEN` in the registry) is dead.** It is declared in
  the endpoint table and called from nowhere in the bundle. `/auth/firebase-token` is the
  live one. **[code]**
- The Bearer the browser sends to `api.fantalab.it` is the **`id_token`**, not the
  `access_token`. **[code]**
- **The Firebase idToken from step 3 is also accepted as `Bearer` by `api.fantalab.it`** —
  verified against `/fantaleagues/live`, `/fantaleague/fetchByInvitation` and `/user`, all
  `200`. So after the exchange a client can carry **one** token for both layers. **[observed]**
- Firebase idTokens are the usual **1 hour**; refresh before `exp`. **[observed]**
- The web client instantiates **21 Firebase apps** — the default plus one per shard, named
  `"0"`…`"19"` — and signs the same custom token into every one. All share one uid, so the
  shard count is a connection-pooling detail, not an identity one. **[observed]**
- The account used here signs in with Google. **Whether a Google-only account can obtain a
  password login at step 1 was not tested** — see `§8`.

> Storage rule, inherited: the triple plus the derived Firebase idToken are bearer
> credentials. They belong encrypted in Postgres via `tokens/`, never on argv, never in a
> log line, never in a `repr`. `tests/test_token_secrecy.py` already enforces the shape of
> that promise for the Leghe token and must be extended, not worked around.

---

## 3. Joining by invite link

**Link shape** — minted client-side as `https://app.fantalab.it/join-asta?invitation_id=<uuid>`.
`invitation_id` is a uuid v4 **distinct from `fantaleague_id`**. **[code]**
A shortened alias may also be issued via `POST api.fantalab.it/v2/links`; resolve it with a
plain redirect follow and take the query param. **[code]**

The **USA LINK** modal on `/tool-asta` does no parsing at all — its confirm handler is
`window.open(pasted, "_blank")`. All the logic lives on the `/join-asta` route. **[code]**

**Step 1 — resolve.**

```
POST https://api.fantalab.it/fantaleague/fetchByInvitation
     Authorization: Bearer <idToken>
     {"invitation_id": "<uuid>"}
```

Returns the whole league record **flat** — `fantaleague_id`, `fantaleague_name`, `admin_id`,
`db`, `asta_type`, `asta_mode`, `raise_mode`, `num_teams`, `num_credits`, `counter_time`,
`counter_time_first`, `players_settings_data`, `is_live`, `auction_running`, `is_private`,
`season`, plus `fantateams[]`. **[observed]**

Each `fantateams[]` entry: `fantateam_id`, `user_id`, `team_name`, `position`, `max_credits`,
`takeover`, timestamps. **A seat is free iff `user_id === null`.** **[observed]**

> The room page reads the league name as `data.fantaleague_name`, one level deeper than every
> other field it reads off the same object. The response is flat, so that read yields
> `undefined` — a client bug, not a nested envelope. **[observed]**

**Step 2 — claim a seat.** Exactly three keys; the response body is discarded by the client,
which reads only `err.data.message` on failure. **[code]**

```
POST https://api.fantalab.it/fantaleague/join
     {"invitation_id": "<uuid>", "fantateam_id": "<a free seat>", "user_id": "<uid>"}
```

**Step 3 — the poke** (optional, cosmetic): an `update()` on `auction/<fantaleague_id>` with
`{last_update_asta: {".sv":"timestamp"}, update_type: "asta_db_update", last_update: …}`,
which nudges other clients to re-fetch the league over REST. **[code]**

No password, no credits, no premium gate on this path. The credits gate people associate with
"joining a league" belongs to the unrelated FindFanta marketplace (`POST /findfanta/join`). **[code]**

The room is then `/asta?asta=<fantaleague_id>` — **the `asta` query param is the
fantaleague_id**, not a separate id. **[observed]**

`POST /fantaleague/fetch` `{fantaleague_id, type:"fantaleague"}` returns the same record and
is what the room itself bootstraps from, so a bot already holding a `fantaleague_id` needs no
invitation. **[code]**

---

## 4. The realtime surface

**Shard resolution.** `db` is an **integer index, not a hostname**:

```
db = 0..19   →  https://fantalab-<db>.europe-west1.firebasedatabase.app/
db = null    →  https://fantalab-79eaa-default-rtdb.europe-west1.firebasedatabase.app
```

A missing `db` means the **default** namespace, *not* shard 0. In list payloads the same
value is aliased `db_shard`; accept either key. **[code]** — the `fantalab-<db>` form itself
is **[observed]**, and note it carries no `-default-rtdb` infix.

Websocket form, for a streaming client: `wss://fantalab-<db>.…/.ws?v=5&ns=fantalab-<db>`,
`@firebase/database 0.13.3`, protocol `5`. **[code]**

**Nodes, all on the auction's own shard** (`<fl>` = `fantaleague_id`):

| Path | Carries |
|---|---|
| `auction/<fl>` | live bid state (a **CHIAMA** lot) — the table below **[observed]** |
| `assign/<fl>` | an **ASSEGNA** lot — same schema, same reducer, a real biddable lot on a *separate node*. A bot watching only `auction/` silently misses every ASSEGNA lot. **[observed 2026-08-28 — see §10.6]** |
| `purchases/<fl>/<purchase_id>` | the settled-sale ledger; **both CHIAMA and ASSEGNA lots settle here identically** **[observed]** |
| `offset/<uid>/<session>` | `{time:{".sv":"timestamp"}}`, the server-clock correction **[observed]** |
| `chat/<fl>`, `users_online/<fl>/…`, `soundboard/<fl>`, `draft/<fl>` | chat, presence, sounds, draft modes **[code]** |

**`auction/<fl>` schema.** Flat, small, every field optional — a freshly `init`ed node holds
only `next_turn`, `update_type`, `last_update`. **[observed]**

| Field | Type | Meaning |
|---|---|---|
| `fantaleague_id` | string | echoed on writes |
| `player_id` | string | the lot on the block |
| `price` | int | current high bid |
| `user_id` | string | current high bidder — **in the clear even in `blind_mode`** |
| `fantateam_id` | string | their seat |
| `is_first` | bool | first call for this lot → uses `counter_time_first` |
| `last_bid_time` | int | server ms; written as `{".sv":"timestamp"}`; the countdown anchor |
| `asta_state` | `"closed"` \| null | freezes the lot |
| `closed_time` | int | stamped at close |
| `skip_control` | bool | written by `close_auction`; no reader branches on it **[inferred]** |
| `next_turn` | string | `fantateam_id` whose turn it is to call |
| `timeToPass` | int | seconds carried on the close write |
| `raise_state` | array \| `{}` | `ordered` mode only; **`{}` on reset — tolerate both shapes** |
| `teamsToSkip` | string[] | seats excluded from rotation **[code]** |
| `blind_mode` | bool | "Asta Ninja", display-only **[code]** |
| `last_fold` | string | last folding seat **[code]** |
| `update_type` | enum | below |
| `last_update` | int | `Date.now() - offset`, client-stamped |
| `last_update_asta` | int | changing it signals "re-fetch the league over REST" |

`update_type`, verbatim from the bundle: `init, first_call, raise, fold, reopen, reset,
update_turn, teams_to_skip, confirm, close_auction, assign, asta_db_update, draft`. **[code]**
Of these, `init`, `first_call`, `raise`, `close_auction` and `confirm` were seen on the wire. **[observed]**

---

## 5. The writes, as captured

Operation is Firebase `update()` — a **merge**, never `set`/`push`/`runTransaction`. There is
no compare-and-swap anywhere in the client. **[code]** Over REST that is `PATCH`. **[observed]**

```
PATCH https://fantalab-<db>.europe-west1.firebasedatabase.app/auction/<fl>.json?auth=<idToken>
```

**Call a player** — `update_type: "first_call"`:

```json
{"price": 1, "fantaleague_id": "<fl>", "user_id": "<uid>", "fantateam_id": "<seat>",
 "player_id": "<player_id>", "is_first": true, "raise_state": [],
 "update_type": "first_call", "last_bid_time": {".sv": "timestamp"},
 "asta_state": null, "closed_time": null, "last_update": <epoch ms>}
```

**Raise** — `update_type: "raise"`:

```json
{"price": <current + step>, "fantaleague_id": "<fl>", "user_id": "<uid>",
 "fantateam_id": "<seat>", "player_id": "<player_id>", "is_first": false,
 "update_type": "raise", "last_bid_time": {".sv": "timestamp"},
 "last_update": <epoch ms>}
```

In `raise_mode: "free"` a raise echoes `raise_state` back unchanged; in `ordered` it is built
by a helper that was not decoded (`§8`). **[code]**

**Close the lot** — written when the timer runs out:

```json
{"asta_state": "closed", "closed_time": {".sv": "timestamp"}, "skip_control": false,
 "player_id": "<player_id>", "timeToPass": 8,
 "update_type": "close_auction", "last_update": <epoch ms>}
```

**Confirm the sale** — every bid field nulled, turn handed on:

```json
{"price": null, "player_id": null, "user_id": null, "fantateam_id": null,
 "is_first": null, "last_bid_time": null, "asta_state": null, "closed_time": null,
 "skip_control": null, "next_turn": "<next fantateam_id>", "raise_state": {},
 "fantaleague_id": "<fl>", "update_type": "confirm", "last_update": <epoch ms>}
```

**The purchase record** — a separate node, written in the same beat as `confirm`:

```
PUT …/purchases/<fl>/<purchase_id>.json?auth=<idToken>
{"purchase_id": "<uuid>", "player_id": "<player_id>", "user_id": "<uid>",
 "fantaleague_id": "<fl>", "fantateam_id": "<seat>", "season": "s_24_25",
 "price": 16, "my_user_id": "<uid>", "price_percentage": 0.032,
 "created_at": <epoch ms>, "updated_at": <epoch ms>}
```

`price_percentage` = `price / num_credits` (16/500 = 0.032, 1/500 = 0.002). **[observed]**
An **unsold / skipped** lot is stored as a purchase with `price: 0` and **no** `fantateam_id`
(the key is omitted, not null). **[observed 2026-08-28]** The bundle mentions a `call_type` key
(`"random"`/`"skip"`), but **none of 13 real purchase records carried it** — it appears unused
in production; do not rely on it. **[observed, correcting earlier [code]]**

> **The purchase has no REST mirror.** The bundle contains a `PUT purchase/create` call site,
> but `fetch` **and** `XMLHttpRequest` were both tapped across a complete
> close→confirm→purchase cycle and **not one non-GET request was made**. On this path the
> RTDB node is the only record written. Treat `purchases/<fl>` as the ledger of record.
> **[observed]**, contradicting **[code]**

---

## 6. Guards, timers, and what the server enforces

The client refuses its own raise if any of these hold — all four are plain JS
short-circuits, so **a CLI must reimplement them**: **[code]**

1. `auction.asta_state === "closed"`
2. `now - auction.last_bid_time <= 500` ms
3. `payload.player_id !== auction.player_id`
4. `!(payload.price > auction.price)`

**The countdown is not a field.** No node carries remaining time; it is recomputed on a
500 ms tick: **[code]**

```
remaining = (is_first ? counter_time_first : counter_time)
          - round((now - offset - last_bid_time) / 1000)
```

`counter_time` / `counter_time_first` come from the **REST** league record (10 / 20 in the
auction measured), not from the realtime node. **[observed]** `offset` is the per-client
clock correction mirrored at `offset/<uid>/<session>`. A bot that computes the deadline from
`last_bid_time` sees the true remaining time rather than the rendered one.

**What the server enforces, and what it does not** — settled 2026-08-28 (§10), overturning
this section's earlier "unproven / probably client-side" hedge:

- **Value rules ARE server-enforced.** A raise with `price ≤ current` → `401`, no write. A
  raise with the wrong `player_id` → `401`, no write. These are RTDB `.validate` rules; the
  four client-side guards mirror them (except the 500 ms debounce, which is client-only —
  two writes 120 ms apart both `200`). **[observed]**
- **Identity is NOT enforced.** A valid raise carrying a `fantateam_id`/`user_id` the writer
  does not own is accepted `200` and becomes the high bid. One process can drive every seat.
  **[observed]**
- **Auth is NOT required to bid.** A valid raise with **no token at all** is accepted `200`.
  **[observed, headless]**
- **Turn is not enforced** (in `raise_mode:free`). A raise out of `next_turn` is accepted.
  **[observed]**
- **Settling IS gated.** `close_auction`/`confirm` from a non-admin → `401`; that rule checks
  `auth.uid == admin_id`, so those two writes need the **admin's** token. A bidding bot never
  settles, so it never needs auth; a bot that must also settle needs the admin token. **[observed]**

The upshot for the CLI: **a bidder needs nothing but the shard URL, the `fantaleague_id`, a
seat's `fantateam_id`/`user_id`, and the live `player_id`/`price` — all readable without
auth.** See the security note in §10.

---

## 7. What this changes in the earlier files

Neither file is amended — they are records of what was known when written.

- [`03 §3`](03-platform-map.md) lists `POST /create-token` in the auth chain. That endpoint is
  **dead code**; the live bridge is `POST /auth/firebase-token`, and the identity provider
  upstream of both is the AWS host in `§2`, not `api.fantalab.it/sign-in` (which is a profile
  upsert run *after* the Bearer exists).
- [`03 §3`](03-platform-map.md) says "there is no REST endpoint for bidding". Still true, and
  now stronger: there is no REST endpoint for the *sale* either.
- [`05`](05-osserva-aste-harvest.md) reads `auction/<id>` unauthenticated. Unchanged — and the
  write path needs no new read permission, only `assign/<fl>` and `purchases/<fl>` added to
  the subscription set.

---

## 8. Still unknown, with the experiment for each

Several rows of the original list were **settled 2026-08-28** — see [§10](#10-live-verification-2026-08-28--the-rules-are-validation-not-auth). What remains:

| Unknown | What settles it |
|---|---|
| ~~Do RTDB rules enforce anything beyond payload shape?~~ **RESOLVED (§10):** values enforced (`price>current`, player match), identity **not**, auth **not required** to bid; `close_auction` needs admin. | — |
| **Can a Google-only account get a password login** at `/auth/email/login` | One call with the real address; a `401`/`404` answers it. *Moot for bidding (no auth needed); relevant only to settle as admin.* |
| **`fantaleague/join` failure modes** — seat lost to a race, auction already running, league full | Open `/join-asta?invitation_id=…` for a full league with the network tab recording |
| **`raise_state` in `raise_mode: "ordered"`** — built by an undecoded helper (module `70120`, export `_k`) | Join an `ordered` room, raise once, diff the node before and after |
| **`skip_control` semantics** | Let a lot expire with no bids and read the node immediately after close |
| **`assign/<fl>` payload** — asserted to share the auction reducer, never observed | Have an admin assign a player manually while subscribed |
| **Whether any endpoint wants `access_token` rather than `id_token`** | Send one call with the access_token and see whether it 401s |
| **Draft / `draft_pack` modes** | Separate pass; run one and dump `draft/<fl>` |

---

## 9. Consequences for the CLI

Sketch only — the modules are not written yet.

- **Pool connections by shard, not by room.** ≤21 namespaces cover every auction that exists;
  many rooms share one. This is the same lesson `aste-collect` learned as `--pool`.
- **Subscribe to three nodes, not one** — `auction/`, `assign/`, `purchases/` — or the roster
  a bot reconstructs will silently omit assigned players.
- **Budget is derived, not served.** `remaining = max_credits - sum(prices in purchases/ for
  that seat)`; `max_credits` comes from the REST record. **[code]**
- **Turn order is computed client-side** from `position` ascending, skipping full seats and
  `teamsToSkip`, wrapping past the current turn. Nothing serves it. **[code]**
- **The evening ends** only when an admin POSTs `fantaleague/update` with
  `auction_completed: true, auction_running: false`. `close_auction` ends a *lot*. **[code]**
- **Keep the decision pure.** `(snapshot, my seat, remaining budget, target price) → payload
  or None` has no I/O and belongs beside `strategy.decide_bid`; the socket and the `PATCH`
  belong in the shell. The four guards of `§6` live in the pure half, where they are testable.
- **`FANTABOT_AUTO_ACT` now gates four distinct writes**, and they are not equally safe:
  `fantaleague/join`, the `asta_db_update` poke, the `raise`, and the sale writes
  (`close_auction` / `confirm` / `purchases`). A non-admin bot must never send the last group.
- **The collection path still must not import `fantabot.db`** — more so here than for the
  harvester. A database outage must cost catch-up time, never a bid.
- **A bid loop is a run with no end**, so it must speak while it runs: last snapshot age,
  seconds to close, raises refused per guard. The exit summary is never reached — and a raise
  that lost a race looks exactly like a raise that was never sent unless something counts it.

---

## 10. Live verification 2026-08-28 — the rules are validation, not auth

A second session drove a throwaway 4-team Mantra auction (`asta_mode: random`,
`raise_mode: free`, shard `db=9`) from two angles: the browser as a seated non-admin
participant, and — the decisive part — a **plain Python process over Bash, no browser**.
Everything below is **[observed]**, on the wire, that day.

### 10.1 What was proven

| # | Test | Result |
|---|---|---|
| 1 | **Join a seat**, `POST fantaleague/join` | `200 {"message":"Fantateam Joined"}`. Body that worked was **`{fantateam_id, user_id}`** — `invitation_id` **not required** when the `fantateam_id` is already known. |
| 2 | **Bid as a seated non-admin**, `PATCH …/auction/<fl>.json?auth=<idToken>` | `200`, became and held high bidder across multiple lots. |
| 3 | **Bid with NO token**, valid raise, headless Python | `200`. `.sv` timestamp resolved. **Bidding requires no authentication whatsoever.** |
| 4 | **Below-price raise** (`price ≤ current`), any auth state | `401`, no write. |
| 5 | **Wrong `player_id`** at a valid price | `401`, no write. |
| 6 | **Two raises 120 ms apart** | both `200`. No server debounce; the 500 ms floor is client-only. |
| 7 | **Drive a seat you don't own** (valid raise, foreign `fantateam_id`/`user_id`) | `200`, the node's high bid became that seat. **No identity binding.** |
| 8 | **`close_auction` from a non-admin** | `401`. That rule checks `auth.uid == admin_id`; settling needs the **admin** token. |
| 9 | **Out-of-turn raise** in `raise_mode:free` | `200`. Turn does not gate raises. |

### 10.2 The model, stated plainly

The auction node's RTDB security rules are **validation rules, not an auth gate**:

- **A write succeeds iff its *values* are legal** — `price` strictly greater than the current
  `price`, `player_id` equal to the lot on the block, and (for `close_auction`/`confirm`) the
  writer authenticated as the league admin. Auth is *not* otherwise required.
- `401 "Permission denied"` is Firebase's response to a **failed `.validate`** just as much as
  to a missing credential. The two are indistinguishable from the client — the trap in §1.
- Consequently a bidder, **once it knows** the shard, its seat's `fantateam_id`/`user_id`, and the
  `fantaleague_id`, needs **no login and no token** to bid: the live lot (`GET auction/<fl>.json`)
  and the raise are both unauthenticated.

> **Correction 2026-08-28 (verified headless):** the *discovery* calls are **not** public. `POST
> /fantaleague/fetch` and `GET /fantaleagues/live` (and `fantaleague/join`) answer **`401`
> without a Bearer** — only the RTDB nodes and the player CDN are open. So a participant bot must
> be **given** its `db` shard, seat and uid (a seat is claimed once, interactively, with a token);
> it then bids fully unauthenticated. `fantalab.rest.fetch_league`/`live_leagues`/`join_team` take
> an optional `token` for the authenticated discovery/admin path; `asta-bid` and `asta-live
> --league` take `--db` and touch no auth'd REST at all. An earlier draft here wrongly called
> `fetch`/`list` public — it conflated the unauthenticated *RTDB* surface with the *REST* one.
>
> **Amended 2026-09-20.** "touch no auth'd REST at all" is still true of every *bid* and of
> every ledger read, and is no longer true of `asta live --league` as a whole: it now probes
> `fetch_league` for the room's `asta_type`, because defaulting the format silently priced a
> Classic room as Mantra. The probe is **optional by construction** — a missing session, a
> missing encryption key or an unreachable FantaLab each degrade to the recorded corpus and
> then to `--format`, so the command still runs with no credential at all. What it will not
> do any more is guess.

### 10.3 Consequence for the CLI (supersedes part of §9)

- **The bid path needs no auth chain.** `fantalab/auth.py` (§9) is required **only** to settle
  as admin (`close_auction`/`confirm`) or to call the REST bootstrap with a Bearer. A pure
  *participant* bidder can run on unauthenticated reads + writes alone.
- **`FANTABOT_AUTO_ACT` still gates every write** — more important here, not less, precisely
  because the platform will not stop a malformed or hostile write on the bot's behalf.
- The four client guards in §6 are now known to be a *mirror* of real server rules (bar the
  500 ms debounce). Keep reimplementing them — not because the server won't reject a bad bid
  (it will, with a `401`), but so the bot doesn't waste a round trip and can tell a
  rule-refusal from a lost race.

### 10.4 Security note — this is a real exposure, not just a convenience

Because a valid bid needs no authentication and no seat ownership, **anyone who can name a
public auction** (its `fantaleague_id` + shard, both in the unauthenticated `fantaleagues/live`
list) **can place or inflate bids in it as any team.** For `fantabot` that is what makes the
bidder trivial; for the user's **real** leagues it means a live asta is open to griefing by any
outsider. Worth reporting upstream to FantaLab. It also argues for running our own leagues with
**Osserva Aste visibility OFF** where possible, so the room is at least not enumerable from the
public list.

### 10.6 CHIAMA random vs ASSEGNA random — two nodes

The admin of a `random` auction has two controls, and they route a lot to **different nodes**:

| Admin action | Lot lands on | Bid node | Bot must watch |
|---|---|---|---|
| **CHIAMA random** | `auction/<fl>` | `auction/<fl>` | `auction/<fl>` |
| **ASSEGNA random** | `assign/<fl>` | `assign/<fl>` | **`assign/<fl>`** — else it is invisible |

Observed `assign/<fl>` write (same schema as `auction/<fl>`):

```json
{"fantaleague_id":"<fl>","player_id":"<random>","price":1,
 "user_id":"<uid>","update_type":"assign","next_turn":"<next seat>",
 "last_bid_time":<ms>,"last_update_asta":<ms>}
```

- The price is raiseable on `assign/<fl>` exactly as on `auction/<fl>` (watched it climb
  1→2→4→6). Between lots, `auction/<fl>` gets an `update_type:"reset"` and `assign/<fl>`
  briefly returns to `null`.
- An assigned lot **settles to `purchases/<fl>` with the identical record shape** as a called
  lot; an unsold one is the `price:0`, no-`fantateam_id` record noted in §5.
- **Consequence, now proven not inferred:** a bidder that subscribes only to `auction/<fl>`
  bids on nothing during an ASSEGNA-run auction. The reducer is shared, so the fix is one more
  subscription to `assign/<fl>` feeding the same state machine — not new parsing.

### 10.5 Still not exercised here

`raise_mode: ordered` (this league was `free`; the ordered `raise_state` builder is still
undecoded), `draft`/`draft_pack` modes, and whether a *participant* (not the admin) may write
`assign/<fl>`. These stay on the §8 list.
