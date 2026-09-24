# FantaLab — asta reference

Field notes from a live walk-through of `app.fantalab.it` on **2026-08-25**, taken to
specify a CLI asta simulator for our league.

We play the real league on **leghe.fantacalcio.it**; FantaLab is the *tool* we drive the
asta with (it hosts the auction room, then exports the result back to Leghe Fantacalcio).
So FantaLab's engine is the thing our simulator has to imitate, and its data model is the
thing our pricing has to speak.

**Scope:** the CLI targets *any* FantaLab Mantra auction, not just ours — every rule a league
can vary is runtime configuration, discovered from the auction where possible and asked for
only when it must be. Our league is one saved profile. See
[`04 §9`](04-simulator-spec.md).

## Files

| File | What's in it |
|------|--------------|
| [`00-asta-e-requisiti-cli.md`](00-asta-e-requisiti-cli.md) | 🇮🇹 **In italiano, non tecnico.** How the asta actually unfolds start to finish, plus a requirements analysis for the CLI. **Read this one first** — the rest is reference. |
| [`01-auction-engine.md`](01-auction-engine.md) | The rules: call modes, raise modes, timers, bid steps, turn rotation, max-bid formula, state machine. |
| [`02-data-model.md`](02-data-model.md) | Players, `quotazione` vs `fvm` vs PMA, fasce, indices, strategy/budget model. |
| [`03-platform-map.md`](03-platform-map.md) | Every screen and tool, the REST/Firebase surface, Leghe Fantacalcio import/export. |
| [`04-simulator-spec.md`](04-simulator-spec.md) | What the CLI has to implement, with our league's numbers plugged in. |
| [`05-osserva-aste-harvest.md`](05-osserva-aste-harvest.md) | How we harvest **real prices paid** from spectator mode: the public Firebase node, the id bridge, the collector, and what one evening yielded. |
| [`06-asta-write-path.md`](06-asta-write-path.md) | The other half of `05`: **joining an asta by invite link and bidding**. Auth chain, the join calls, and the exact Firebase writes for call / raise / close / confirm / purchase — measured against a live room. |

## Our league (the target configuration)

**Mantra**, Serie A, 8 teams, 500 credits each. Roster limits still to be agreed — Mantra has
no P/D/C/A quotas at all, only a total-roster band or a goalkeepers-vs-outfield band
(see [`01 §6`](01-auction-engine.md) and [`01 §9`](01-auction-engine.md)).

| # | Team | Owner | Credits |
|---|------|-------|---------|
| 1 | Olympique Sartiglia | Francesco Totti | 500 |
| 2 | CSKA Monza | Luca Apollaro | 500 |
| 3 | **RealEspresso** | **neuroespresso (us)** | 500 |
| 4 | Tettenham | Lore | 500 |
| 5 | IPSWICH DOWN | Ruffini P | 500 |
| 6 | BERLUSCA DORTMUND | Filippo | 500 |
| 7 | Vericot | Il Fenomeno di Veruda | 500 |
| 8 | ho poca fantasia | Romano_Floriano | 500 |

**Regolamento, as confirmed by the group on 2026-08-25:**

| Setting | Status |
|---|---|
| Formato | ✅ **Mantra** |
| Modalità di rilancio | ✅ **Libera** — free-for-all against the clock |
| Modificatore di difesa (*D. Factor*) | ✅ **No** |
| **Limiti di rosa (min / max)** | ⏳ **the most important open question** — drives the max-bid cap |
| Modalità di chiamata | ⏳ undecided, leaning **random** (7 of 10 live Mantra auctions use it) |
| Imbattibilità Portiere | ⏳ never asked |
| Timer prima chiamata / ogni rilancio | ⏳ set in a test asta on **Thu 2026-08-27** |

Consequences are worked through in [`04-simulator-spec.md §1`](04-simulator-spec.md), and
the test asta has its own measurement checklist in `§8` of that file.

Three things to internalise before writing any bidding logic:

- **4 000 credits** chase ~**208 obliged slots** → mean price ≈ **19 credits/player**. The
  listone is ~519 players, so well over half goes unsold and a 1-credit filler is always
  available.
- **The max-bid cap reserves against the roster MINIMUM, and disappears once you meet it.**
  In Mantra `max > min`, so a team that has bought its obligatory players can put its entire
  remaining balance on one more. Classic has no equivalent.
- **Mantra enforces no role quotas during the auction.** You can finish with 3 goalkeepers
  and 23 attackers and the room will not object — it just leaves you unable to field a legal
  XI. Lineup legality is *our* problem, not the platform's.

## Confidence markers used throughout

- **[observed]** — seen happening in the UI during the walk-through.
- **[code]** — read out of the shipped JS bundle (`main.a84bc0e9.js` + route chunks).
- **[inferred]** — reasoned from the two above; flagged where it matters.

Anything not marked is UI copy quoted directly from the app.
