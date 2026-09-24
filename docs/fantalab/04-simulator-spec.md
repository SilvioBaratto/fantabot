# 04 — What the CLI simulator has to implement

Translating `01`–`03` into a build target for `fantabot`, with our league's numbers.

> **Format: Mantra.** An earlier revision of this file was written for Classic. Mantra keeps
> the same auction *mechanics* but changes the roster model, the role model, the budget
> model and the price scale. Everything below has been reworked accordingly; `01 §9` is the
> reference.

---

## 1. League parameters

Status as of **2026-08-25**. ✅ = confirmed by the league, ⏳ = still open,
▫ = FantaLab default assumed until told otherwise.

```python
ASTA_CONFIG = {
    "asta_type": "mantra",          # ✅ we play Mantra
    "num_teams": 8,
    "num_credits": 500,

    # roster — NO P/D/C/A quotas exist in Mantra
    "number_of_players_selection": "min-max-goalie-others",   # ⏳ or "no-limit-per-role"
    "min_goalkeepers": 3, "max_goalkeepers": 3,               # ⏳
    "min_others": 23,     "max_others": 28,                   # ⏳
    # → min_player = 26, max_player = 31   (the shape seen in a live 8×500 Mantra league)

    "asta_mode": "random",          # ⏳ leaning random, not decided
    "raise_mode": "free",           # ✅ "rilancio libero"
    "call_at_quotaz": False,        # ▫ calls open at 1
    "counter_time_first": 20,       # ⏳ decided Thu 2026-08-27 in a test asta
    "counter_time": 10,             # ⏳ same
    "max_purchases_per_player": 1,  # ▫
    "d_factor": False,              # ✅ "modificatore difesa no" — Mantra calls it D. Factor
    "imb_portiere": None,           # ⏳ never asked. Ask.
}
```

### What the confirmed answers buy us

**`raise_mode: "free"` is locked.** Free-for-all against the clock, and the `ordered`
(Poker) branch — fold state, rotating raise turn, public pass information — **does not need
to be built**. Keep `fold` in the action enum as a stub so a future Poker league is an
extension, not a rewrite.

**`d_factor: False` is locked.** No defensive modifier, so `Dc`/`B`/`Ds`/`Dd` are valued on
their own bonus/malus alone.

> **None of the `⏳` rows blocks the build.** They are runtime configuration, resolved by a
> startup wizard and sweepable in the meantime — see **`§9`**. Do not hard-code any of them,
> and do not wait for Thursday to start.

### Still open, in order of how much they change the build

**Roster limits.** This is now the *first* question, not a detail. Mantra offers only two
shapes (`01 §6`), and the numbers drive the max-bid cap directly. Two live 8-team/500
Mantra leagues observed used `min 30 / max 30` and `min 26 / max 31` — a five-slot spread
that changes the whole endgame.

**`asta_mode`.** The league leans **random**, and that matches the wider Mantra community:
of the ten live Mantra auctions listed on `/aste-live`, **seven were `Random`** against
three `Chiamata`. Under `random` our agent has no call-selection lever at all and the policy
collapses to "what is this player worth to me right now" — which is a genuinely simpler
build. Implement `chiamata` anyway: `random` is a strict subset of it.

**`imb_portiere`.** Never asked in the group. If the goalkeeper clean-sheet bonus is on,
`Por` valuation moves a lot. Add it to the next round of questions.

**Timers.** Being measured Thursday 2026-08-27 — see `§8`.

---

## 2. Derived economics

Assuming `min 26 / max 31`:

| Quantity | Value |
|---|---|
| Credits in the league | `8 × 500 = 4000` |
| Slots the league is *obliged* to fill | `8 × 26 = 208` |
| Slots the league *may* fill | `8 × 31 = 248` |
| League mean price at minimum roster | **≈ 19 credits/player** |
| Opening max bid | `500 − 26 + 1 = 475` |
| **Max bid once your minimum is met** | **your entire remaining balance** |
| Listone size | 519 (FantaLab) / 523 (ours) |
| Players left unsold | ~300 (**~58 %**) |

The third row is the one with no Classic analogue. With `max > min`, a team that has met its
26-player obligation faces **no cap at all** and can put its full balance on one player.
Model it, or the simulator will systematically under-price the endgame.

---

## 3. Core loop to reproduce

