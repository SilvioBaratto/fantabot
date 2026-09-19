"""T24: what a valid exclusion is, and what a row with no name means.

The pure half of `application/exclusions.py`. The I/O half round-trips in
`tests/integration/test_exclusions.py` (the `db` tier), and the two surfaces are
compared by the parity tier.

One exclusion removes a player from **every** plan the bot makes, and it does so
silently — there is no second screen where an operator finds out it happened. So the
two things that make such a row unreadable six months later are refused here rather
than on a surface: a blank reason, and an id nothing can resolve.
"""

from __future__ import annotations

import pytest

from fantabot.application.exclusions import (
    ExclusionRow,
    InvalidExclusion,
    clean_exclusion,
    join_names,
)


class TestWhatIsRefused:
    """Both refusals produce a row that cannot be acted on later, and both are missed
    by the surfaces on their own: Typer's required `--reason` does not catch
    `--reason ""`, and a JSON body has no Typer at all."""

    def test_a_blank_reason_is_refused(self) -> None:
        """`reason: str = typer.Option(...)` refuses a *missing* reason and accepts an
        empty one. The table's own docstring is explicit that a row with no provenance
        is indistinguishable from a typo."""
        with pytest.raises(InvalidExclusion) as caught:
            clean_exclusion(4344, reason="", source="goal.com")

        assert "reason" in str(caught.value)

    def test_a_whitespace_only_reason_is_refused_too(self) -> None:
        """A space is not a reason. It is what a form sends when nobody typed anything."""
        with pytest.raises(InvalidExclusion):
            clean_exclusion(4344, reason="   \t ", source="")

    @pytest.mark.parametrize("player_id", [0, -1, -4344])
    def test_an_id_that_cannot_be_a_player_is_refused(self, player_id: int) -> None:
        """Platform ids are positive. A `0` reaches the table, matches nothing for ever,
        and reads on the list as an exclusion that is doing something."""
        with pytest.raises(InvalidExclusion) as caught:
            clean_exclusion(player_id, reason="left Serie A 2026-08-30", source="")

        assert str(player_id) in str(caught.value)


class TestWhatSurvives:
    def test_the_reason_and_source_are_trimmed_and_otherwise_untouched(self) -> None:
        """Trimmed because a trailing newline is what a textarea sends; untouched
        otherwise because the reason is free text for a human and this is not its
        editor."""
        assert clean_exclusion(
            4344, reason="  left Serie A 2026-08-30 (Galatasaray)\n", source=" goal.com "
        ) == (4344, "left Serie A 2026-08-30 (Galatasaray)", "goal.com")

    def test_an_absent_source_stays_absent(self) -> None:
        """Optional, as the command has always had it. The reason is the required half."""
        assert clean_exclusion(4344, reason="left Serie A", source="") == (
            4344,
            "left Serie A",
            "",
        )


class TestTheNameOnTheRow:
    """`db exclusions` ran `SELECT id, nome FROM players WHERE id = ANY(:ids)` inside
    the Typer body — a join written where the app cannot reach it."""

    def test_a_known_id_carries_its_name(self) -> None:
        assert join_names(
            [(4344, "left Serie A 2026-08-30", "goal.com")], {4344: "Leao"}
        ) == [
            ExclusionRow(
                player_id=4344,
                nome="Leao",
                reason="left Serie A 2026-08-30",
                source="goal.com",
            )
        ]

    def test_an_id_players_does_not_carry_reads_as_none(self) -> None:
        """`None`, never `"?"`. An id `players` has never held is either a typo or a
        player from a season nobody scraped — and the first is a row to delete while
        the second is a scrape to run. The CLI rendered both as `?`."""
        assert join_names([(999_001, "typo", "")], {})[0].nome is None

    def test_the_table_order_is_kept(self) -> None:
        """`exclusions()` orders by `player_id`; the join must not reorder by whatever
        the name lookup returned."""
        rows = join_names(
            [(1, "a", ""), (2, "b", ""), (3, "c", "")],
            {3: "Third", 1: "First"},
        )

        assert [r.player_id for r in rows] == [1, 2, 3]
        assert [r.nome for r in rows] == ["First", None, "Third"]

    def test_the_reason_and_source_cross_verbatim(self) -> None:
        """A read is not an edit. `clean_exclusion` trims on the way in, once."""
        (row,) = join_names([(7, "  kept as stored  ", "  src  ")], {})

        assert row.reason == "  kept as stored  "
        assert row.source == "  src  "

    def test_no_exclusions_is_an_empty_list_and_not_an_error(self) -> None:
        """A fresh install has none, and that is a true answer rather than a failure."""
        assert join_names([], {}) == []
