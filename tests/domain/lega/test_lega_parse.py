"""`domain/lega/parse` — the translation from abbreviated JSON to named records.

Pure tests: dicts in, dataclasses out, no network and no database. The cases that carry
their reason are the ones that cost something to learn — the `cal`/`cs` pairing, the
`"-"` result, and the role map that was an open question in `docs/leghe-api.md` until it
was measured.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from fantabot.domain.lega.parse import (
    CLASSIC_ROLE_CODES,
    MARLE_TO_CODE,
    LeaguePayloadError,
    classic_role_code,
    parse_competitions,
    parse_custom_roles,
    parse_fixtures,
    parse_league_state,
    parse_matchday_start,
    parse_pool,
    parse_team_roster,
    parse_team_rosters,
    role_code,
)

TEAM = {
    "id": 10000003,
    "idu": 20000003,
    "n": "Team C",
    "nu": "Owner C",
    "d": "A",
    "cri": 500,
    "crs": 474,
    "cr": 26,
    "cal": "7071;6966;6827",
    "cs": "72;47;3",
}


def test_roster_pairs_ids_with_costs_positionally() -> None:
    team = parse_team_roster(4103937, TEAM)
    assert [(s.player_id, s.cost) for s in team.roster] == [(7071, 72), (6966, 47), (6827, 3)]
    assert team.division == "A"
    assert team.credits_spent == 474


def test_roster_refuses_a_length_mismatch() -> None:
    """`cal` and `cs` are two parallel strings with nothing but position tying them.

    Zipping the shorter one would attribute one player's price to another and the row
    would still look plausible — 29 players, credits that nearly add up. So it raises.
    """
    with pytest.raises(LeaguePayloadError, match="3 roster ids but 2 costs"):
        parse_team_roster(4103937, {**TEAM, "cs": "72;47"})


def test_roster_accepts_an_empty_rosa() -> None:
    """Before the asta every `cal` is empty. That is a state, not a fault."""
    team = parse_team_roster(4103937, {**TEAM, "cal": "", "cs": ""})
    assert team.roster == ()


def test_rosters_reads_a_page() -> None:
    teams = parse_team_rosters(4103937, {"data": [TEAM, {**TEAM, "id": 1}]})
    assert [t.team_id for t in teams] == [10000003, 1]


def test_league_state_joins_three_endpoints() -> None:
    state = parse_league_state(
        4103937,
        {"sId": 21, "mday": 3, "mstr": "2026-09-04T18:45:00", "activ": True, "sto": False},
        {"budg": 500, "xsltc": 32, "sroles": 2, "minrl": [2, 23], "maxrl": [4, 28]},
        {"mods": ["3412", "442"], "tbench": 12, "lcap": 3},
    )
    assert state.matchday == 3
    assert state.matchday_start == datetime(2026, 9, 4, 18, 45)
    assert state.budget == 500
    assert state.min_roles == (2, 23) and state.max_roles == (4, 28)
    assert state.modules == ("3412", "442")
    assert state.bench_size == 12


def test_league_state_survives_a_missing_settings_read() -> None:
    """One endpoint failing must not lose the other two: `lega sync` reports the failure
    and writes what it has, so the parser has to accept the empty mappings it passes."""
    state = parse_league_state(4103937, {"mday": 3}, {}, {})
    assert state.matchday == 3
    assert state.budget is None and state.modules == ()


@pytest.mark.parametrize("raw", ["", None, "not a date", 5])
def test_matchday_start_of_an_unusable_value_is_none(raw: object) -> None:
    assert parse_matchday_start(raw) is None


def test_matchday_start_stays_naive() -> None:
    """The platform sends Europe/Rome wall time with no zone. Stamping UTC on it here
    would move kickoff by two hours in summer."""
    assert parse_matchday_start("2026-09-04T18:45:00").tzinfo is None


def test_fixtures_flatten_rounds_and_carry_both_matchdays() -> None:
    fixtures = parse_fixtures(
        311681,
        [
            {
                "matchDay": 1,
                "championshipMatchDay": 3,
                "calculated": False,
                "matches": [
                    {"tIdH": 1, "tIdA": 2, "ptH": 0.0, "ptA": 0.0, "result": "-", "resultSR": ""}
                ],
            },
            {
                "matchDay": 2,
                "championshipMatchDay": 4,
                "calculated": True,
                "matches": [
                    {
                        "tIdH": 2, "tIdA": 1, "ptH": 78.5, "ptA": 66.0,
                        "standingPtH": 3, "standingPtA": 0, "result": "2-0", "resultSR": "1-0",
                    }
                ],
            },
        ],
    )
    assert len(fixtures) == 2
    assert (fixtures[0].matchday, fixtures[0].championship_matchday) == (1, 3)
    assert fixtures[1].calculated and fixtures[1].points_home == 78.5
    assert fixtures[1].result == "2-0"


def test_an_unplayed_result_is_none_not_a_dash() -> None:
    """`"-"` and `""` are both the platform's way of writing "not played". Storing them
    verbatim would make `where result is not null` return every fixture of the season."""
    fixtures = parse_fixtures(
        1, [{"matchDay": 1, "matches": [{"tIdH": 1, "tIdA": 2, "result": "-", "resultSR": ""}]}]
    )
    assert fixtures[0].result is None and fixtures[0].real_result is None


def test_competitions_keep_the_deleted_flag() -> None:
    comps = parse_competitions(
        4103937,
        [{"id": 311681, "name": "Fanta 26-27", "type": 1, "sDay": 3, "eDay": 38,
          "tmids": [1, 2], "del": False},
         {"id": 177318, "name": "old", "del": True}],
    )
    assert [c.competition_id for c in comps] == [311681, 177318]
    assert comps[0].team_ids == (1, 2)
    assert comps[1].deleted is True


def test_custom_roles_use_the_classic_scale_not_marle() -> None:
    """The two endpoints send integers that look alike and mean different things.

    `marle`'s 3 is nothing; `custom-roles`' 3 is Classic C. Reading the override through
    `MARLE_TO_CODE` turned Dimarco's D -> C into a Mantra `DC`, which is a real code and
    the wrong one — the kind of mistake that never raises.
    """
    roles = parse_custom_roles(
        4103937,
        [{"id": 254, "name": "Dimarco", "team": "Inter", "originalRole": 2, "role": 3}],
    )
    assert roles[0].player_id == 254
    assert (roles[0].original_role, roles[0].role) == ("D", "C")


def test_the_two_role_scales_do_not_share_a_map() -> None:
    """Guards the confusion directly: 3 is `C` as a Classic macro role and unmapped as a
    `marle` code, and no integer may resolve to the same string through both."""
    assert classic_role_code(3) == "C"
    assert role_code(3) == "3"
    assert not set(CLASSIC_ROLE_CODES).intersection(MARLE_TO_CODE)


def test_pool_reads_the_players_key_not_a_bare_list() -> None:
    """The body is an object with a `players` key. Code written against a top-level list
    gets `KeyError: 0`, which is what happened the first time it was called."""
    pool = parse_pool(
        4103937,
        {"players": [
            {"id": 5585, "name": "Malen", "stnme": "ROM", "quotd": 1,
             "fvmfc": 207, "fvmma": 210, "marle": [16]}
        ]},
    )
    assert pool[0].player_id == 5585
    assert pool[0].ruoli_codice == ("PC",)
    assert (pool[0].fvm_classic, pool[0].fvm_mantra) == (207, 210)


def test_pool_of_a_body_without_players_is_empty() -> None:
    assert parse_pool(1, {}) == ()


def test_the_role_map_covers_the_twelve_mantra_codes() -> None:
    """Twelve integers, twelve codes, each appearing once. The map was derived by joining
    588 pool entries against the `quotazioni` Mantra listone; every integer resolved to
    exactly one letter with no runner-up. `17` and `18` were never observed and are
    absent rather than invented."""
    assert len(MARLE_TO_CODE) == 12
    assert len(set(MARLE_TO_CODE.values())) == 12
    assert MARLE_TO_CODE[6] == "POR" and MARLE_TO_CODE[19] == "B"


def test_an_unknown_role_integer_is_kept_verbatim() -> None:
    """Dropping it would make a 30-man rosa silently 29. A code we cannot name is still
    a fact, and it shows up in the table as the integer it was."""
    assert role_code(17) == "17"
