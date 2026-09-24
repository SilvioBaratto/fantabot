# 02 — Data model: players, prices, fasce, strategy

What FantaLab knows about a player, and how it turns that into a number you bid.

---

## 1. The four different "prices"

Getting these straight matters more than anything else in this document, because three of
them are called *prezzo* somewhere in the UI.

| Name | Field | What it is |
|---|---|---|
| **Quotazione** | `quotazione` / `quotazione_mantra` | The official Fantacalcio.it list price. Static-ish, published with the listone. Kean 2026/27 = **25**. **[observed]** |
| **FVM** (Fanta Valore di Mercato) | `fvm_classic` / `fvm_mantra` | FantaLab's own valuation, stored **in per-mille of the total budget**. |
| **PMA / Prezzo Medio Aste** | — | The *actual* mean price paid for that player across FantaLab auctions. Premium-gated, and shown both as a number, as a % of budget (`PMA 8.5%`), and as a **time series** ("Andamento Prezzo Medio Aste", ~3 weeks of daily points). |
| **Prezzo impostato** | strategy | *Your* target price from your own strategy/fasce. Drives the red "over budget" warning during bidding. |

### FVM → credits

The conversion is budget-relative — **[code]**, seen in both roster valuation and the
Magic Asta pick logic:

```
credits = Math.floor(fvm_classic / 1000 * num_credits)
```

So FVM is scale-free: the same player is worth `fvm/2` credits in a 500-credit league and
`fvm` credits in a 1000-credit league. **This is the right internal representation for our
simulator's price model** — store per-mille, multiply at the edges.

A team's "market value" is just `Σ floor(fvm/1000 · num_credits)` over its roster, which is
what the in-room *Analisi Rosa* scores against what the team actually paid.

---

## 2. Player record

Fields surfaced in the listone table and the in-auction player search **[observed]**:

**Identity** — `player_id`, `name`, Serie A club, nationality, age, `role` (P/D/C/A),
**`mantra_roles[]`**, `svincolato` (free agent — struck through in the UI).

`mantra_roles` is the field that matters for us. It is a **list** — a player may hold
several codes (Tourè I. = `E`,`M`) — and the bundle also accepts a comma-separated string,
re-sorting it into the canonical order `Por · Dc · B · Ds · Dd · E · M · C · W · T · A · Pc`.
Multi-role players fit more schema slots, which is a form of value Classic has no analogue
for. See `01 §9` for the full role → line mapping.

**Prices** — `quotazione`, `quotazione_mantra`, `fvm_classic`, `fvm_mantra`, PMA (+ PMA %).

**Indices** — three bars shown on every player card:

| Index | Meaning |
|---|---|
| **Titolarità** | Likelihood of starting. Also given as a hard % (e.g. `95%`, `50%`, `1%`). |
| **Affidabilità** | Consistency of returns. |
| **Integrità** | Injury-proneness / availability. |

**Projection** — `FMV Exp.` ("Fanta Media Attesa"): algorithmic expected fantamedia,
premium-gated. Kean 7.38, Krstovic 7.21, Laurientè 6.91, Dovbyk 6.97. **[observed]**

**Season stats** — MV, FMV, MV/FMV last 5, FMV home/away/as-sub, Pres., Partite da
titolare, Gol, Assist, Rig., Amm., Esp., Inf. (missed through injury), minutes/match,
touches/match, pass accuracy, `% partite voto ≥ 6` and `≥ 6.5`, `% partite con gol/assist`,
bonus/match, bonus-malus, xG, xA, shots/match, shots on target, conversion %,
`% acquisto nelle leghe`.

---

## 3. Fasce (tiers)

Six tiers, per role. These are the atoms of every strategy in the app:

| # | Full | Short |
|---|---|---|
| 1 | **Top** | Top |
| 2 | **Semi-Top** | Semi-Top |
| 3 | **Terza** (fascia) | Terza |
| 4 | **Quarta** (fascia) | Quarta |
| 5 | **Scommesse** | Scomm. |
| 6 | **Riserve** — renamed **Evitare** in the Mantra rooms observed | Riserve |

The sixth tier's default label in the bundle is `Riserve` **[code]**, but leagues and
creators rename fasce freely (*"Modifica Nomi Fasce"*), and every live Mantra room seen
called it **Evitare**. Same slot, different name — do not model it as two things.

A player with no tier assigned reads *"Fascia non impostata"*. During the auction the
**Recap Asta** panel tracks how many players of each tier are still unsold — **per Mantra
role in Mantra** (all 12 tabs: `Por · Dc · B · Ds · Dd · E · M · C · W · T · A · Pc`), per
macro-role in Classic. That is the scarcity signal a bidding agent should be watching, and
in Mantra it is 12-dimensional rather than 4.

Tiers come from three places: your own hand-set fasce, an imported **creator strategy**
(~44 creators publish tier+price+notes+budget sets), or the built-in *Prezzi Medi Aste* /
*Prezzi Manager* presets.

---

## 4. Strategy & budget model (`Simula Asta`)