```
for each call turn (round-robin over the 8 teams, skipping teams_to_skip):
    if asta_mode == "random":  player = draw_unsold()
    else:                      player = caller.choose_unsold()
    price = 1 ; high_bidder = caller
    deadline = now + counter_time_first
    while now < deadline:
        for each team that is not high_bidder:
            if wants_to_raise(team, player, price):
                step = choose_step(team, ...)            # 1, 5, 10 or manual
                if price + step <= max_bid(team, player.mantra_roles):
                    price, high_bidder = price + step, team
                    deadline = now + counter_time
    assign(player, high_bidder, price)
until every team has met min_player and no team wants to keep buying
```

Invariants the engine enforces and the simulator must too:

1. **You cannot outbid yourself.**
2. **`price + step ≤ max_bid`**, with `max_bid` computed the FantaLab way (`01 §5`):
   `required_left = min_player − n_purchases`, then
   `max_bid = required_left > 0 ? credits_left − required_left + 1 : credits_left`.
3. **Termination is not "everyone at 25".** In Mantra a team may stop anywhere between
   `min_player` and `max_player`. The end condition is the admin marking the auction closed,
   which in practice means every team has met its minimum and nobody wants more.

### The constraint FantaLab does *not* enforce — and we must

The auction room has **no P/D/C/A quotas in Mantra**. Nothing stops a team from ending the
auction with 3 goalkeepers and 23 attackers. The room will happily let it happen; the team
simply cannot field a legal XI afterwards.

