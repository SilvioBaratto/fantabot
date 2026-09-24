# 03 — Platform map & technical surface

Where things live, and what talks to what.

---

## 1. Screens

`app.fantalab.it` is a React SPA (CRA build, `main.a84bc0e9.js` + ~310 lazy chunks). Routes
are client-side; there are **no `<a href>` links**, so navigate by URL directly.

### Tool Asta (`/tool-asta`) — the hub

| Tile | Route | What it is |
|---|---|---|
| Guida All'Asta | `/guida-asta` | Per-club scouting guide, refreshed daily. |
| Simula Asta | `/simulazione` | Roster/budget planner (see `02 §4`). |
| Listone | `/listone` | Player database, Serie A / Euroleghe, season selector. |
| Abbinamenti | `/abbinamenti` | Goalkeeper pairing tool. |
| Osserva Aste | `/aste-live` | **Spectator mode** — ~190–200 public auctions running live. |
| Trova Fanta | `/trova-fanta` | Find a league to join. |
| Centro Dati | `/centro-dati` | Stats explorer (players / teams). |
| Le Tue Leghe | — | Your imported leagues. |
| Creators | `/creators` | ~44 published tier-and-price strategies. |

### Auction lifecycle

| Route | Purpose |
|---|---|
| `/seleziona-asta` | Gestione Aste: **Crea asta da zero** · **Gestisci asta già creata** · **Asta di riparazione**. |
| `/crea-asta` | Full configuration form (see `01 §1`). |
| `/crea-asta-riparazione` | Repair auction: sync from Leghe Fantacalcio, or from a summer FantaLab auction. |
| `/crea-lega?tipo=asta` | League import form. |
| `/asta?asta=<uuid>` | **The auction room.** Same route for admin, participant and spectator; the role decides what renders. |
| `/asta-tv` | Broadcast/TV view of a running auction. |
| `/join-asta`, `/asta-preview`, `/pre-asta` | Invite-link join and pre-room screens. |
| `/magic-asta` | The token-based mini-auction mode. |

An auction is addressed by a UUID in the query string, e.g.
`/asta?asta=fd451375-1808-410f-95e5-7cb2a5196760`.

---

## 2. Spectator mode

`Osserva Aste` is genuinely useful for research: any league can flip
**"Asta Visibile In Osserva Aste"** and strangers can then watch the room live — current
player, current bid and bidder, timer, every team's full roster and every price paid, plus
the Analisi Asta price curve.

Filterable by name, format (Classic/Mantra), call mode (Chiamata / Random / Alphabetic),
credits (500 / 1000) and team count. **This is the cheapest source of real price
distributions we have** — no premium, no scraping of our own league, and it is exactly the
population PMA is computed from.

Since 2026-08-26 this is no longer a plan: it is collected. The room's state is a public
Firebase node carrying every raise with its bidder, so the harvest yields whole bid ladders
rather than clearing prices alone. See [`05`](05-osserva-aste-harvest.md).

⚠️ The list **paginates at 10** and the page-size select must be set to `Tutte` before
reading it. Nothing errors if you forget; you simply see a tenth of the population.

The counterpart setting is **Asta Ninja** (`blind_mode`), which hides information inside
the room.

---

## 3. Technical surface

### Auth

`api.fantalab.it/sign-in` → `api.fantalab.it/auth/firebase-token` →
`identitytoolkit.googleapis.com/v1/accounts:signInWithCustomToken`. So the REST API and
the realtime layer share one identity; the session holds a Firebase ID token.

Email/password, Google and Apple sign-in are all offered.

### REST (`api.fantalab.it`, plus `manager.fantalab.it` for Fanta Manager)

~124 endpoints in the bundle. The ones that matter here:

