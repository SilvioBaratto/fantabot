"""A roster player's upcoming match, read off the lineup DTO's own fields. Pure and total.

`lineUpInfo[i]` carries `teamH`, `teamA`, `hoaw`, `tid`, `tidOp` and `tname`. The one rule that
matters is that **an unresolvable row is absent, never guessed at**: the function sits on the
hourly job's default path (the planner calls it for every roster row, whatever the setting), so
it must also never raise.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from _paths import GOLDEN

from fantabot.domain.lineup.pairing import Pairing, pairing_from_row

#: Torino's Perri in the captured DTO: Torino at home to Udinese.
HOME_ROW: dict[str, Any] = {
    "pid": 6534, "tid": 18, "tname": "Torino", "tidOp": 19,
    "teamH": "TOR", "teamA": "UDI", "hoaw": 0,
}
AWAY_ROW: dict[str, Any] = {
    "pid": 1, "tid": 19, "tname": "Udinese", "tidOp": 18,
    "teamH": "TOR", "teamA": "UDI", "hoaw": 1,
}


def _row(**over: Any) -> dict[str, Any]:
    return {**HOME_ROW, **over}


class TestAResolvedRow:
    def test_a_home_player_faces_the_away_side(self) -> None:
        assert pairing_from_row(HOME_ROW) == Pairing(own="TOR", opponent="UDI")

    def test_an_away_player_faces_the_home_side(self) -> None:
        assert pairing_from_row(AWAY_ROW) == Pairing(own="UDI", opponent="TOR")

    def test_codes_are_normalised_so_they_join_the_quotazioni_vocabulary(self) -> None:
        assert pairing_from_row(_row(teamH=" tor ", teamA="udi")) == Pairing("TOR", "UDI")

    def test_the_own_club_is_the_dto_s_and_not_a_lookup_elsewhere(self) -> None:
        """A transfer is invisible to a table keyed on the preseason club; the row says it."""
        moved = _row(tname="Udinese", teamH="TOR", teamA="UDI", hoaw=1)
        assert pairing_from_row(moved) == Pairing("UDI", "TOR")


class TestAnUnresolvableRowIsAbsent:
    @pytest.mark.parametrize(
        "over",
        [
            {"teamH": ""},
            {"teamA": ""},
            {"teamH": None},
            {"teamA": None},
            {"tname": ""},
            {"tname": None},
            {"tname": "Milan"},  # neither end of TOR-UDI: not this player's match
            {"hoaw": 1},  # says away, but tname is the home side
            {"hoaw": None},
            {"hoaw": 2},
            {"hoaw": "0"},
            {"tidOp": 0},
            {"tidOp": None},
            {"teamH": "TORINO", "teamA": "UDI"},  # not a three-letter code
            # The own club resolves, so only the code's length keeps the opponent from being
            # read as a club: a four-letter or two-letter code is malformed, not "unknown".
            {"teamA": "UDIN"},
            {"teamA": "UD"},
            {"teamH": "TORI", "teamA": "UDI", "tname": "Udinese", "hoaw": 1},
            {"teamH": "TO", "teamA": "UDI", "tname": "Udinese", "hoaw": 1},
            {"teamH": "T0R"},  # a digit is not a letter
            {"teamH": "UDI", "teamA": "UDI", "tname": "Udinese", "hoaw": 0},  # plays itself
            # `hoaw` says away, so a fall-through that guessed the own club to be the away end
            # would accept these: only the own-club check refuses them.
            {"tname": "Milan", "hoaw": 1},
            {"tname": "", "hoaw": 1},
            {"tidOp": True},  # `True == 1`, a real-looking id that is a JSON boolean
            # The character class is checked on a code that nothing else cross-checks: the
            # opponent's is taken as given, so a malformed one would otherwise become a club.
            {"teamA": "U1I"},
            {"teamA": "ÀÀÀ"},  # three letters, and not ASCII
            # And the own end, with a `tname` whose first three characters spell the same
            # malformed code, so the own club resolves to it and matches the end it came from.
            {"tname": "T0rino", "teamH": "T0R"},
            {"tname": "ÀÀÀnimo", "teamH": "ÀÀÀ"},
        ],
        ids=lambda over: ",".join(f"{k}={v!r}" for k, v in over.items()),
    )
    def test_it_returns_none(self, over: dict[str, Any]) -> None:
        assert pairing_from_row(_row(**over)) is None

    @pytest.mark.parametrize(
        ("row", "flag"), [(HOME_ROW, False), (AWAY_ROW, True)], ids=["home-False", "away-True"]
    )
    def test_a_boolean_hoaw_is_not_the_integer_it_compares_equal_to(
        self, row: dict[str, Any], flag: bool
    ) -> None:
        """`False == 0` and `True == 1`, so an equality check alone would read a JSON boolean
        as the venue it happens to equal; only the type check refuses it."""
        assert pairing_from_row(row) is not None
        assert pairing_from_row({**row, "hoaw": flag}) is None

    @pytest.mark.parametrize("missing", ["teamH", "teamA", "tname", "hoaw", "tidOp"])
    def test_a_missing_key_returns_none(self, missing: str) -> None:
        row = dict(HOME_ROW)
        del row[missing]
        assert pairing_from_row(row) is None

    def test_an_empty_row_returns_none(self) -> None:
        assert pairing_from_row({}) is None

    @pytest.mark.parametrize("junk", [None, 0, 3.5, True, [], {}, (1, 2), object(), b"TOR"])
    def test_no_value_in_any_field_makes_it_raise(self, junk: Any) -> None:
        for key in HOME_ROW:
            pairing_from_row(_row(**{key: junk}))  # must not raise; the result is not the point


class TestTheCapturedDto:
    def _rows(self) -> list[dict[str, Any]]:
        body = json.loads((GOLDEN / "lineup" / "teamLineup_read.json").read_text())
        return list(body["lineUpInfo"])

    def test_every_one_of_the_thirty_rows_resolves(self) -> None:
        rows = self._rows()
        assert len(rows) == 30
        assert all(pairing_from_row(r) is not None for r in rows)

    def test_the_pairings_are_a_set_of_disjoint_matches(self) -> None:
        pairs = {
            frozenset((p.own, p.opponent))
            for r in self._rows()
            if (p := pairing_from_row(r)) is not None
        }
        clubs = [club for pair in pairs for club in pair]
        assert len(clubs) == len(set(clubs)), "a club appears in two matches of one round"

    def test_both_ends_of_a_match_agree_on_who_faces_whom(self) -> None:
        by_own = {
            p.own: p.opponent for r in self._rows() if (p := pairing_from_row(r)) is not None
        }
        for own, opponent in by_own.items():
            if opponent in by_own:
                assert by_own[opponent] == own
