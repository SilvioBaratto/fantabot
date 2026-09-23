"""Which recorded rooms a backtest may replay. Pure — no database, no uuids, no clock.

The live measurement lives beside the data, in
`tests/adapters/persistence/test_backtest_corpus_db.py`. This file is the rule itself: the
three admissions, the order they are judged in, and the two things the fold must not do —
count rows instead of distinct ids, and consult a room's declared `max_player`.

Ids are **sparse and unsorted on purpose**. With contiguous ids CPython's small-int hashing
makes `tuple(a_set)` already ascending, so an implementation that forgot to sort would pass
every assertion here and produce a different replay on a real roster.
"""

from __future__ import annotations

import pytest

from fantabot.domain.lineup.backtest_corpus import (
    MAX_ROSTER,
    MIN_IDENTIFIED,
    RoomRow,
    SaleRow,
    admit,
    report,
)

#: Real-shaped fantacalcio ids, deliberately neither contiguous nor in order.
IDS = (6094, 574, 2891, 132, 4820, 9001, 77, 15234, 388, 6001, 231, 7712, 1450, 8803,
       319, 5560, 2044, 11901, 646, 3377, 928, 7105, 1688, 4412, 266, 9930, 1177, 6650,
       3901, 812, 5215, 1399, 7488)


def _room(asta_id: str, *, teams: int | None = 2, credits: int | None = 500,
          max_player: int | None = None) -> RoomRow:
    return RoomRow(asta_id, teams, credits, max_player)


def _sales(asta_id: str, buyers: int, size: int) -> list[SaleRow]:
    """`buyers` rosters of `size` distinct ids each, one disjoint id block per buyer."""
    return _uneven(asta_id, [size] * buyers)


def _uneven(asta_id: str, sizes: list[int]) -> list[SaleRow]:
    """One roster per entry in `sizes`, so a short buyer is spelled out rather than sliced."""
    return [
        SaleRow(asta_id, f"buyer{b}", IDS[i % len(IDS)] + 100_000 * b)
        for b, size in enumerate(sizes)
        for i in range(size)
    ]


class TestWhatIsAdmitted:
    def test_a_complete_room_is_admitted_with_one_roster_per_buyer(self) -> None:
        corpus = admit([_room("r1")], _sales("r1", 2, 4), min_identified=3, max_roster=5)

        assert [r.asta_id for r in corpus.rooms] == ["r1"]
        assert corpus.roster_count == 2
        assert corpus.rejected == ()

    def test_the_rosters_are_ordered_by_buyer_and_their_ids_ascending(self) -> None:
        """T32 replays 38 giornate per rosa; an unordered set is a different replay."""
        corpus = admit([_room("r1")], _sales("r1", 2, 4), min_identified=3, max_roster=5)
        first, second = corpus.rooms[0].rosters

        assert (first.buyer_team_id, second.buyer_team_id) == ("buyer0", "buyer1")
        assert first.player_ids == (132, 574, 2891, 6094)
        assert second.player_ids == (100_132, 100_574, 102_891, 106_094)

    def test_a_size_is_distinct_ids_and_not_rows(self) -> None:
        """One buyer, the same player listed four times, is a roster of one."""
        sales = [SaleRow("r1", "buyer0", 6094) for _ in range(4)]
        sales += [SaleRow("r1", "buyer1", 574 + i) for i in range(4)]

        corpus = admit([_room("r1")], sales, min_identified=3, max_roster=5)

        assert corpus.rooms == ()
        assert corpus.rejected_for("TOO_FEW_IDS") == ("r1",)

    def test_the_read_order_is_the_replay_order(self) -> None:
        rooms = [_room("rB"), _room("rA")]
        sales = _sales("rA", 2, 4) + _sales("rB", 2, 4)

        corpus = admit(rooms, sales, min_identified=3, max_roster=5)

        assert [r.asta_id for r in corpus.rooms] == ["rB", "rA"]


class TestWhatIsRejected:
    def test_a_room_that_states_no_shape_is_refused(self) -> None:
        corpus = admit([_room("r1", teams=None)], _sales("r1", 2, 4), min_identified=3)

        assert corpus.rejected_for("NO_SHAPE") == ("r1",)

    @pytest.mark.parametrize("teams", [0, None])
    def test_zero_teams_is_not_a_declaration_either(self, teams: int | None) -> None:
        """FantaLab sends 0 for an unset field; taken literally it is a `0x0` cell."""
        corpus = admit([_room("r1", teams=teams)], _sales("r1", 2, 4), min_identified=3)

        assert corpus.rejected_for("NO_SHAPE") == ("r1",)

    @pytest.mark.parametrize("credits", [0, None])
    def test_zero_credits_is_not_a_declaration_either(self, credits: int | None) -> None:
        corpus = admit([_room("r1", credits=credits)], _sales("r1", 2, 4), min_identified=3)

        assert corpus.rejected_for("NO_SHAPE") == ("r1",)

    def test_a_room_missing_a_buyer_is_refused(self) -> None:
        corpus = admit([_room("r1", teams=3)], _sales("r1", 2, 4), min_identified=3)

        assert corpus.rejected_for("BUYER_COUNT") == ("r1",)
        assert "2 buyers, room declares 3" in corpus.rejected[0].detail

    def test_a_room_with_no_sales_at_all_is_refused_for_its_buyers(self) -> None:
        corpus = admit([_room("r1")], [], min_identified=3)

        assert corpus.rejected_for("BUYER_COUNT") == ("r1",)

    def test_a_buyer_short_of_the_minimum_refuses_the_whole_room(self) -> None:
        corpus = admit(
            [_room("r1")], _uneven("r1", [4, 2]), min_identified=3, max_roster=5
        )

        assert corpus.rejected_for("TOO_FEW_IDS") == ("r1",)

    def test_a_roster_above_the_ceiling_refuses_the_whole_room(self) -> None:
        corpus = admit([_room("r1")], _sales("r1", 2, 6), min_identified=3, max_roster=5)

        assert corpus.rejected_for("ROSTER_TOO_LARGE") == ("r1",)
        assert "largest roster 6 > 5" in corpus.rejected[0].detail


