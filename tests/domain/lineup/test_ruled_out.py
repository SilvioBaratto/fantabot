"""Who the platform itself rules out, read off a `lineUpInfo` row. Pure and total.

One value of one field is read as "out": `status == 2`, an `int` and nothing else. The platform
does not document it; it was **observed** on four players (two on 2026-09-22 in the golden
fixture, two on 2026-10-14), each with `percent` 0 and a `descr` naming an injury and a return
date. Every other value of `status`, and `percent` as a probability, are unmeasured, so none of
them is read. The function sits on the hourly job's default path, so it must never raise.
"""

from __future__ import annotations

import json
import math
from typing import Any

import pytest
from _paths import GOLDEN

from fantabot.domain.lineup.ruled_out import (
    MAX_REASON,
    TRIPWIRE_SHARE,
    RuledOut,
    implausible,
    ruled_out_from_row,
)

LEYSEN: dict[str, Any] = {
    "pid": 6052, "plyr": "Leysen F.", "status": 2, "percent": 0,
    "descr": "lesione crociato anteriore del ginocchio sinistro, ipotesi rientro da aprile",
    "comment": "lesione crociato anteriore del ginocchio sinistro, ipotesi rientro da aprile",
}


def _row(**over: Any) -> dict[str, Any]:
    return {**LEYSEN, **over}


class TestStatusTwoIsOut:
    """The one value that means out."""
    def test_the_observed_row_is_ruled_out_with_the_platform_s_own_words(self) -> None:
        """The observed Leysen row is out, with his descr as the reason."""
        assert ruled_out_from_row(LEYSEN) == RuledOut(reason=LEYSEN["descr"])

    def test_the_status_alone_decides_whatever_percent_says(self) -> None:
        """`percent` was 0 on every observed status-2 row, and that is all that is known of it:
        it is not a probability, so a row with a high one is still out."""
        assert ruled_out_from_row(_row(percent=90)) == RuledOut(reason=LEYSEN["descr"])
        assert ruled_out_from_row(_row(percent=None)) == RuledOut(reason=LEYSEN["descr"])
        assert ruled_out_from_row({"status": 2}) == RuledOut(reason="")

    def test_a_row_without_words_is_still_out(self) -> None:
        """The status decides; a missing reason costs only the sentence."""
        assert ruled_out_from_row(_row(descr=None, comment=None)) == RuledOut(reason="")
        assert ruled_out_from_row(_row(descr="  ", comment="")) == RuledOut(reason="")

    def test_the_comment_stands_in_when_descr_is_blank(self) -> None:
        """`comment` carried the same text as `descr` on every observed row."""
        assert ruled_out_from_row(_row(descr="", comment="rientro a marzo")) == RuledOut(
            reason="rientro a marzo"
        )


class TestNothingElseIsRead:
    """Every other value, type and field is unmeasured and so is not read."""
    @pytest.mark.parametrize("status", [1, 0, 3, 4, -1, 20, None])
    def test_another_status_is_not_ruled_out(self, status: object) -> None:
        """Only 1 and 2 have been observed. A value nobody has explained is not a verdict."""
        assert ruled_out_from_row(_row(status=status)) is None

    def test_a_missing_status_is_not_ruled_out(self) -> None:
        """No field, no verdict."""
        row = dict(LEYSEN)
        del row["status"]
        assert ruled_out_from_row(row) is None
        assert ruled_out_from_row({}) is None

    @pytest.mark.parametrize(
        "status",
        [True, 2.0, 2.5, "2", " 2", "two", [2], (2,), {"status": 2}, b"2", object(), math.nan],
    )
    def test_only_the_integer_two_counts(self, status: object) -> None:
        """`True == 2` is false but `2.0 == 2` is true, and a JSON `"2"` is a string: a type
        that drifted is a field that changed meaning, not a player who is out."""
        assert ruled_out_from_row(_row(status=status)) is None

    def test_a_high_percent_with_another_status_is_not_ruled_out(self) -> None:
        """`percent` 0 beside status 1 is not a ruling either."""
        assert ruled_out_from_row(_row(status=1, percent=0)) is None


class TestTheReasonIsBounded:
    """The reason is the platform's free text, so its length is ours to cap."""
    def test_a_long_reason_is_cut_to_the_bound_and_says_it_was_cut(self) -> None:
        """Cut to the bound, with an ellipsis as the last character."""
        reason = ruled_out_from_row(_row(descr="x" * 5000)).reason  # type: ignore[union-attr]
        assert len(reason) == MAX_REASON
        assert reason.endswith("…")

    def test_a_reason_at_the_bound_is_kept_whole(self) -> None:
        """The bound is inclusive."""
        assert ruled_out_from_row(_row(descr="y" * MAX_REASON)) == RuledOut("y" * MAX_REASON)

    def test_whitespace_runs_and_newlines_collapse_to_one_space(self) -> None:
        """The reason lands in a one-line note and a JSON run record."""
        row = _row(descr="  problema\n\tmuscolare  \r\n al polpaccio ")
        assert ruled_out_from_row(row) == RuledOut("problema muscolare al polpaccio")

    @pytest.mark.parametrize("descr", [5, 2.5, True, ["rientro"], {"a": 1}, object(), b"x"])
    def test_a_reason_that_is_not_text_is_dropped_and_the_row_is_still_out(
        self, descr: object
    ) -> None:
        assert ruled_out_from_row(_row(descr=descr, comment=None)) == RuledOut("")


