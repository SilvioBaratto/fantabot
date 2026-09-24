# Mantra `starts[]` slot order — T01 findings (phase `lineup-theory`)

Captured 2026-09-21. This is the provenance table that CP0 reviews and that T02 pins into
`src/fantabot/data/mantra_starts_order.json`.

## Verdict

**The bundle has all 11 modules, so every module's provenance is `bundle`. No module is
`p3`.** Open Question 5 does not arise.

- The P3 reading of the PDF pitch diagrams matches the bundle at all 11 × 11 positions.
  P3 reads each module line from the team's left to right; the diagrams have the
  goalkeeper at the top and Ds on the viewer's right.
- The two alternatives P3 could not rule out on its own, the per-line mirror and the
  row-by-row reading, are both contradicted by the bundle.
- **11 of 11 modules** are sent today in an order that differs from the platform's.
  442 and 3412 were accepted only because those particular XIs happened to fit.

## Source

- **What was fetched:** `GET https://leghe.fantacalcio.it/resources/`, then
  `resources/main-BWAQ2MMm.js`, then 272 `chunk-*` files, crawled until the set of chunk
  names stopped growing.
  - `/resources/` returns a 404 that still ships the SPA shell. The site root `/` is a
    landing page and does not.
  - Every request was an unauthenticated static GET: no cookie jar, no `Authorization`
    header, no token read, no POST.
- **The table is in `resources/chunk-Dc2l8Fqx.js`:**
  - 19,672 bytes, sha256 `421ca5952970224ead95c4953786b83241941e9d64d09ff3bcbf021941698d71`;
  - fetched 2026-09-21 15:52 CEST;
  - a copy is kept at `docs/sources/leghe-chunk-Dc2l8Fqx.js`, because content-hashed
    chunk names disappear on the next deploy.
- **Why this table is the positional order**, traced through the lineup page chunk
  (`chunk-Dn0VNEyz.js`) to the POST body:
  - The page builds `formationRoles = Ae[code].split(" ")`, where
    `Ae = Object.fromEntries(S.schemes.mantra.map(e => [digits(e.label), e.roles]))`.
  - `unplaceableStarterSlots` checks `canBePlaced(starters[r], formationRoles[r])` for
    r = 0..10.
  - The POST body is `Cp(e) = {starts: e.starters, …}`.
  - So `starts[i]` is judged against `formationRoles[i]`.
  - `getMantraSchemeRole(i, label)` returns `roles.split(" ")[i]` from the same table.

Verbatim literal (`S.schemes.mantra`):

```
mantra:[{id:0,label:"3-4-3",roles:"Por Dc/B Dc Dc E C M/C E W/A A/Pc W/A"},{id:1,label:"3-4-1-2",roles:"Por Dc/B Dc Dc E C M/C E T A/Pc A/Pc"},{id:2,label:"3-4-2-1",roles:"Por Dc/B Dc Dc E M/C M E/W T/A T A/Pc"},{id:3,label:"3-5-2",roles:"Por Dc/B Dc Dc E C M M/C E/W A/Pc A/Pc"},{id:4,label:"4-4-2",roles:"Por Ds Dc Dc Dd E C M/C E/W A/Pc A/Pc"},{id:5,label:"4-3-3",roles:"Por Ds Dc Dc Dd C M M/C W/A A/Pc W/A"},{id:6,label:"4-3-1-2",roles:"Por Ds Dc Dc Dd C M M/C T A/Pc T/A/Pc"},{id:7,label:"3-5-1-1",roles:"Por Dc/B Dc Dc E/W M C M E/W T/A A/Pc"},{id:8,label:"4-1-4-1",roles:"Por Ds Dc Dc Dd M W T C/T E/W A/Pc"},{id:9,label:"4-4-1-1",roles:"Por Ds Dc Dc Dd E/W C M E/W T/A A/Pc"},{id:10,label:"4-2-3-1",roles:"Por Ds Dc Dc Dd M/C M W/A T T/W A/Pc"}]
```

**Corroboration:** the same chunk carries the platform's malus matrix:
- `he[slot][ge[role]]`, with `c=0` meaning ok, `s=1` `MANTRA_MALUS` and `r=-1`
  `ROLE_ERROR`;
- plus row exceptions for 4-1-4-1 (`getMantraRowExceptions`).