class TestTheDeclaredMaximumIsIgnored:
    def test_a_declared_maximum_below_the_rosters_never_rejects_a_room(self) -> None:
        """Measured 2026-09-23: 7 of the 17 admitted Mantra rooms declare `max_player = 25`
        and all 7 hold rosters above it. 32 is the platform's `xsltc`; 25 is what the room's
        organiser typed, and judging by it throws away 7 of 17."""
        corpus = admit(
            [_room("r1", max_player=3)], _sales("r1", 2, 4), min_identified=3, max_roster=5
        )

        assert [r.asta_id for r in corpus.rooms] == ["r1"]
        assert corpus.rooms[0].declared_max_player == 3

    def test_the_declaration_is_still_carried(self) -> None:
        corpus = admit(
            [_room("r1", max_player=25)], _sales("r1", 2, 4), min_identified=3, max_roster=5
        )

        assert corpus.rooms[0].declared_max_player == 25


class TestTheRuleOrder:
    """Only the breakdown depends on the order — a room fails every rule it fails — but the
    breakdown is what an operator reads, and a report naming the wrong rule sends them to
    the wrong place. Measured on the live corpus: this order gives
    `NO_SHAPE 3 / BUYER_COUNT 30 / TOO_FEW_IDS 156 / ROSTER_TOO_LARGE 4`; putting the id
    count before the buyer count gives `3 / 1 / 185 / 4`."""

    def test_no_shape_outranks_every_other_failure(self) -> None:
        corpus = admit([_room("r1", teams=None)], _sales("r1", 1, 1), min_identified=3)

        assert corpus.rejected[0].reason == "NO_SHAPE"

    def test_the_buyer_count_outranks_a_short_roster(self) -> None:
        """The reorder that turns 30/156 into 1/185. Both rules fail here."""
        corpus = admit([_room("r1", teams=3)], _sales("r1", 2, 1), min_identified=3)

        assert corpus.rejected[0].reason == "BUYER_COUNT"

    def test_the_buyer_count_outranks_an_oversized_roster(self) -> None:
        corpus = admit(
            [_room("r1", teams=3)], _sales("r1", 2, 6), min_identified=3, max_roster=5
        )

        assert corpus.rejected[0].reason == "BUYER_COUNT"

    def test_a_short_roster_outranks_an_oversized_one(self) -> None:
        corpus = admit(
            [_room("r1")], _uneven("r1", [1, 6]), min_identified=3, max_roster=5
        )

        assert corpus.rejected[0].reason == "TOO_FEW_IDS"


class TestTheShapeBreakdown:
    def test_shapes_are_counted_separately(self) -> None:
        rooms = [_room("a"), _room("b"), _room("c", credits=300)]
        sales = _sales("a", 2, 4) + _sales("b", 2, 4) + _sales("c", 2, 4)

        counts = admit(rooms, sales, min_identified=3, max_roster=5).by_shape()

        assert [(c.shape, c.rooms, c.rosters) for c in counts] == [
            ("2x500", 2, 4), ("2x300", 1, 2)
        ]

    def test_a_tie_in_room_count_is_broken_by_the_shape(self) -> None:
        """Without a tiebreak the report reorders itself between runs over one dict."""
        rooms = [_room("a", credits=900), _room("b", credits=300), _room("c", credits=600)]
        sales = _sales("a", 2, 4) + _sales("b", 2, 4) + _sales("c", 2, 4)

        counts = admit(rooms, sales, min_identified=3, max_roster=5).by_shape()

        assert [c.shape for c in counts] == ["2x300", "2x600", "2x900"]

    def test_the_report_names_the_admitted_and_every_refusal(self) -> None:
        rooms = [_room("a"), _room("b", teams=3), _room("c", teams=None)]
        sales = _sales("a", 2, 4) + _sales("b", 2, 4) + _sales("c", 2, 4)

        lines = report(admit(rooms, sales, min_identified=3, max_roster=5))

        assert lines[0] == "admitted 1 room(s), 2 roster(s)"
        assert "  2x500: 1 room(s), 2 roster(s)" in lines
        assert "  rejected BUYER_COUNT: 1" in lines
        assert "  rejected NO_SHAPE: 1" in lines


class TestTheShippedThresholds:
    def test_the_defaults_are_the_specs_own_numbers(self) -> None:
        assert (MIN_IDENTIFIED, MAX_ROSTER) == (23, 32)

    def test_a_bare_call_uses_the_specs_own_numbers_and_not_a_tests(self) -> None:
        """Every case above overrides both thresholds; a caller that passes none must still
        get SPEC A16(1), and the boundary is where that is visible."""
        short = admit([_room("r1")], _sales("r1", 2, MIN_IDENTIFIED - 1))
        exact = admit([_room("r1")], _sales("r1", 2, MIN_IDENTIFIED))
        over = admit([_room("r1")], _sales("r1", 2, MAX_ROSTER + 1))
        ceiling = admit([_room("r1")], _sales("r1", 2, MAX_ROSTER))

        assert short.rejected_for("TOO_FEW_IDS") == ("r1",)
        assert [r.asta_id for r in exact.rooms] == ["r1"]
        assert over.rejected_for("ROSTER_TOO_LARGE") == ("r1",)
        assert [r.asta_id for r in ceiling.rooms] == ["r1"]
