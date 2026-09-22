"""The lega's own scoring: per-appearance fantavoto and the goal ladder. **Literal data only.**

Every number here was read, not chosen. The payload is `GET settings/calculate` for lega
4103937 on 2026-09-22; the fixtures are its 12 calculated matches in `league_fixture`
(competition 311681, matchdays 1-3); the rows are `match_grain` appearances whose
`fantavoto_fc` the A8 formula reproduces exactly (12,653 of 12,686 in 2025/26).
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from fantabot.domain.lineup.scoring import (
    MVP_RATE_BEFORE_2024_25,
    Appearance,
    ScoringRules,
    goals,
    lega_fantavoto,
)

#: `GET /onboarding/v1/league/settings/calculate`, lega 4103937, 2026-09-22, verbatim.
CALCULATE = {
    "bnMls": {
        "bmasf": [1, 1], "bmasg": [1, 1], "bmass": [1, 1], "bmcg": [0, 0], "bmcsh": 1,
        "bmdg": [1, 1], "bmeg": [0, 0], "bmgc": [-1, -1], "bmgs": [3, 3], "bmog": [-2, -2],
        "bmpns": [-3, -3], "bmpsa": [3, 3], "bmpsc": [3, 3], "bmrc": [-1, -1],
        "bmyc": [-0.5, -0.5], "bmycsv": 0, "motm": [1, 1],
    },
    "count": 4,
    "skodm": None, "smodcp": None, "smodd": None, "smodf": None, "smodg": None,
    "smodl": None, "smodm": None, "smodp": None,
    "sourcev": 1,
    "stbdf": {"sdgoal": 0, "sdpnt": 60},
    "step": {
        "starni": 0, "starnil": False, "stchkd": 0, "stchkdl": False,
        "stf1": [25, 18, 12, 10, 8, 6, 4, 3, 2, 1],
        "stgoal": [6, 12, 18, 24, 30, 36, 42], "stlmt": 66, "stog": 0,
    },
    "subst": {"ssdfg": 2, "ssdfm": 4, "ssdfn": 1, "ssdft": False, "ssnum": 5, "sstype": 5},
    "version": "v3",
}

RULES = ScoringRules.from_settings(CALCULATE["bnMls"], CALCULATE["step"])

#: Every calculated match of lega 4103937 by 2026-09-22: (home, away, the platform's result).
CALCULATED = [
    (82.0, 73.0, "3-2"), (68.5, 65.5, "1-0"), (82.5, 77.0, "3-2"), (66.5, 72.5, "1-2"),
    (73.0, 80.0, "2-3"), (74.0, 68.0, "2-1"), (77.0, 70.5, "2-1"), (64.0, 68.5, "0-1"),
    (88.0, 77.5, "4-2"), (72.0, 64.5, "2-0"), (76.0, 61.0, "2-0"), (65.5, 75.0, "0-2"),
]


# -- the ladder -------------------------------------------------------------------------


def _ladder(points: float) -> int:
    return goals(points, threshold=RULES.threshold, steps=RULES.steps)


def test_the_ladder_reproduces_every_calculated_result() -> None:
    assert len(CALCULATED) == 12
    assert [f"{_ladder(h)}-{_ladder(a)}" for h, a, _ in CALCULATED] == [
        result for _, _, result in CALCULATED
    ]


@pytest.mark.parametrize(
    ("points", "expected"),
    [(65.5, 0), (66.0, 1), (71.5, 1), (72.0, 2), (78.0, 3), (108.0, 8), (200.0, 8)],
)
def test_the_ladder_at_its_boundaries(points: float, expected: int) -> None:
    """Scores move in 0.5 steps, so exactly 66.0 and 72.0 are common — and the 24 real
    scores contain 72.0 but not 66.0, so the threshold's own edge needs its own case.
    Past the last step (66 + 42) the ladder stops adding goals."""
    assert _ladder(points) == expected


# -- the rules --------------------------------------------------------------------------


def test_the_rules_parse_from_today_s_payload() -> None:
    assert (RULES.goal, RULES.assist, RULES.motm) == (3.0, 1.0, 1.0)
    assert (RULES.yellow, RULES.red, RULES.own_goal) == (-0.5, -1.0, -2.0)
    assert (RULES.penalty_scored, RULES.penalty_missed, RULES.penalty_saved) == (3.0, -3.0, 3.0)
    assert RULES.conceded == -1.0
    assert (RULES.clean_sheet, RULES.decisive_goal) == (1.0, 1.0)
    assert RULES.threshold == 66.0
    assert RULES.steps == (6.0, 12.0, 18.0, 24.0, 30.0, 36.0, 42.0)


def test_a_pair_whose_halves_differ_is_refused_not_guessed() -> None:
    """Every `bnMls` value is a pair and nobody has said what the two halves mean. While
    they agree it does not matter; the day they differ, picking one is a guess."""
    bn = {**CALCULATE["bnMls"], "bmgs": [3, 2]}
    with pytest.raises(ValueError, match="bmgs"):
        ScoringRules.from_settings(bn, CALCULATE["step"])


def test_assist_variants_that_disagree_are_refused() -> None:
    """Three assist fields, and `match_grain` has one `assist` column to weigh them by."""
    bn = {**CALCULATE["bnMls"], "bmasf": [2, 2]}
    with pytest.raises(ValueError, match="assist"):
        ScoringRules.from_settings(bn, CALCULATE["step"])


# -- the per-appearance fantavoto -------------------------------------------------------

#: The default fantacalcio.it weights `fantavoto_fc` is computed with: the lega's, less MVP
#: and less the clean sheet — neither is in `fantavoto_fc`.
FC_DEFAULT = replace(RULES, motm=0.0, clean_sheet=0.0)

#: (stagione, name, role, the appearance, fantavoto_fc) — literal `match_grain` rows.
ROWS = [
    ("2025/26", "Belotti", "A",  # a penalty scored, outside `gol_segnati`
     Appearance(voto_fc=7.5, gol_segnati=1, rigori_segnati=1, mvp=1), 13.5),
    ("2025/26", "Zapata D.", "A",  # a penalty missed
     Appearance(voto_fc=5.0, rigori_sbagliati=1), 2.0),
    ("2025/26", "Montipò", "P",  # a keeper: a goal conceded and a penalty saved
     Appearance(voto_fc=6.5, rigori_parati=1, gol_subiti=1), 8.5),
    ("2025/26", "Butez", "P",  # a clean sheet, which `fantavoto_fc` does not reward
     Appearance(voto_fc=6.0), 6.0),
    ("2025/26", "Paz N.", "C",  # a goal, an assist, a yellow
     Appearance(voto_fc=7.5, gol_segnati=1, assist=1, ammonizione=1, mvp=1), 11.0),
    ("2025/26", "Cambiaso", "D", Appearance(voto_fc=4.5, espulsione=1), 3.5),
    ("2025/26", "Hien", "D", Appearance(voto_fc=5.0, autoreti=1), 3.0),
    ("2024/25", "Altare", "D",  # the first season `mvp` was recorded: it counts there
     Appearance(voto_fc=6.5, mvp=1), 6.5),
    ("2023/24", "Almqvist", "A", Appearance(voto_fc=7.5, gol_segnati=1), 10.5),
]


@pytest.mark.parametrize(("season", "name", "role", "row", "fc"), ROWS, ids=[r[1] for r in ROWS])
def test_the_formula_reproduces_fantavoto_fc(
    season: str, name: str, role: str, row: Appearance, fc: float
) -> None:
    """A8, with fantacalcio.it's default weights: the check that the formula *is* theirs."""
    assert lega_fantavoto(row, rules=FC_DEFAULT, season=season, role=role) == fc


