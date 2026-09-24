# 01 — The auction engine

Everything here is what FantaLab's auction room actually does. This is the spec the
simulator has to reproduce.

---

## 1. Auction configuration

Set at creation (`/crea-asta`) and editable afterwards by the admin (⚙ **Impostazioni asta**
inside the room). The object FantaLab POSTs when creating a league — **[code]**, from the
create-asta chunk — is:

```js
{
  fanta_teams: [...], num_teams: 8, num_credits: 500,
  asta_type: "classic" | "mantra",
  asta_mode: "chiamata" | "random" | "alphabetic" | "draft" | "draft_pack",
  raise_mode: "free" | "ordered",
  call_at_quotaz: false,               // "Chiama giocatore al prezzo di quotazione"
  blind_mode: false,                   // "Asta Ninja"
  counter_time_first: 20,              // "Timer prima chiamata"   (seconds)
  counter_time: 10,                    // "Timer ogni rilancio"    (seconds)
  mod: "no_mod" | "yes_mod",           // Modificatore Difesa
  players_list: "serie-a" | "euroleghe",
  playing_mode: "online" | "alone",

  // how the roster is constrained — drives the max-bid cap (§5, §6)
  number_of_players_selection:
      "static" | "no-limit-per-role" | "min-max-per-role" | "min-max-goalie-others",
  players_settings_data: { P: 3, D: 8, C: 8, A: 6 },   // Classic quotas
  min_player_per_position: {},                          // min-max-per-role only
  max_player: 25, min_player: 0,                        // total-roster band

  max_purchases_per_player: 1,         // >1 lets N teams own the SAME player
  unlimited_player_per_role: false,
  overrides: {},                       // manual role reassignments ("Cambio Ruolo")
  admin_id, fantaleague_id, db, is_private
}
```

Those defaults describe a **Classic** league. We play **Mantra** — see §9 — where
`players_settings_data` does not apply at all and the roster is constrained only by
`min_player`/`max_player`, or by a goalkeeper band plus an outfield band.

### Settings panel (admin, in-room) — verbatim

**Tab `Asta`**: Nome Asta · Modalità di chiamata · Modalità di rilancio ·
*Chiama giocatore al prezzo di quotazione* · **Timer prima chiamata `20`** ·
**Timer ogni rilancio `10`** · Numero Massimo Acquisiti Per Giocatore `1` ·
*Prezzo Medio Altre Leghe – Modificatore Difesa* NO/SÍ ·
*Asta Visibile In "Osserva Aste"* NO/SÍ · **Asta Ninja** NO/SÍ ·
Modalità Visualizzazione Delle Squadre Estesa/Compatta

**Tab `Squadre`**: Numero Squadre · Crediti Squadre (global) · then **per-team name and
per-team credit override** — asymmetric budgets are legal.

**Tab `Ruoli`**: the role-limit mode + the four counters (Portieri 3, Difensori 8,
Centrocampisti 8, Attaccanti 6).

**Tab `Cambio Ruolo`**: override a player's role for this auction only.

**Header actions**: `RIMUOVI ASTA` · `GESTIONE SVINCOLI` · `ESPORTA IN FANTALEGHE` ·
`TERMINA ASTA`. Plus in the room header: TV mode, participants, **pause**, history, settings.

---

## 2. Call modes (`asta_mode`) — *who/what gets put on the block*