class TestTheReasonCarriesNoControlCharacters:
    """The reason reaches a terminal and a JSONL record, and it is the platform's free text:
    an escape sequence must not drive the operator's console, and a lone surrogate must not
    make the record's UTF-8 encode raise."""

    @pytest.mark.parametrize(
        ("descr", "expected"),
        [
            ("rientro\x00 a marzo", "rientro a marzo"),
            ("\x1b[2Jrientro", "[2Jrientro"),
            ("rientro\x07\x7f a marzo", "rientro a marzo"),
            ("ri\u200bentro \u202ea marzo", "rientro a marzo"),
            ("rientro\x1fa marzo", "rientro a marzo"),
            ("rientro\x85a marzo", "rientro a marzo"),
            ("rientro\ud800 a marzo", "rientro a marzo"),
        ],
    )
    def test_a_control_character_is_dropped_and_the_words_are_kept(
        self, descr: str, expected: str
    ) -> None:
        """Whitespace-like separators (`\\x1f`, `\\x85`) still separate words; the rest vanish
        without leaving a doubled space."""
        assert ruled_out_from_row(_row(descr=descr)) == RuledOut(expected)

    def test_nothing_but_control_characters_falls_through_to_the_comment(self) -> None:
        """An empty reason is no reason, so `comment` stands in exactly as for a blank one."""
        row = _row(descr="\x00\x07\u200b", comment="rientro a marzo")

        assert ruled_out_from_row(row) == RuledOut("rientro a marzo")

    def test_nothing_but_control_characters_and_no_comment_is_an_empty_reason(self) -> None:
        assert ruled_out_from_row(_row(descr="\x00\x1b", comment=None)) == RuledOut("")

    def test_what_is_kept_is_printable_and_encodes_to_utf8(self) -> None:
        """The two properties the console and the JSONL record need."""
        reason = ruled_out_from_row(_row(descr="a\x00b\ud800c\u202ed\n\x1be")).reason  # type: ignore[union-attr]

        assert reason.isprintable()
        reason.encode("utf-8")
        json.dumps(reason, ensure_ascii=False).encode("utf-8")

    def test_the_bound_counts_what_is_kept_and_not_what_was_dropped(self) -> None:
        """Control characters do not eat the budget: the cut is on the cleaned text."""
        reason = ruled_out_from_row(_row(descr="\x00".join("y" * MAX_REASON))).reason  # type: ignore[union-attr]

        assert reason == "y" * MAX_REASON

    def test_the_accented_words_of_the_platform_survive(self) -> None:
        """Printable non-ASCII is the platform's language, not noise."""
        row = _row(descr="rientro metà di ottobre, è un'«ipotesi»")

        assert ruled_out_from_row(row) == RuledOut("rientro metà di ottobre, è un'«ipotesi»")


class TestTheTripwire:
    """If an implausible share of the roster says "out", the field has probably changed
    meaning, and acting on it would bench a quarter of the team on a misread."""

    def test_the_threshold_is_a_quarter(self) -> None:
        """The number the operator was told, stated once."""
        assert TRIPWIRE_SHARE == 0.25

    @pytest.mark.parametrize(("ruled", "priced"), [(2, 30), (7, 30), (3, 12), (0, 30), (0, 0)])
    def test_a_quarter_or_less_is_plausible(self, ruled: int, priced: int) -> None:
        """Two of thirty is what both live reads showed. Exactly a quarter still passes:
        the bound is *more than* a quarter."""
        assert not implausible(ruled, priced)

    @pytest.mark.parametrize(("ruled", "priced"), [(8, 30), (4, 12), (1, 3), (30, 30), (4, 15)])
    def test_more_than_a_quarter_is_not(self, ruled: int, priced: int) -> None:
        assert implausible(ruled, priced)

    def test_it_is_a_share_of_the_priced_roster(self) -> None:
        """The same count is plausible in a big roster and not in a small one."""
        assert not implausible(7, 32)
        assert implausible(7, 27)


class TestItNeverRaises:
    """The default path runs this on every row, whatever the setting."""
    @pytest.mark.parametrize(
        "row",
        [
            {},
            {"status": 2, "descr": object(), "comment": object()},
            {"status": object(), "descr": "x"},
            {"status": 2, "descr": "é" * 10_000},
            {"status": 2, "descr": "\x00\ud800"},
        ],
    )
    def test_a_malformed_row_costs_that_player_and_not_the_call(self, row: dict[str, Any]) -> None:
        """Whatever the row holds, the call returns."""
        ruled_out_from_row(row)


class TestTheCapturedRows:
    """The golden fixture is the lega's roster as the platform returned it on 2026-09-22."""

    def _rows(self) -> list[dict[str, Any]]:
        document = json.loads((GOLDEN / "lineup" / "teamLineup_read.json").read_text("utf-8"))
        return document["lineUpInfo"]

    def test_exactly_the_two_players_the_platform_marked_are_ruled_out(self) -> None:
        """Rovella and Piotrowski, and nobody else."""
        out = {row["plyr"]: ruled_out_from_row(row) for row in self._rows()}
        assert {name for name, ruling in out.items() if ruling} == {"Rovella", "Piotrowski"}
        assert out["Rovella"] == RuledOut(
            "problema muscolare al polpaccio, rientro metà di ottobre"
        )

    def test_every_other_row_is_status_one(self) -> None:
        """What the zero-effect claim rests on: 28 of 30 rows carry `status` 1 and are read as
        nothing at all, whatever their `percent` (40 to 90)."""
        rows = [row for row in self._rows() if ruled_out_from_row(row) is None]
        assert len(rows) == 28
        assert {row["status"] for row in rows} == {1}
