"""My scoring against the platform's own, player by player. **Whole responses, no network.**

`fixtures/lineup_reconcile/` holds the platform's match detail for all 12 calculated
matches of lega 4103937 (rounds 1-3, Serie A 3-5), exactly as `apileague.match_detail`
returned them on 2026-09-22, the `settings/calculate` payload of the same day, and the
`match_grain` rows of Serie A giornata 3 for every player who took a vote in round 1.

What this settles, and how:

* **The per-player scores are not in `lply`**, which stays null on a calculated round.
  They are the `starts[]`/`bench[]` lines: `scr` the vote, `b` sixteen event counts,
  `cscr` the platform's fantavoto, `m` the positional malus, `ptype` the substitution.
* **What each of `b`'s positions counts** was solved from 454 voted lines, and every one
  of them is reproduced exactly by the lega's own weights — the first test below.
* **`bmcsh` is a keeper's clean sheet, `bmdg` a decisive goal, and `sourcev: 1` is
  `voto_fc`**: round 1 against `match_grain`, where 147 of 153 players match exactly and
  each of the other six is named.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

import pytest
from _paths import FIXTURES

from fantabot.domain.lineup.scoring import (
    EVENT_WEIGHTS,
    Appearance,
    PlatformLine,
    ScoringRules,
    lega_fantavoto,
    malus_starters,
    platform_fantavoto,
)

HERE = FIXTURES / "lineup_reconcile"
OURS = 18780035

CALCULATE = json.loads((HERE / "calculate_4103937.json").read_text(encoding="utf-8"))
RULES = ScoringRules.from_settings(CALCULATE["bnMls"], CALCULATE["step"])
MATCHES = [
    json.loads(path.read_text(encoding="utf-8"))
    for path in sorted(HERE.glob("311681_*.json"))
]
MATCH_GRAIN_G3 = {
    row["player_id"]: row
    for row in json.loads((HERE / "match_grain_2026-27_g3.json").read_text(encoding="utf-8"))
}


def _lines(rounds: tuple[int, ...] = (1, 2, 3)) -> Iterator[tuple[dict[str, Any], PlatformLine]]:
    for match in MATCHES:
        if match["mday"] not in rounds:
            continue
        for side in ("home", "away"):
            for where in ("starts", "bench"):
                for raw in match[side][where]:
                    yield match, PlatformLine.parse(raw)


def test_the_fixture_is_every_calculated_match() -> None:
    assert len(MATCHES) == 12
    assert sorted({m["mday"] for m in MATCHES}) == [1, 2, 3]
    assert all(m["cal"] for m in MATCHES)
    assert all(m[side]["lply"] is None for m in MATCHES for side in ("home", "away")), (
        "`lply` is null even on a calculated round; the per-player lines are starts/bench"
    )


def test_every_voted_line_is_reproduced_by_the_lega_s_weights() -> None:
    voted = [line for _, line in _lines() if line.vote is not None]

    assert len(voted) == 454
    assert [
        (line.pid, line.score, platform_fantavoto(line, rules=RULES))
        for line in voted
        if abs(platform_fantavoto(line, rules=RULES) - (line.score or 0.0)) > 0.01
    ] == []


def test_a_line_with_no_vote_parses_as_none() -> None:
    """`scr` 55 or 56 and `cscr` 100: no vote, and nothing to count."""
    silent = [line for _, line in _lines() if line.vote is None]

    assert len(silent) == 98
    assert {line.score for line in silent} == {None}
    assert all(sum(line.events) == 0 for line in silent)


def test_the_vote_is_voto_fc() -> None:
    """`sourcev: 1`. `voto_stat` and `voto_italia` agree on 87 and 104 of these 153."""
    round_1 = [line for _, line in _lines((1,)) if line.vote is not None]

    assert len(round_1) == 153
    assert [line.pid for line in round_1 if line.vote != MATCH_GRAIN_G3[line.pid]["voto_fc"]] == []


def test_round_1_against_match_grain_every_mismatch_is_named() -> None:
    """The lega's formula over `match_grain`'s columns, against the platform's `cscr`.

    Two things `match_grain` cannot know: a **decisive goal** (`bmdg`, `b[8]`) is not a
    column, and the **positional malus** (`m`) belongs to the lineup, not the player. The
    residual must be exactly those two, for every player; anything else is unexplained.
    """
    named: dict[str, list[int]] = {"bmdg": [], "malus": []}
    unexplained = []
    for _, line in _lines((1,)):
        if line.vote is None:
            continue
        row = MATCH_GRAIN_G3[line.pid]
        mine = lega_fantavoto(
            Appearance(**{k: v for k, v in row.items() if k in Appearance.__slots__}),
            rules=RULES,
            season="2026/27",
            role=row["ruolo_codice"],
        )
        residual = (line.score or 0.0) - mine
        explained = RULES.decisive_goal * line.events[8] - line.malus
        if abs(residual - explained) > 0.01:
            unexplained.append((line.pid, row["nome"], residual))
        if line.events[8]:
            named["bmdg"].append(line.pid)
        if line.malus:
            named["malus"].append(line.pid)

    assert unexplained == []
    assert (len(named["bmdg"]), len(named["malus"])) == (5, 1)


def test_every_position_that_occurred_has_a_weight() -> None:
    seen = {i for _, line in _lines() for i, count in enumerate(line.events) if count}

    assert seen <= set(EVENT_WEIGHTS)


def test_an_unmeasured_position_is_refused_not_scored_as_zero() -> None:
    """Positions 7, 9 and 11 never fired in 552 lines; the own goal (-2) is one of them.
    Scoring one as 0 would hide exactly the event whose weight is not known."""
    line = PlatformLine.parse(
        {"pid": 1, "scr": 6, "cscr": 4, "m": 0, "ptype": "-",
         "b": "0;0;0;0;0;0;0;1;0;0;0;0;0;0;0;0"}
    )
    with pytest.raises(ValueError, match="7"):
        platform_fantavoto(line, rules=RULES)


class TestTheMalusCheck:
    """SPEC A18's check, on the platform's own flag: `m` weighs exactly -1 in all 454
    voted lines, so a starter with `m` set is a starter who took the positional malus."""

    def test_it_finds_every_flagged_starter_and_nothing_else(self) -> None:
        flagged = {
            (match["mday"], match[side]["tid"], pid)
            for match in MATCHES
            for side in ("home", "away")
            for pid in malus_starters(match[side])
        }

        assert flagged == {(1, 18774379, 6495), (2, 18774379, 4502), (3, 19184924, 6727)}

    def test_our_rounds_before_the_fix_took_none(self) -> None:
        """Rounds 1-3 predate T08. Not the acceptance check — that is round 4, the first
        submitted in the platform's order — but the baseline it is compared with."""
        ours = [
            (match["mday"], malus_starters(match[side]))
            for match in MATCHES
            for side in ("home", "away")
            if match[side]["tid"] == OURS
        ]

        assert len(ours) == 3
        assert all(pids == () for _, pids in ours)