| UI label | id | Behaviour (app's own description) |
|---|---|---|
| **Chiamata Libera** | `chiamata` | "i giocatori da chiamare vengono scelti liberamente" — the **call turn rotates** team by team, and the team on turn picks any unsold player. |
| **Chiamata Random** | `random` | "i giocatori vengono scelti randomicamente per ruolo". |
| **Ordine Alfabetico** | `alphabetic` | "i giocatori vengono scelti in ordine alfabetico". |
| **Draft** | `draft` | Turn-based pick, no bidding. |
| **Draft con sbusto** | `draft_pack` | Pack-opening draft (the Magic Asta machinery). |

> **The single most misread rule.** "Chiamata Libera" is *free choice of player*, not free
> choice of *when to call*. There is still a strict round-robin call turn: the room shows
> **"È il turno di `<team>` di chiamare un giocatore"** and only that team gets the
> `CHIAMA` button. **[observed]** — and the turn advances even when a call is cancelled.

---

## 3. Raise modes (`raise_mode`) — *who may bid*

| UI label | id | Behaviour |
|---|---|---|
| **Libera** | `free` | "In questa modalità chiunque può rilanciare il giocatore fino allo scadere del tempo." Free-for-all against the clock. |
| **In ordine (Poker)** | `ordered` | Raising rotates in turn order. Teams hold a `raise_state` of `raised` / folded; the update stream carries a `fold` event. A team that folds is out of that player's bidding. |

`raise_state` is an array — **[code]**:

```js
raise_state: [ { fantateam_id: "t1", state: "raised" },
               { fantateam_id: "t2", state: null } ]
```

In `ordered` mode every raise re-derives the array (`reorder` → `raised`) and the next
bidder is read off it; in `free` mode `raise_state` is ignored.

---

## 4. The bidding loop (verified live)

What happens, step by step. Numbers below are the observed defaults.

1. **Turn.** Room announces the call turn. Only that team sees `ASSEGNA` / `CHIAMA`.
2. **Selection.** The caller searches the listone (filter `TUTTI / P / D / C / A`, or by
   Serie A club). Each row shows **PMA**, **Prezzo impostato** (your strategy target) and
   **Quotazione**.
3. **Call.** `CHIAMA` opens the auction at **price = 1**, and **the caller is immediately
   the standing high bidder**. **[observed]** — called Kean, board read `1 · Tuo Team`.
   - If `call_at_quotaz` is on: opening price is the player's `quotazione`
     (`quotazione_mantra` in Mantra). In `free` raise mode the code opens at
     `quotazione − 1` so that the first `+1` lands exactly on the quotazione. **[code]**
4. **Timer.** Counts down from `counter_time_first` = **20 s**. **[observed]** — snapshot
   at 17 immediately after the call.
5. **Raises.** Bid buttons are **`+10`, `+5`, `+1`**, plus a manual-amount control
   (`currency_exchange` icon; the admin can show/hide both via "mostra bottoni di rilancio"
   / "mostra rilancio manuale"). The default programmatic step is `1`. **[code]**
   Every accepted raise resets the timer to `counter_time` = **10 s**.
6. **Guards.** A raise is rejected if either holds — **[code]**:
   - you are already the standing high bidder, or
   - `price + step > maxBid(yourTeam, playerRole)` (§5).
7. **Close.** Timer reaches 0 → `close_auction`: the player is assigned to the high bidder
   at the standing price, `next_turn` is set, and the board clears after ~1.8 s.
8. **Repeat** until every team has filled all 25 slots. The app then offers
   *"Tutti gli slot sono pieni. Vuoi segnare l'asta come conclusa? Genereremo gli
   highlights e la classifica."*

**Cancelling.** The caller/admin can bin an in-flight call (trash icon → "Annulla
Chiamata"). The player returns to the pool, nobody is charged, and the call turn still
advances. **[observed]**

**Undoing a purchase.** Admin can delete a completed purchase:
*"Vuoi davvero eliminare questo acquisto? La squadra che lo possiede riprenderà tutti i
crediti."* — full refund, so mistakes are recoverable.

---

## 5. The max-bid formula

The room shows a `$MAX` figure per team. It is a **hard cap enforced by the client**, not
advice: you must keep enough credits to complete the **minimum** roster you are obliged to
field, at 1 credit per remaining obligation.

The real function, lifted from the bundle **[code]**, branches on
`number_of_players_selection`. The general shape is one line:

```
required_left = however many players you are still OBLIGED to buy
max_bid       = required_left > 0 ? credits_left − required_left + 1 : credits_left
```

Two things that are easy to get wrong and that matter enormously:

- **It reserves against the MINIMUM, not the maximum.** A Mantra league with `min 26 /
  max 31` reserves 26, not 31.
- **Once the minimum is met, the cap disappears.** `required_left ≤ 0` → you may bid every
  credit you hold on a single player. In Mantra, where max > min, this is a real endgame
  weapon: after your 26th player you can dump 100+ credits on one more.

### Per mode

```js
// no-limit-per-role         — Mantra's usual setting
required_left = min_player − n_purchases

// static                    — Classic exact quotas (3/8/8/6)
required_left = Σ over roles of (quota[role] − filled[role])

// min-max-per-role
required_left = Σ over roles of (min_player_per_position[role] − filled[role])

// min-max-goalie-others
required_left = (min_P − filled_P) + (min_others − filled_others)
```

When the client evaluates the cap **for a specific player's role** and that role is already
full, the function returns **0** — you cannot bid at all on a role you have completed.

### Verified against four live boards **[observed]**

| League | credits_left | bought | required_left | formula | shown |
|---|---|---|---|---|---|
| Our Lega Demo, Classic 3/8/8/6 | 500 | 0/25 | 25 | 500 − 25 + 1 | **$476** ✓ |
| Live Classic, endgame | 3 | 23/25 | 2 | 3 − 2 + 1 | **2** ✓ |
| **Fantalabasta**, Mantra, min 26 / max 31 | 500 | 0/31 | 26 | 500 − 26 + 1 | **$475** ✓ |
| **Fantalabasta**, after buying Martinez L. at 191 | 309 | 1/31 | 25 | 309 − 25 + 1 | **$285** ✓ |

The Mantra rows are the ones that prove the "minimum, not maximum" reading: with `max 31`
the naive formula predicts 470, and the board says 475.

---

## 6. Roster / role-limit modes

The dropdown is **"Limite … per ruolo"**, and **the options offered depend on the format**.

### Classic — four options

| Option | `number_of_players_selection` | Meaning |
|---|---|---|
| **Limite singolo per ruolo** | `static` | Exact count per role, e.g. `P 3 / D 8 / C 8 / A 6` = 25. |
| **Min max per ruolo** | `min-max-per-role` | A `[min, max]` band per role. |
| **Nessun limite per ruolo** | `no-limit-per-role` | Only a total-roster band. |
| **Min max portieri e min max altri** | `min-max-goalie-others` | GK band + one band for everyone else. |

### Mantra — only two **[observed]**

| Option | Default |
|---|---|
| **Nessun limite per ruolo** | `Min 25 / Max 30` |
| **Min max portieri e min max altri** | `P Min 2 / Max 5` · `Mov Min 20 / Max 30` |

**There is no P/D/C/A quota in Mantra at all.** The only split the platform offers is
*portieri* vs *movimento*. Mantra has 12 role codes across four lines, so a four-bucket
quota would be meaningless — legality is enforced at *lineup* time by the schema, not at
*auction* time by a quota.

Consequence for bidding: in Mantra **nothing forces you to buy a difensore**. You can end
the auction with 3 GKs and 23 attackers and the auction engine will not object — you simply
will not be able to field a legal XI. That constraint has to live in *our* simulator,
because FantaLab's auction room does not apply it.

`max_purchases_per_player` (default `1`) lets N teams own the same player — off for us.

---

## 7. State machine

Two enums drive the room — both lifted verbatim from the bundle **[code]**.

**`update_type`** — what was just written to the live node:

```
init · first_call · raise · fold · reopen · teams_to_skip · update_turn
reset · confirm · assign · close_auction · asta_db_update · draft
```

**`auctionState`** — what the client renders:

```
confirm (TO_CONFIRM) · running · paused (READY_TO_CALL) · checking (WAITING)
assign · skipTeam · deactivate · notConnected
spectatorActive · spectatorPurchased · spectatorStarting
```

The live auction node (Firebase RTDB, see `03`) carries:

```js
{
  player_id, price, user_id, fantateam_id,   // fantateam_id = current high bidder
  is_first,                                  // true while on the first-call timer
  last_bid_time, asta_state, closed_time,
  next_turn, raise_state, teamsToSkip,
  skip_control, update_type, last_update
}
```

The timer is **derived, not ticked server-side**: clients compute
`remaining = (is_first ? counter_time_first : counter_time) − (now − last_bid_time)`,
correcting `now` with a persisted client/server clock `offset`. **[code]** There is a ~3 s
grace guard against double-fire on close.

`teamsToSkip` lets the admin drop a team out of the call/raise rotation
(*"Sei stato tolto dai turni dall'admin"*). If the admin disconnects the auction pauses,
and another participant may **Prendi il controllo**.

---

## 8. Team accounting object

Per team, the client keeps — **[code]**:

```js
teamPurchasesInfo[fantateam_id] = {
  fantateam_id, max_credits, spent, n_purchases,
  max_player: 25, min_player,
  positions: { P: [...], D: [...], C: [...], A: [...] },
  unlimited_player_per_role
}
```

and each purchase is:

```js
{
  purchase_id, fantaleague_id, fantateam_id, user_id, my_user_id,
  player_id, price,
  price_percentage: Math.round(price / max_credits * 1000) / 1000,
  season, updated_at
}
```

`price_percentage` is the useful one for cross-league comparison: price as a fraction of
the team's budget, so a 500-credit league and a 1000-credit league are directly comparable.

---

## 9. Mantra — our format

**This is the format we play.** The auction *mechanics* are identical to Classic — same call
modes, same raise modes, same timers, same bid buttons, same state machine. What changes is
everything around the roster.

### The 12 role codes

Canonical order, exactly as the listone and the Recap Asta present them **[observed]**:

```
Por · Dc · B · Ds · Dd · E · M · C · W · T · A · Pc
```

They roll up into **five** lines, not four — the mapping is hard-coded in the bundle
**[code]**:

```js
P: ["Por"]
D: ["Ds", "Dc", "B", "Dd"]          // terzino sx, centrale, braccetto, terzino dx
C: ["E", "M", "C"]                  // esterno, mediano, centrocampista
T: ["W", "T"]                       // ala, trequartista
A: ["A", "Pc"]                      // attaccante, punta centrale
```

That extra **T** line is why the Mantra *Valutazione Rosa* grades five departments —
Porta · Difesa · Centrocampo · **Trequartista** · Attacco — where Classic grades four.

**Players can hold more than one role.** `mantra_roles` is a list (the bundle also accepts a
comma-separated string and re-sorts it into canonical order). Observed live: Tourè I. is
`E`/`M`, De Bruyne is `T`, Ilic is `C`, Malen is `Pc`. Multi-role players are worth a premium
that has no Classic equivalent — they fit more schema slots, so they raise the number of
formations a roster can legally field.

### Roster

No P/D/C/A quotas (see §6). Real leagues observed:

| League | Limits | Roster |
|---|---|---|
| Asta Montegranaro (8 teams, 500) | no-limit-per-role | `0/30`, minimum 30 |
| Fantalabasta (8 teams, 500) | min-max-goalie-others | `0/31`, `P Min 3` + `Mov Min 23` = **26 minimum** |
| Crea Asta default | no-limit-per-role | `Min 25 / Max 30` |

Note the gap between minimum and maximum: **26 obliged, 31 permitted**. That gap is exactly
the max-bid loophole described in §5.

### The 11 schemi

The formation list is fixed and matches `data/mantra_schemi.json` in this repo, one for one:

```
3-4-3 · 3-4-1-2 · 3-4-2-1 · 3-5-2 · 3-5-1-1
4-3-3 · 4-3-1-2 · 4-4-2 · 4-1-4-1 · 4-4-1-1 · 4-2-3-1
```

Each schema is 11 typed slots, and **some slots accept two roles**. FantaLab renders them
with a slash — a 4-4-2 reads:

```
Por · Ds · Dc · Dc · Dd · W/E · C · C/M · E · Pc/A · Pc/A
```

### League rules that only exist in Mantra

The *Regolamento Lega* panel carries two toggles Classic does not have **[observed]**:

- **D. Factor** — the Mantra defensive modifier.
- **Imb. Portiere** — goalkeeper clean-sheet bonus.

Both are league-level settings shown inside the auction room, not options in `/crea-asta`.
*(An earlier draft of this document said Mantra simply drops the defence modifier. That was
wrong: it moves, and it gains a companion.)*

### Fasce

**Six tiers in Mantra, not five.** The Recap Asta tracks
`Top · Semi-Top · Terza · Quarta · Scomm. · Evitare`, per Mantra role. The sixth tier's
default name in the bundle is `"Riserve"` **[code]** — leagues and creators rename fasce
freely (*"Modifica Nomi Fasce"*), so `Evitare` is a rename, not a separate concept.

### Prices

Read `quotazione_mantra` / `fvm_mantra`. They are **not** the Classic numbers: Svilar's
Mantra quotazione is 18 where his Classic auction price was 43. Never join the two.

---

## 10. Magic Asta (a different game, noted for completeness)

Not our format, but it is the only place FantaLab publishes a *formal* ruleset, and it
reuses the `draft_pack` machinery:

- 10 participants, one **gettone** (token) per entry, lobby auto-starts when full.
- 25-man roster from **24 consecutive packs** of 10 players each — the first pick pairs GK1
  and GK2, which is why 24 packs yield 25 players. Split `3 P / 8 D / 8 C / 6 A`.
- Player credit value = the FantaLab Manager value (see `02`).
- First pick order is drawn at random; **from the second pick onward the order is
  recomputed before every round by ascending total roster value** — a self-balancing snake.
  Ties break on the initial random draw.
- Miss your turn → auto-assigned the **highest-value player left in the pack** (random among
  equals).
- The roster is valid for exactly one Serie A matchday, then decays.
