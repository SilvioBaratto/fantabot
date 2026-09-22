"""The lega's own scoring: what one appearance is worth, and how many goals a total makes.

`match_grain.fantavoto_fc` is fantacalcio.it's number, scored with *its* default bonuses.
This lega adds `motm` +1 for the man of the match, so its fantavoto is recomputed here
from the appearance's components and the lega's own `bnMls` rather than trusted. The
formula is A8's, which reproduces `fantavoto_fc` exactly on 99.7% of rows with the
default weights: vote, +3 a goal, +3 a penalty scored (counted *outside* `gol_segnati`),
+1 an assist, -0.5 a yellow, -1 a red, -2 an own goal, +3 a penalty saved, -3 a penalty
missed, -1 a goal conceded. There is no clean-sheet term in it.

**What is read and what is refused.** Every `bnMls` value is a pair `[x, y]` and nobody
has said what the halves mean; while they agree it cannot matter, and the day they
differ, choosing one is a guess, so `from_settings` refuses instead. The three assist
fields (`bmasf`, `bmass`, `bmasg`) must agree for the same reason: `match_grain` has one
`assist` column. `bmcg`, `bmeg` and `bmycsv` read 0 and are not applied. Nor are `stbdf`
or the null `smod*` modifiers, which this lega leaves off.

**Pinned against the platform (T12, 2026-09-22)**, over all 12 calculated matches of
rounds 1-3 (`tests/domain/lineup/test_scoring_reconcile.py`):

* the vote is **`voto_fc`** (`sourcev: 1`): equal for 153 of 153 players in round 1;
* **`bmcsh` is a keeper's clean sheet**, +1 to every keeper with a vote and no goal
  conceded (7 of 7, never anyone else). `lega_fantavoto` applies it;
* **`bmdg` is a decisive goal**, +1 once to a scorer whose goal decided the match. It is
  read, and `platform_fantavoto` counts it, but `lega_fantavoto` cannot: `match_grain`
  has no column for it, so a score recomputed from history is short by exactly that —
  5 players of 153 in round 1.

The platform's own per-player scores are not in `lply` (null even once calculated) but in
each side's `starts`/`bench` lines, read by `PlatformLine`.

**`mvp` exists only from 2024/25.** Before that, a 0 means "not recorded", not "no", and
reading it as "no" would put older seasons a fraction of a point below newer ones. They
get an expected per-role rate instead: MVPs per voted appearance over 2024/25 and 2025/26.

Pure: no I/O, no clock. The payload is fetched by `apileague.calculate_settings`.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

#: The first season `match_grain.mvp` was recorded.
MVP_SINCE = "2024/25"

#: MVPs per voted appearance, by Classic role, over 2024/25 and 2025/26 (A8; re-measured
#: 2026-09-22: 294/4969, 55/1540, 267/8748, 144/8555).
MVP_RATE_BEFORE_2024_25: Mapping[str, float] = {"A": 0.059, "P": 0.036, "C": 0.031, "D": 0.017}

_ASSISTS = ("bmasf", "bmass", "bmasg")


@dataclass(frozen=True, slots=True)
class Appearance:
    """One player's match, in `match_grain`'s own column names."""

    voto_fc: float
    gol_segnati: int = 0
    rigori_segnati: int = 0
    assist: int = 0
    ammonizione: int = 0
    espulsione: int = 0
    autoreti: int = 0
    rigori_parati: int = 0
    rigori_sbagliati: int = 0
    gol_subiti: int = 0
    mvp: int = 0


@dataclass(frozen=True, slots=True)
class ScoringRules:
    """A lega's weights per event, and its goal ladder."""

    goal: float
    assist: float
    yellow: float
    red: float
    own_goal: float
    penalty_scored: float
    penalty_missed: float
    penalty_saved: float
    conceded: float
    motm: float
    #: `bmcsh`: a keeper with a vote who conceded nothing.
    clean_sheet: float
    #: `bmdg`: once, to a scorer whose goal decided the match. Not in `match_grain`.
    decisive_goal: float
    #: `step.stlmt`: the total worth the first goal.
    threshold: float
    #: `step.stgoal`: each further goal, as points above `threshold`.
    steps: tuple[float, ...]

    @classmethod
    def from_settings(
        cls, bn_mls: Mapping[str, Any], step: Mapping[str, Any]
    ) -> ScoringRules:
        """Read `settings/calculate`'s `bnMls` and `step`. Raises `ValueError` on a pair
        whose halves differ, on assist fields that disagree, and on a missing field."""
        assists = {key: _weight(bn_mls, key) for key in _ASSISTS}
        if len(set(assists.values())) != 1:
            raise ValueError(
                f"the assist weights disagree ({assists}) and match_grain has one assist column"
            )
        return cls(
            goal=_weight(bn_mls, "bmgs"),
            assist=assists["bmass"],
            yellow=_weight(bn_mls, "bmyc"),
            red=_weight(bn_mls, "bmrc"),
            own_goal=_weight(bn_mls, "bmog"),
            penalty_scored=_weight(bn_mls, "bmpsc"),
            penalty_missed=_weight(bn_mls, "bmpns"),
            penalty_saved=_weight(bn_mls, "bmpsa"),
            conceded=_weight(bn_mls, "bmgc"),
            motm=_weight(bn_mls, "motm"),
            clean_sheet=_weight(bn_mls, "bmcsh"),
            decisive_goal=_weight(bn_mls, "bmdg"),
            threshold=float(step["stlmt"]),
            steps=tuple(float(s) for s in step["stgoal"]),
        )