@pytest.mark.parametrize(
    ("season", "name", "role", "row", "fc"),
    [r for r in ROWS if r[0] >= "2024/25"],
    ids=[r[1] for r in ROWS if r[0] >= "2024/25"],
)
def test_the_lega_adds_its_mvp_bonus_where_one_was_recorded(
    season: str, name: str, role: str, row: Appearance, fc: float
) -> None:
    """`motm` +1 is this lega's, and `fantavoto_fc` has no MVP term at all; nor the
    keeper's clean sheet, which the lega pays +1."""
    clean_sheet = 1.0 if role == "P" and row.gol_subiti == 0 else 0.0
    assert lega_fantavoto(row, rules=RULES, season=season, role=role) == (
        fc + row.mvp + clean_sheet
    )


def test_before_2024_25_the_mvp_is_an_expected_rate_by_role() -> None:
    """`mvp` was not recorded before 2024/25: a zero there is "unknown", not "no". The
    expected rate keeps pre-2024/25 history on the same scale as the seasons after."""
    almqvist = Appearance(voto_fc=7.5, gol_segnati=1)

    score = lega_fantavoto(almqvist, rules=RULES, season="2023/24", role="A")

    assert score == pytest.approx(10.5 + MVP_RATE_BEFORE_2024_25["A"])
    assert MVP_RATE_BEFORE_2024_25 == {"A": 0.059, "P": 0.036, "C": 0.031, "D": 0.017}


def test_an_unknown_role_before_2024_25_is_refused() -> None:
    """A coach row (`ALL`) or a typo has no MVP rate, and 0 would be a guess."""
    with pytest.raises(ValueError, match="ALL"):
        lega_fantavoto(Appearance(voto_fc=6.0), rules=RULES, season="2023/24", role="ALL")


def test_a_keeper_who_concedes_nothing_takes_the_clean_sheet() -> None:
    """`bmcsh`, pinned by T12: +1 to every keeper with a vote and no goal conceded — 7 of 7
    in rounds 1-3, and never to an outfield player or a keeper who conceded."""
    butez = Appearance(voto_fc=6.0)

    assert lega_fantavoto(butez, rules=RULES, season="2025/26", role="P") == 7.0
    assert lega_fantavoto(butez, rules=RULES, season="2025/26", role="D") == 6.0
    conceded = Appearance(voto_fc=6.5, gol_subiti=1)
    assert lega_fantavoto(conceded, rules=RULES, season="2025/26", role="P") == 5.5


def test_the_decisive_goal_is_read_but_not_recomputed() -> None:
    """`bmdg`, pinned by T12: +1 once to a scorer whose goal decided the match. It is read
    into the rules because the platform's own lines count it, and it cannot be recomputed
    from `match_grain`, which has no column for it — so a history score is short of it."""
    scorer = Appearance(voto_fc=7.0, gol_segnati=1)

    assert RULES.decisive_goal == 1.0
    assert lega_fantavoto(scorer, rules=RULES, season="2025/26", role="A") == 10.0