```
GET  /v2/listone                       the player list
GET  /players                          player records
POST /players/version_new              listone version check
GET  /players/get-season-info-cached-25
GET  /v2/metadata/teams  ·  /v2/metadata/stats
GET  /v2/strategy  ·  /v2/strategy/public  ·  /v2/player-strategy
GET  /experts                          creator strategies
GET  /deployability                    schierabilità indices
GET  /calendar/all  ·  /v2/calendar  ·  /calendario/next-matches
GET  /standings  ·  /referees  ·  /match-lineups/day
POST /seasons  ·  /season/create  ·  /season/update  ·  /season/delete
POST /season/import-league             import from Leghe Fantacalcio
POST /season/re-sync
POST /season/teams  ·  /season/team/players  ·  /season/players-by-team
GET  /asta/ranking  ·  POST /asta/ranking/submit
GET  /magic/config  ·  /magic/gk-pairs  ·  POST /magic/v2/pack/open  ·  /magic/v2/pick
```

Notably **there is no REST endpoint for bidding**. The whole live auction is realtime-only.

### Realtime (Firebase RTDB)

`https://fantalab-79eaa-default-rtdb.europe-west1.firebasedatabase.app`
(plus dedicated `fantalab-audio` and `fantalab-match` databases).

> **Corrected 2026-08-26 — auctions are sharded.** Live auctions do not sit on the default
> database. They are spread across at least twenty namespaces, `fantalab-0` …
> `fantalab-19`, and each auction's list card carries its shard in a field named `db`.
> Seventeen distinct shards were seen in a single evening. **[observed]**
>
> **`auction/<id>` reads without any authentication**, which is what makes price
> harvesting possible; enumeration (`/auction.json?shallow=true`) is denied, and the
> `/aste-live` list itself requires a signed-in session. Full treatment in
> [`05-osserva-aste-harvest.md`](05-osserva-aste-harvest.md).

Node paths seen in the code — **[code]**:

```
auction/<fantaleague_id>      live bid state (§ 01.7)
assign/<fantaleague_id>       assignment handshake
draft/<id>/pack               draft-pack turns, consumed picks, turn_started_at
```

Writes are plain `update()` calls carrying an `update_type`. Clients keep a persisted
clock `offset` and derive the countdown from `last_bid_time` locally — **the timer is not
authoritative on the server**, which is worth knowing if we ever simulate against it.

---

## 4. Leghe Fantacalcio integration — the important bit for `fantabot`

FantaLab talks to leghe.fantacalcio.it in **both directions**.

### Import

`/crea-lega?tipo=asta` → *"Importa Rosa da Leghe Fantacalcio — Inserisci il nome della tua
lega. Assicurati che il nome sia corretto (maiuscole e spazi inclusi)."*

**League name only. No username, no password, no token.** The request goes
`POST api.fantalab.it/season/import-league` and FantaLab's *backend* resolves the league.
That is strong evidence that leghe.fantacalcio.it exposes league composition on a
name-addressable endpoint that needs no user authentication — worth chasing in
`docs/leghe-api.md`, where our own read endpoints are already mapped.

Caveat printed on the page: *"Leghe Fantacalcio non supporta la sincronizzazione delle leghe
a listone."*

`POST /season/re-sync` refreshes an already-imported league.

### Export

The admin settings panel carries **`ESPORTA IN FANTALEGHE`** — it pushes the finished
auction's rosters back into the Leghe Fantacalcio league. This is the seam that makes
"run the asta on FantaLab, play the season on Leghe Fantacalcio" work, and it is the step
our workflow depends on.

> Not exercised during the walk-through — it writes to the real league. Confirm the exact
> behaviour (does it overwrite? does it need admin rights on the Leghe side?) before the
> real asta, not during it.

### Repair auction

`/crea-asta-riparazione` builds an asta di riparazione by syncing current rosters from
Leghe Fantacalcio, so only free agents go on the block. Same import mechanism.

---

## 5. Access tiers

Free covers the auction engine itself: creating and running an auction, all call/raise
modes, timers, roster tracking, the recap panel, and spectator mode.

Premium gates the *information*: **Prezzo Medio Aste** and its history, **FMV Exp.**,
creator strategies, Punti di Forza/Deboli, Ruoli Chiave, Analisi Asta's price curve,
**Prezzo reattivo**, admin/solo mode, and the soundboard.

Practical read: everything premium withholds is a *model output* — an expected fantamedia,
a market-clearing price, a dynamic reprice. Those are precisely the things our CLI would
compute itself from the data we already collect.