So **lineup legality is our simulator's job, not the platform's**. A roster is only viable
if it can fill at least one of the 11 schemi from `data/mantra_schemi.json`, where slots are
typed and some accept two roles. Concretely, the simulator needs a
`can_field(roster, schema) -> bool` (a bipartite matching between players' `mantra_roles`
and the schema's slot types) and should score a roster by **how many schemi it can field**,
not just by what it cost.

That single function is the biggest piece of genuinely new work in this project.

---

## 4. Where this lands in the existing codebase

`strategy.py` holds the pure decision logic and must stay pure. The auction simulator is the
same shape — no I/O, injected clock — so it belongs beside it, not inside `auction.py`
(the Playwright/network shell for the real leghe.fantacalcio.it room).

```
src/fantabot/mantra/
    roles.py       the 12 codes, the role→line map, multi-role parsing (";"-separated)
    schemi.py      load mantra_schemi.json; can_field(); count_fieldable_schemi()
    lineup.py      pick the best legal XI for a roster  ← replaces the Classic-only path

src/fantabot/asta/
    config.py      frozen AstaConfig (§1)
    state.py       AuctionState, TeamState, purchases — frozen
    rules.py       max_bid(), can_raise(), required_left()
    engine.py      pure state machine: (state, action) -> state
    policies/      valuation, aggression, budget curve
    sim.py         run N auctions, collect price distributions
```

`engine.py` should be a pure state machine driven by an action enum mirroring FantaLab's
`update_type` (`first_call`, `raise`, `fold`, `assign`, `close_auction`, `update_turn`,
`teams_to_skip`). Mirroring their vocabulary keeps the simulator honest.

### What this invalidates in the current code

- **`models.Role` (P/D/C/A) and `VALID_FORMATIONS` (7 Classic tuples) are the wrong model.**
  `strategy.pick_starting_lineup` cannot field a Mantra XI, and it is not a small patch —
  it needs the schema-matching engine above.
- **`strategy.allocate_auction_budget` has the wrong shape.** Its `{P, D, C, A} → %` split
  has no Mantra equivalent. FantaLab's Mantra planner splits **TIT. 80% / RIS. 20%** —
  starters vs bench — which is a different question entirely. Either adopt that axis, or
  split across the 12 Mantra roles; do not keep four buckets.

The Mantra data is already in the repo and is the right shape: `data/quotazioni_mantra.csv`
carries `ruoli_codice` as `;`-separated uppercase codes (`M;C`, `DS;E`, `W;A`), plus
`qi`/`qa`/`fvm`. `mantra_schemi.json` and `mantra_compat.json` are the legality inputs.

CLI surface:

```
fantabot asta-sim --runs 500 --seed 42          # Monte Carlo, dump price distribution
fantabot asta-sim --interactive                 # play our seat against 7 bots
fantabot asta-plan                              # target roster + budget curve
```

---

## 5. Calibration

### Our target prices are on the wrong scale

`data/target_price_2026_27_mantra.csv` holds 523 players with median target **5**, p90 **12**,
max **34**. Sum of the top 208 (the slots our league is obliged to fill) is **2167**.

But our league has **4000 credits**. Teams historically leave ~7 % unspent, so roughly
**3720 credits actually chase those 208 slots**.

```
inflation ≈ 3720 / 2167 ≈ 1.7
```

**The target-price column is a quotazione-scale number and must be multiplied by ~1.7 before
it can be used as a bid ceiling in our league.** Using it raw would have us walking away from
almost every contested player. Recompute this factor from the real `min_player` once the
league settles its roster limits.

### Observed Mantra clearing prices (live, 8 teams, 500 credits)

| Player | Role | Price |
|---|---|---|
| Malen | `Pc` | **192** |
| Martinez L. | `Pc` | **191** |
| Yildiz | `A` | 51 |
| Maignan | `Por` | 41 |
| De Bruyne (10-team, 500) | `T` | 32 |

Two top strikers at ~190 out of 500 is **38 % of a budget on one player**, twice over, in the
opening minutes. Whatever aggression distribution the bots get, it has to produce this.

### Multi-role is worth money

**44 % of the Mantra listone holds more than one role** (`M;C` 76 players, `C;T` 36,
`DS;E` 35, `W;A` 27, `DD;E` 24, and a handful of triples). A player who fits two schema slots
raises the number of formations the roster can legally field, which is the thing `§3` says we
should be scoring. Price that in explicitly — it is a Mantra-only source of edge and it is
invisible to anyone valuing players on fantamedia alone.

### Price model

Store valuations **per-mille of budget** and convert at the edges, the way FantaLab does
(`02 §1`):

```python
def credits(fvm_per_mille: int, num_credits: int) -> int:
    return fvm_per_mille * num_credits // 1000
```

Layer on:

1. **Baseline** — `quotazione_mantra` / `fvm_mantra`, rescaled by the inflation factor above.
2. **Scarcity, 12-dimensional.** Mantra's Recap Asta tracks unsold players by tier **per
   Mantra role** — 12 buckets, not 4. `Dc` running dry is a different event from `Pc`
   running dry, and both matter more than in Classic because no quota forces anyone to buy
   either.
3. **Schema pressure.** How many of my 11 schemi are still fieldable if I skip this player?
   This is the Mantra-specific replacement for "I still owe two defenders".
4. **Reactive reprice** — recompute from credits still in the league and slots still to fill:
   `inflation = credits_remaining / slots_remaining / mean_price`.
5. **News sentiment** — `fantabot news-fetch` and `mantra.drift()`, applied to titolarità.
   `drift()` earns its keep here specifically: the platform freezes Mantra role tags in late
   July and never revisits them, so a player's listed `ruoli_codice` may no longer describe
   how they are used — and in Mantra a wrong role tag is a wrong *slot*, not just a wrong
   projection.

---

## 6. Fidelity checklist

- [ ] Call turn rotates round-robin and advances on a cancelled call.
- [ ] Caller opens at 1 and is the initial high bidder.
- [ ] First-call clock `counter_time_first`; every accepted raise resets to `counter_time`.
- [ ] Raise steps are +1 / +5 / +10 plus arbitrary manual amounts.
- [ ] A team cannot outbid itself.
- [ ] `max_bid` reserves against the **minimum** roster, not the maximum.
- [ ] `max_bid` **unlocks to the full balance** once the minimum is met.
- [ ] Bidding on a role-full player returns 0 when role limits are in force.
- [ ] A team with 1 credit and 1 obligation left can still complete its minimum.
- [ ] Rosters are checked against `mantra_schemi.json` — a roster that cannot field any
      schema is reported as broken, even though FantaLab would have allowed it.
- [ ] **No auction rule is hard-coded.** Every field in `§9`'s `AstaConfig` can be changed
      without touching engine code, and a run with non-default values behaves accordingly.
- [ ] **Config drift is detected.** Feed the copilot a stream whose real timer differs from
      the configured one and confirm it says so instead of silently miscounting.

---

## 7. Open questions

1. ~~Raise mode~~ — ✅ **libera.** Poker branch not needed.
2. ~~Defence modifier~~ — ✅ **no** (D. Factor off).
3. **Roster limits** — ⏳ **ask first.** Which of the two Mantra modes, and what min/max?
4. **`asta_mode`** — ⏳ leaning random.
5. **Imbattibilità Portiere** — ⏳ never asked.
6. **Timers** — ⏳ Thursday.

   *(3–6 are runtime config — `§9`. Sweep them, don't wait for them.)*
7. **`ESPORTA IN FANTALEGHE`** — untested; it writes to the real league. Confirm overwrite
   semantics and permissions *before* auction day.
8. **`mantra_compat.json` is thin** — it lists blocked pairs for 4-1-4-1 only, from a 2024-25
   PDF. Any lineup-legality scoring leans on it. Verify before trusting it.
9. **Listone reconciliation** — 519 (FantaLab) vs 523 (ours). Diff by player id.
10. **PMA is premium.** Rebuild the equivalent from `Osserva Aste` — **45 live Mantra
    auctions** were listed during the walk-through, which is a usable sample.

---

## 8. Test asta, Thursday 2026-08-27 — measurement checklist

Fix the settings first (⚙ → tab `Asta`): agreed timers, `Modalità di rilancio = Libera`, and
turn **`Asta Visibile In "Osserva Aste"` OFF** for a private test.

- [ ] **Timers** — note both values; confirm a raise really resets the clock to the second.
- [ ] **The max-bid cap, and its release.** Buy up to one player short of the minimum and
      read `$MAX`. Then buy one more — crossing the minimum — and read it again. It should
      jump to the full balance. This is the single most important thing to verify: it is a
      Mantra-only behaviour and the whole endgame model depends on it.
- [ ] **Role-group counters.** Watch `P Min` / `Mov Min` decrement as you buy, and confirm
      `$MAX` tracks them.
- [ ] **Multi-role display.** Confirm the room shows all of a player's `mantra_roles`, so we
      know we can read them live rather than joining offline.
- [ ] **Contested-player price.** Fight one striker to the top. Note how many raises land
      inside a 10 s window — that calibrates bot aggression better than any guess.
- [ ] **Cancel + undo.** Cancel an in-flight call; delete a completed purchase; confirm the
      refund is full and turn order stays sane.
- [ ] **Random mode**, if you test it — confirm whether a random draw still rotates a call
      turn or removes the concept entirely.

---

## 9. Runtime configuration — the tool must run *any* Mantra asta

**Scope statement.** This is not a simulator for our league. It is a simulator for **an
arbitrary FantaLab Mantra auction**, and our league is one saved profile. That framing is not
ambition, it is the cheapest way to stay unblocked: half our settings are undecided, some land
on auction night, and there will be a riparazione in January and another season after that.

The hard rule that follows: **any value a league can change is a parameter.** A rule of the
auction written into a branch is a defect.

### The full configuration surface

Everything FantaLab lets a Mantra league vary, plus our own knobs. Defaults are FantaLab's.

```python
@dataclass(frozen=True)
class AstaConfig:
    # --- league ------------------------------------------------------------
    asta_type: Literal["mantra", "classic"]      = "mantra"
    num_teams: int                                = 8
    num_credits: int                              = 500
    credits_by_team: dict[str, int] | None        = None   # asymmetric budgets are legal
    players_list: Literal["serie-a", "euroleghe"] = "serie-a"
    season: str                                   = "2026/27"

    # --- roster ------------------------------------------------------------
    number_of_players_selection: Literal[
        "no-limit-per-role", "min-max-goalie-others",
        "static", "min-max-per-role",            # Classic only; keep for the other profile
    ] = "no-limit-per-role"
    min_player: int = 25
    max_player: int = 30
    min_goalkeepers: int | None = None           # min-max-goalie-others
    max_goalkeepers: int | None = None
    min_others: int | None = None
    max_others: int | None = None
    players_settings_data: dict[str, int] | None = None    # Classic quotas
    max_purchases_per_player: int = 1

    # --- auction mechanics -------------------------------------------------
    asta_mode: Literal["chiamata","random","alphabetic","draft","draft_pack"] = "chiamata"
    raise_mode: Literal["free", "ordered"] = "free"
    call_at_quotaz: bool = False
    counter_time_first: int = 20
    counter_time: int = 10
    raise_steps: tuple[int, ...] = (1, 5, 10)
    allow_manual_raise: bool = True
    blind_mode: bool = False                     # "Asta Ninja"
    admin_confirms_purchase: bool = False
    teams_to_skip: frozenset[str] = frozenset()  # mutable mid-auction

    # --- scoring rules (change valuation, not the auction) -----------------
    d_factor: bool = False
    imb_portiere: bool | None = None             # must be asked
    allowed_schemi: frozenset[str] | None = None # None = all 11
    out_of_position: Literal["forbidden","malus","free"] = "malus"

    # --- data --------------------------------------------------------------
    price_source: Literal["quotazione","fvm","ours","pma"] = "ours"
    price_rescale: float | None = None           # None = derive from league credits
    tier_names: tuple[str, ...] = ("Top","Semi-Top","Terza","Quarta","Scomm.","Riserve")
    role_overrides: dict[int, tuple[str, ...]] = field(default_factory=dict)  # "Cambio Ruolo"
    schemi_path: Path = Path("data/mantra_schemi.json")
    compat_path: Path = Path("data/mantra_compat.json")
    listone_path: Path = Path("data/quotazioni_mantra.csv")

    # --- our strategy (not a league rule, still a parameter) ---------------
    budget_split: tuple[float, float] = (0.80, 0.20)   # titolari / panchina
    risk_profile: str = "balanced"
```

Two fields deserve a note because they are easy to miss and both are real:

- **`credits_by_team`** — the admin panel exposes a per-team credit override. Asymmetric
  budgets are a legal league configuration, not an edge case.
- **`role_overrides`** — "Cambio Ruolo" lets the admin reassign a player's Mantra roles **for
  that auction only**. A tool that reads roles solely from the listone will be wrong about
  exactly the players a league cared enough to reclassify.

### Resolution order

```
CLI flags  →  auction discovery  →  saved profile  →  interactive wizard  →  defaults
```

`--no-input` turns the wizard step into a hard failure, which is what sweeps and CI want.
Profiles are per league (`--profile mantra`, `--profile classic`) so two rule sets never mix.

### Discovery: read the config off the auction *[S] — highest leverage item here*

FantaLab's spectator view already publishes most of what the wizard would ask. Given an
auction URL, derive rather than prompt:

| Field | Where it is visible |
|---|---|
| `num_credits`, `num_teams` | Regolamento Lega strip; team cards |
| `credits_by_team` | team cards |
| `d_factor`, `imb_portiere` | Regolamento Lega toggles |
| `min_goalkeepers`, `min_others` | team cards — `P Min` / `Mov Min` |
| `max_player` | team cards — the `n/31` denominator |
| `asta_mode` | the `/aste-live` listing (`Chiamata` / `Random` / `Alphabetic`) |
| `admin_confirms_purchase` | the *"Aspetta che l'admin confermi l'acquisto"* state |

**Not asking beats asking well.** Every field discovered is a field nobody can typo.

### Inference: learn the rest by watching

Settings the room does not label can be measured from the stream itself:

```
counter_time         = Δt between two consecutive raises that reset the clock
counter_time_first   = Δt from first_call to the first raise deadline
call_at_quotaz       = (opening price of a first call) != 1
required_left(team)  = credits_left − max_bid_shown + 1     # invert 01 §5
cap_released(team)   = max_bid_shown == credits_left
turn_order           = observe two full rotations
```

`required_left` is the useful one: it recovers a league's **minimum roster** from two numbers
the board prints anyway, without anyone being asked.

**And it must keep checking.** The copilot continuously reconciles declared config against
observed behaviour and says so when they diverge — *"configured counter_time=10, measuring
15"*. A setting mis-entered at 20:30 poisons every number until midnight, and without this
check nobody notices.

### Degrade, don't guess

An unknown field must not stop the tool and must not be silently invented. Report an
**interval** plus the question that would collapse it:

```
  walk-away price: 34–41   (uncertain: min_player unknown)
  → answering it narrows to ±2
```

That keeps the uncertainty in front of the person deciding instead of hiding inside a
confident-looking number.

### Sweep the unknowns

```
fantabot asta-sim --sweep min_player=25..30 --runs 200
fantabot asta-sim --sweep asta_mode=random,chiamata --runs 200
fantabot asta-sim --sweep counter_time=5,10,20 --runs 200
```

```
  min_player      25     26     27     28     30
  1st Pc price   188    191    190    186    179
  schemi ok      7.2    7.1    7.0    6.8    6.4
```

Where the outcome barely moves, the question did not matter. Where it moves, we know **which
question to push first**, with a number instead of a hunch. Thursday's test asta then stops
being a prerequisite and becomes what it should be: a way to narrow a range on something that
already runs.

### Validation

Reject, naming the inconsistency:

- `min ≤ max` in every band
- `min_goalkeepers + min_others == min_player`, and the max counterpart ≥ `max_player`
- `num_teams × min_player ≤ len(listone)`
- **the minimum roster must be able to field ≥ 1 of the allowed schemi** — `1 Por + 10 Mov`
  is arithmetically valid and footballistically impossible
- `counter_time ≥ 1` and `counter_time_first ≥ counter_time`
- `max_purchases_per_player ≥ 1`
- every `role_overrides` code is one of the 12

Warn without blocking when `call_at_quotaz` is on: it deletes the 1–3 credit tail, which is
most of the roster.

### Mutable mid-auction

The admin can change timers, skip lists and settings while the room is live (`01 §1`). Config
edits must apply **without restart** — state preserved, derived numbers recomputed. A copilot
that must be relaunched mid-asta will not be relaunched; it will be abandoned.

### What is *not* configurable

The FantaLab engine rules, which no league can alter and which therefore get written once:
you cannot outbid yourself; the cap reserves against the roster minimum and releases when it
is met; the caller holds the opening bid; every accepted raise resets the clock. Those are the
platform, not the league.