`/simulazione` is not a live-auction simulator — it is a **roster planner**. You declare,
per slot, which tier you intend to buy, and it prices and grades the resulting squad.

**The planner is structurally different in the two formats.** This is not cosmetic.

### Mantra — our format **[observed]**

The roster is laid out as **TITOLARI + PANCHINA**, not as four role buckets:

- **TITOLARI** — the 11 slots of a chosen schema, each typed by that schema, some accepting
  two roles. For 4-4-2: `Por · Ds · Dc · Dc · Dd · W/E · C · C/M · E · Pc/A · Pc/A`.
- **PANCHINA** — `Por`, `Por`, then N generic `Mov` slots, with *Aggiungi Por* /
  *Aggiungi Mov* to extend up to the roster maximum.

**Budget split is `TIT.` vs `RIS.`** — default **80% / 20%**. There is no per-role
percentage at all. The lever is *how much of the budget goes into the starting eleven*,
which is a completely different planning question from "how much to the attack".

Presets are a different, shorter set than Classic's: **Rosa Profonda · TOP 11 · Affidabile ·
Alto Rischio / Offensiva**.

**Valutazione Rosa** grades **five** departments plus four qualities:
Porta · Difesa · Centrocampo · **Trequartista** · Attacco, then Titolarità · Affidabilità ·
Integrità · Bonus.

The *Regolamento Lega* strip inside the planner shows the two Mantra-only toggles —
**D. Factor** and **Imb. Portiere** — alongside `P` and `MOV` roster minimums.

### Classic — for reference

**Budget split** is a percentage per role summing to 100. Default shown for a 3-4-3 was
`P 9% · D 11% · C 21% · A 59%`. Real rosters observed in a live 8-team league ran
`P 2–13% · D 7–22% · C 15–38% · A 37–63%`. **[observed]**

Once set, the budget is carried **into the auction room**: *"Quando i tuoi rilanci
supereranno il limite del budget da te impostato, FantaLab te lo colorerà di rosso."*

**Presets** (art assets name them — **[code]**): `equilibrata`, `difesaOltranza`,
`attaccoTotale`, `senzaTop`, `top11`, `affidabile`, `altoRischio`, `rosaProfonda`.
In the UI: Equilibrata · Difesa a Oltranza · Attacco Totale · Senza Top · TOP11 ·
Affidabile · Alto Rischio.

**Valutazione Rosa** — eight axes scored per squad: Porta · Difesa · Centrocampo · Attacco ·
Titolarità · Affidabilità · Integrità · Bonus.

**Prezzo reattivo** (premium): *"Parte dal prezzo che hai impostato nella tua strategia e lo
ricalcola in tempo reale in base a quanti crediti sono già stati spesi e quanti giocatori
restano."* — this is exactly the dynamic-reprice behaviour our CLI should implement, and it
is the single most valuable premium feature to reproduce locally.

---

## 5. In-room decision tools

Eight tabs in the auction room. Each is a candidate feature for the CLI:

| Tab | Content |
|---|---|
| **Rose Squadre** | Every team: credits, `$MAX`, `n/25`, remaining slots per role, budget % spent per role, and the full roster with the price paid for each player. |
| **Fasce Giocatori** | The full listone as a working table — owner, tier, your price/budget, PMA %, quotazione, the three index bars, FMV Exp., and season stats. Filterable by role and by creator. |
| **Recap Asta** | I tuoi crediti · Il tuo rilancio max · Giocatori comprati `23/25` · Giocatori chiamati `184/519` · Chip leader · slots to fill per role · **players of each tier still unsold, per role**. |
| **Guida all'Asta** | Per-club scouting: projected XI + formation, coach, rigoristi / punizioni / calci d'angolo, **ballottaggi with split percentages** (e.g. `Scamacca 51% – Krstovic 49%`), Valorizzati / Penalizzati / Nomi Nascosti / Giovani. Updated daily. |
| **Portieri** | Goalkeeper **pairing** — scores 2-GK and 3-GK club combinations `/100` across matchdays 1–38 (easy/medium/hard fixture split, home/away). |
| **Avversari** | Every opponent's optimal XI, side by side, with a formation optimiser. |
| **Simula Rosa/Budget** | The `Simula Asta` planner, embedded live. |
| **Analisi Asta** | **Curva dei prezzi** (paid vs PMA, ordered by role) + per-team Analisi Rosa (optimal XI, budget distribution, roster grades, tier counts) + **Più costosi / Affari / Strapagati** per role. |

---

## 6. Listone scope

The live league observed was at **`184/519` giocatori chiamati** — so the 2026/27 Serie A
listone in FantaLab is **519 players**. Our own `fantabot` pipeline has been running over
**523**; the gap is a snapshot-date difference (late-August listone churn), not a
disagreement about scope. Reconcile by player id, not by count.

Consequence for our league: **200 of 519 players get bought (38 %)**. 319 stay unsold. Any
simulator that models the endgame has to model the fact that a 1-credit filler is always
available.