Mapping `ok→0`, `-1→1` and `no|-1*→-1`, it agrees with `mantra_compat.json` in **1,452 of
1,452 cells**, the 4-1-4-1 exceptions included. The PDF transcription and the platform's
own engine are the same table.

## Provenance table

Position 0 is the goalkeeper. "Today" is `GK + mantra_schemi.json` slots, which is the PDF
table's row order and what the builder lays into `starts[]`. Every row is a permutation of
today's slots.

| Module | Bundle order (provenance `bundle`) | P3 | Mirror | Row-by-row | Today differs at |
|---|---|---|---|---|---|
| 343  | Por Dc/B Dc Dc E C M/C E W/A A/Pc W/A     | = | 1,3,5,6 | 5,6,7,9,10 | 1,3,5,6 |
| 3412 | Por Dc/B Dc Dc E C M/C E T A/Pc A/Pc      | = | 1,3,5,6 | 5,6,7 | 1,3,5,6 |
| 3421 | Por Dc/B Dc Dc E M/C M E/W T/A T A/Pc     | = | 1,3,4–9 | 5,6 | 1,3,5,6,8,9 |
| 352  | Por Dc/B Dc Dc E C M M/C E/W A/Pc A/Pc    | = | 1,3,4,5,7,8 | 5,6,7 | 1,3,5,6,7 |
| 3511 | Por Dc/B Dc Dc E/W M C M E/W T/A A/Pc     | = | 1,3 | 4,7 | 1,3,6,7 |
| 433  | Por Ds Dc Dc Dd C M M/C W/A A/Pc W/A      | = | 1,4,5,7 | 9,10 | 5,6,7 |
| 4312 | Por Ds Dc Dc Dd C M M/C T A/Pc T/A/Pc     | = | 1,4,5,7,9,10 | = | 5,6,7,9,10 |
| 442  | Por Ds Dc Dc Dd E C M/C E/W A/Pc A/Pc     | = | 1,4,5,6,7,8 | 6,7 | 6,7 |
| 4141 | Por Ds Dc Dc Dd M W T C/T E/W A/Pc        | = | 1,4,6,7,8,9 | 6,7,8 | 6,8,9 |
| 4411 | Por Ds Dc Dc Dd E/W C M E/W T/A A/Pc      | = | 1,4,6,7 | 5,6,7 | 6,7 |
| 4231 | Por Ds Dc Dc Dd M/C M W/A T T/W A/Pc      | = | 1,4,5,6,7,9 | = | 5,6,7,9 |

`=` means equal as role sets at every position; 4231's `T/W` is the diagram's `W/T`.

## What today's order risks, per swapped position

Each row takes a single-role player whose role is natural to *our* slot i and judges him
at the *platform's* slot i. A multi-role player takes the minimum over his roles, as
`getMinRoleMalus` does.

- **ERROR → LUP009:**
  - 3421: [3] B, [6] C, [9] A;
  - 352: [3] B, [6] C;
  - 3511: [3] B, [7] C;
  - 343 and 3412: [3] B;
  - 433 and 4312: [6] C;
  - 4411: [7] C;
  - 4141: [6] T, [8] W;
  - 4231: [6] C, [9] A.
- **MALUS −1, accepted silently:**
  - 343, 3412 and 433: [5] M;
  - 352 and 4312: [5] M;
  - 3511 and 4411: [6] M;
  - **442: [6] M, its only risk, so 442 can never refuse but can malus**;
  - 4312: [9] T;
  - 4141: [6] C, [8] E;
  - 4231: [7] T.

**Checked against the live record:** the 3412 submitted hourly since 09:51 today,
Mandas … Gonzalez N., has **0** malus cells under the bundle order:
- Calhanoglu `{M,C}` sits at C;
- Kessiè `{C}` sits at M/C;
- Zè Pedro `{DD,DC}` sits at Dc.

The earlier 442 (runs 34–36) put Kessiè `{C}` at index 6, which is the platform's C, so it
was clean too. Roles were read from the `league_player_pool` capture of 12:45. Every bot
submit so far is for matchday 4, which has not been played, so no calculated round can hold
a bot-caused malus.

**The risk stays live until T08.** An hourly re-plan that puts an M-only player at
3412's index 5 is accepted with −1 and nothing reports it.

## Flags for the operator (CP0)