def _weight(bn_mls: Mapping[str, Any], key: str) -> float:
    value = bn_mls[key]
    if isinstance(value, Sequence) and not isinstance(value, str):
        halves = {float(v) for v in value}
        if len(halves) != 1:
            raise ValueError(
                f"bnMls.{key} is {list(value)}: its halves differ, and which one applies is "
                "not known"
            )
        return halves.pop()
    return float(value)


def lega_fantavoto(
    appearance: Appearance, *, rules: ScoringRules, season: str, role: str
) -> float:
    """One appearance's fantavoto under `rules`, short of the decisive goal (see the module
    docstring). `role` is the Classic P/D/C/A letter: `P` takes the clean sheet, and before
    2024/25 it looks up the expected MVP rate — an unknown one raises there."""
    a = appearance
    score = (
        a.voto_fc
        + rules.goal * a.gol_segnati
        + rules.penalty_scored * a.rigori_segnati
        + rules.assist * a.assist
        + rules.yellow * a.ammonizione
        + rules.red * a.espulsione
        + rules.own_goal * a.autoreti
        + rules.penalty_saved * a.rigori_parati
        + rules.penalty_missed * a.rigori_sbagliati
        + rules.conceded * a.gol_subiti
    )
    if role == "P" and a.gol_subiti == 0:
        score += rules.clean_sheet
    if season >= MVP_SINCE:
        return score + rules.motm * a.mvp
    try:
        return score + rules.motm * MVP_RATE_BEFORE_2024_25[role]
    except KeyError:
        raise ValueError(f"no MVP rate for role {role!r} before {MVP_SINCE}") from None


#: What each of a platform line's sixteen `b` counts is worth, by `ScoringRules` field.
#: Solved from 454 voted lines of rounds 1-3 and exact on every one; 12, 13 and 14 are the
#: three assist kinds, and their sum is `match_grain.assist`.
EVENT_WEIGHTS: Mapping[int, str] = {
    0: "yellow",
    1: "red",
    2: "goal",
    3: "conceded",
    4: "penalty_saved",
    5: "penalty_missed",
    6: "penalty_scored",
    8: "decisive_goal",
    10: "clean_sheet",
    12: "assist",
    13: "assist",
    14: "assist",
    15: "motm",
}
#: How many counts a line carries.
EVENTS = 16
#: `scr` for a player with no vote (`cscr` is then 100). Two codes; the difference between
#: them is not established.
NO_VOTE = frozenset({55.0, 56.0})


@dataclass(frozen=True, slots=True)
class PlatformLine:
    """One player's line in a calculated match: the platform's own score, and its parts."""

    pid: int
    #: `scr`; `None` for no vote.
    vote: float | None
    #: `b`, the sixteen event counts. Positions 7, 9 and 11 never fired in rounds 1-3;
    #: the own goal is one of them.
    events: tuple[int, ...]
    #: `cscr`, the platform's fantavoto; `None` with no vote.
    score: float | None
    #: `m`: 1 when the player took the positional malus (-1) in the slot he was fielded in.
    malus: int
    #: `ptype`: `-`, `U` substituted out, `E` came on.
    sub: str

    @classmethod
    def parse(cls, raw: Mapping[str, Any]) -> PlatformLine:
        vote = None if float(raw["scr"]) in NO_VOTE else float(raw["scr"])
        return cls(
            pid=int(raw["pid"]),
            vote=vote,
            events=tuple(int(n) for n in str(raw["b"]).split(";")),
            score=None if vote is None else float(raw["cscr"]),
            malus=int(raw.get("m") or 0),
            sub=str(raw.get("ptype") or "-"),
        )


def platform_fantavoto(line: PlatformLine, *, rules: ScoringRules) -> float:
    """A line's fantavoto recomputed from its own parts: the vote, each count at its weight,
    less the malus. Raises on a line with no vote, and on a count at a position whose weight
    has never been measured — scoring it as 0 would hide exactly the event not understood.
    """
    if line.vote is None:
        raise ValueError(f"player {line.pid} has no vote")
    if len(line.events) != EVENTS:
        raise ValueError(f"player {line.pid}: {len(line.events)} counts, not {EVENTS}")
    unmeasured = [i for i, n in enumerate(line.events) if n and i not in EVENT_WEIGHTS]
    if unmeasured:
        raise ValueError(
            f"player {line.pid}: b position(s) {unmeasured} fired and their weight is unknown"
        )
    counted = sum(
        float(getattr(rules, EVENT_WEIGHTS[i])) * n for i, n in enumerate(line.events) if n
    )
    return line.vote + counted - line.malus


def malus_starters(side: Mapping[str, Any]) -> tuple[int, ...]:
    """The starters on one side of a calculated match who took the positional malus.

    SPEC A18's check, read off the platform's own flag rather than inferred from a score
    one point short: `m` weighs exactly -1 in every voted line of rounds 1-3.
    """
    return tuple(
        line.pid for line in map(PlatformLine.parse, side.get("starts") or ()) if line.malus
    )


def goals(points: float, *, threshold: float, steps: Sequence[float]) -> int:
    """Goals on the lega's ladder for a fantapunti total. Pure.

    `threshold` and `steps` are `settings/calculate.step.stlmt`/`stgoal`, read from the
    lega, never hard-coded: 66 and +6 is this lega's choice, not the platform's.
    """
    if points < threshold:
        return 0
    return 1 + sum(1 for step in steps if step <= points - threshold)
