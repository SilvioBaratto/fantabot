"""Which listone an `asta live --league` run is priced against, and who said so.

`asta live` declared `--format mantra` as its default and its own help text stated the
harm: *"a Classic room read as Mantra is advised off players it cannot call, priced off
another game — and nothing raises."* It documented the hazard and then defaulted into it.

Nothing raises because nothing can: `application/asta_planner` reads
`reference.quotazioni(season, listone)` and `clearing_sales(asta_type=listone)`, and both
are legal and non-empty for the wrong format. The listone bridge is format-agnostic too,
so even `EmptyPool` cannot fire. Exit 0, a full advisory, the wrong game.

Pure: this module decides *which* answer wins, never how to fetch one.
"""

from __future__ import annotations

import pytest

from fantabot.application.asta_format import (
    OPERATOR_TYPED,
    RECORDED_CORPUS,
    ROOM_DECLARED,
    FormatUnknown,
    choose_listone,
)


class TestWhatTheRoomSaysWins:
    def test_a_declared_format_is_taken(self) -> None:
        chosen = choose_listone("", "classic", None)

        assert (chosen.listone, chosen.provenance) == ("classic", ROOM_DECLARED)
        assert chosen.warning is None

    def test_a_declared_format_beats_the_corpus(self) -> None:
        """The room is about *this* room; the corpus is about rooms that looked like it."""
        chosen = choose_listone("", "classic", "mantra")

        assert (chosen.listone, chosen.provenance) == ("classic", ROOM_DECLARED)


class TestTheCorpusIsTheSecondRung:
    def test_the_recorded_format_is_used_when_the_room_will_not_say(self) -> None:
        chosen = choose_listone("", None, "mantra")

        assert (chosen.listone, chosen.provenance) == ("mantra", RECORDED_CORPUS)

    def test_it_says_where_the_answer_came_from(self) -> None:
        """A format harvested weeks ago is a weaker fact than one the room just stated, and
        the provenance is what lets an operator tell them apart on the terminal."""
        assert RECORDED_CORPUS != ROOM_DECLARED


class TestWhatTheOperatorTypedWinsOverBoth:
    def test_a_typed_format_is_taken(self) -> None:
        chosen = choose_listone("classic", None, None)

        assert (chosen.listone, chosen.provenance) == ("classic", OPERATOR_TYPED)

    @pytest.mark.parametrize(
        ("declared", "recorded"), [("mantra", None), (None, "mantra"), ("mantra", "mantra")]
    )
    def test_a_typed_format_that_disagrees_says_so(
        self, declared: str | None, recorded: str | None
    ) -> None:
        """Planning a room as something it is not is a thing to do deliberately or not at
        all — the same rule `_lega_rules` applies to a lega, for the same reason."""
        chosen = choose_listone("classic", declared, recorded)

        assert chosen.listone == "classic"
        assert chosen.warning is not None
        assert "mantra" in chosen.warning and "--format" in chosen.warning

    def test_when_the_two_reads_disagree_the_room_is_the_one_quoted(self) -> None:
        """The room's own word outranks the corpus, and that ordering is only observable
        here — everywhere else the two agree or only one answered.

        Concretely: the room says Classic, the corpus (which saw this id weeks ago) says
        Mantra, and the operator types `mantra`. That contradicts the **room**, so it warns
        and names the room. Read the corpus first and the typed value matches it, so the run
        goes silent about disagreeing with the live room — which is the one fact worth
        hearing. A mutation swapping the two survived the suite until this test existed.
        """
        chosen = choose_listone("mantra", "classic", "mantra")

        assert chosen.listone == "mantra"
        assert chosen.warning is not None, "disagreeing with the room passed unannounced"
        assert ROOM_DECLARED in chosen.warning
        assert "classic" in chosen.warning

    def test_the_corpus_is_named_when_it_is_the_only_read(self) -> None:
        """The other side of the same ordering: with no declared format, the warning has to
        say the corpus rather than claim the room said something."""
        chosen = choose_listone("mantra", None, "classic")

        assert chosen.warning is not None
        assert RECORDED_CORPUS in chosen.warning

    def test_a_typed_format_that_agrees_is_silent(self) -> None:
        chosen = choose_listone("classic", "classic", None)

        assert chosen.listone == "classic"
        assert chosen.warning is None


class TestNothingIsGuessed:
    def test_no_answer_at_all_is_refused(self) -> None:
        """**Not defaulted to mantra.** That default is the defect: `asta live` had it, and
        a Classic room read as Mantra produces a complete, confident, wrong advisory."""
        with pytest.raises(FormatUnknown) as refused:
            choose_listone("", None, None)

        said = str(refused.value)
        assert "mantra" in said and "classic" in said and "--format" in said

    @pytest.mark.parametrize("junk", ["Mantra", "mantar", "both", " classic"])
    def test_a_format_neither_source_recognises_is_refused(self, junk: str) -> None:
        """A room or a corpus row carrying an unknown string is not a third format to
        support. `asta room` learned this separately (`RoomRefused`); so does this."""
        with pytest.raises(FormatUnknown):
            choose_listone("", junk, None)

    def test_an_unknown_corpus_row_does_not_fall_through_to_a_guess(self) -> None:
        with pytest.raises(FormatUnknown):
            choose_listone("", None, "MANTRA")