1. **The B slot has two tables.** The chunk also holds `le`, the lineup *assistant's*
   default schemes. It is identical except that position 1 of the five 3-back modules is
   `Dc`, not `Dc/B`. The page validates against `S.schemes.mantra`; the server's copy is
   not visible.
   - Treating position 1 as `Dc` would be the robust choice. It costs something only if
     the XI would include a B-only player, who then fits no 3-back slot.
   - **Decided 2026-09-21 (operator): pin `Dc/B`**, the table the page validates
     against. The roster has no B player, so the choice is free today, and if the server
     used `Dc` a B-only player at position 1 would be refused (LUP009), never given a
     silent −1.
2. **T03's planned 343 test assumed a `p3` 343.** "A pinned-order 343 XI with an M-only
   player in M/C is refused because the mirror puts him at −1" relies on a mirror
   alternative, and with `bundle` provenance there are no alternatives. The guard-can-refuse
   test needs a different case, for example:
   - an XI laid out in *today's* order, checked against the bundle order;
   - the 442 silent-malus case above.
3. **Robust slot sets and `alternatives` are unnecessary** for this data: every
   `alternatives` list is empty. **Decided 2026-09-21 (operator): dropped** (SPEC A22),
   along with robust slot sets.

## Reproduce

The throwaway scripts were in the session scratchpad (`crawl.py`, `analyze.py`). The
steps:
1. Crawl from the `/resources/` 404 shell.
2. Regex `\{id:(\d+),label:"([\d-]+)",roles:"([^"]+)"\}` over the chunk. The first
   11 matches are `S.schemes.mantra` and the next 11 are `le`.
3. Parse `ge={…}`, `he={…}` (up to `,Ae=`) and the 4-1-4-1 exception object for the
   matrix check.

## Evidence table (T03, 2026-09-21)

**How it was rebuilt:**
- One pass of read-only calls, the same the hourly job makes: `my_team`, `competitions`,
  `teamLineup_read`, `lineup_settings`, `roster_settings`.
- Run from the live directory with the worktree's interpreter and `FANTABOT_AUTO_ACT=false`,
  with `teamLineup_submit` replaced by a tripwire.
- The roster and values are today's. The XIs are those the **old** builder lays out in
  `mantra_schemi.json`'s order, each judged at the platform's own positions by
  `domain/lineup/positional.py`.
- **Exact, not approximate.** The rebuilt walk reproduces the 17:52 run's attempt order
  exactly: 3421, 4231, 4141, 3511, 352, 4312, 433 refused, then 3412 accepted.

**The old order, as the live job sends it:**

| Module (walk order) | Live result | Refused cells (`no`/`-1*`) | `-1` cells |
|---|---|---|---|
| 3421 | refused (LUP009) | [6] M: C no | — |
| 4231 | refused (LUP009) | [6] M: C no | — |
| 4141 | refused (LUP009) | [6] W: A no | [8] C/T: E -1 |
| 3511 | refused (LUP009) | [7] M: C no | — |
| 352 | refused (LUP009) | [6] M: C no | — |
| 4312 | refused (LUP009) | [6] M: C no | — |
| 433 | refused (LUP009) | [6] M: C no | — |
| 3412 | **accepted** | — | — |
| 343 | not reached | — | — |
| 442 | not reached | — | — |
| 4411 | not reached | [7] M: C no | — |

- **7 of 7 refusals are explained**, each by a named `no` cell. In six of them it is a
  C-only player in the platform's pure-M slot.
- **The accepted one is predicted clean.** 343 and 442 would also have passed; 4411 would
  have been refused.
- **Under the new order, all 11 modules have 0 refused and 0 `-1` cells.**

**Accepted and saved lineups:**
- Accepted 442 (runs 34–36) and 3412 (runs 37–43), judged as sent: 0 refused, 0 `-1`.
- The lineup the platform holds now (`teamLineup_read`, 3412, ldate `20260921155208846`):
  0 refused, 0 `-1`.
- **The UI-saved 3412 of 2026-09-02** (`docs/leghe-api.md`, the operator's own save):
  0 refused, and exactly one `-1`, Tavares (Ds/E) in the T slot.
  - The platform accepted it, which confirms that a `-1` goes through at submission.
  - It is pinned by `test_the_ui_saved_3412_has_exactly_one_malus_and_nothing_refused`.
