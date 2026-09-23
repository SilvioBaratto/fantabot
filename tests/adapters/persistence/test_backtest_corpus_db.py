"""The backtest corpus as it really is. Marked `dbdata`: measured, not fixtured.

`admit`'s rules are tested purely beside the module; these are the numbers they produce
over 4,866 harvested rooms and 192,197 recorded sales, and they are the evidence for three
claims that no synthetic fixture can make.

* **The corpus is 17 Mantra rooms and 148 rosters**, out of 210 that record a bought sale
  at all. That is small, and it is the reason Gate 1 is a gate rather than a formality.
* **The size rule costs 4 rooms and 30 rosters** — 21/178 before it. The four are named,
  so a change that admits them has to say so rather than report a bigger corpus.
* **`max_player` must be ignored.** 7 of the 17 admitted rooms declare 25 and every one of
  them holds a roster above it. Judging by the declaration would throw away 7 of 17.

Classic is measured too, and it is nine times the size. Nothing reads it yet — the phase's
backtest is Mantra — but an admission rule that silently emptied it would be invisible in
a Mantra-only assertion.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from fantabot.adapters.persistence.repositories.lineup_history import BacktestCorpusRepository
from fantabot.domain.lineup.backtest_corpus import Corpus, admit

pytestmark = [pytest.mark.db, pytest.mark.dbdata]

#: The four rooms the 32-player ceiling rejects, measured 2026-09-23. Prefixes, because the
#: ids are uuids and the prefix is what an operator matches against FantaLab.
OVERSIZED = ("07a229dd", "1323c309", "134b7272", "ecf2e90e")


def _corpus(session: Session, asta_type: str = "mantra") -> Corpus:
    rooms, sales = BacktestCorpusRepository(session).corpus_rows(asta_type=asta_type)
    if not sales:
        # Skip on the *read*, never on the fold: an empty `Corpus` from a non-empty read is
        # exactly what a broken admission rule produces, and skipping on it would silence
        # every measurement below at the moment they matter.
        pytest.skip("no harvested sales in this database")
    return admit(rooms, sales)


def test_the_mantra_corpus_is_seventeen_rooms_and_one_hundred_and_forty_eight_rosters(
    db_session: Session,
) -> None:
    corpus = _corpus(db_session)

    assert (len(corpus.rooms), corpus.roster_count) == (17, 148)


def test_the_size_rule_costs_four_named_rooms_and_thirty_rosters(db_session: Session) -> None:
    """21 rooms / 178 rosters before it. The four are named so a change that admits them
    reports a different set rather than a bigger number."""
    rooms, sales = BacktestCorpusRepository(db_session).corpus_rows()
    if not sales:
        pytest.skip("no harvested sales in this database")

    with_rule = admit(rooms, sales)
    without = admit(rooms, sales, max_roster=10_000)

    assert (len(without.rooms), without.roster_count) == (21, 178)
    assert sorted(a[:8] for a in with_rule.rejected_for("ROSTER_TOO_LARGE")) == list(OVERSIZED)


def test_a_declared_max_player_of_twenty_five_never_rejects_a_room(db_session: Session) -> None:
    """The measurement the rule exists for: 7 of the 17 declare 25, and all 7 exceed it."""
    corpus = _corpus(db_session)
    declared = [room for room in corpus.rooms if room.declared_max_player == 25]

    assert len(declared) == 7
    assert all(
        max(len(roster) for roster in room.rosters) > 25 for room in declared
    ), "a declared 25 that no roster exceeds would make this test vacuous"


def test_the_rejection_breakdown_is_the_pinned_rule_order(db_session: Session) -> None:
    """`NO_SHAPE 3 / BUYER_COUNT 30 / TOO_FEW_IDS 156 / ROSTER_TOO_LARGE 4`. Putting the id
    count before the buyer count gives `3 / 1 / 185 / 4` — the same 17 admitted, and an
    operator sent to the wrong rule."""
    corpus = _corpus(db_session)

    assert [len(corpus.rejected_for(r)) for r in
            ("NO_SHAPE", "BUYER_COUNT", "TOO_FEW_IDS", "ROSTER_TOO_LARGE")] == [3, 30, 156, 4]


def test_the_corpus_is_not_one_shape(db_session: Session) -> None:
    """8x500 is 7 of the 17. A replay that averaged across shapes would price a 10-team
    300-credit room off an 8x500 cell."""
    counts = {c.shape: (c.rooms, c.rosters) for c in _corpus(db_session).by_shape()}

    assert counts["8x500"] == (7, 56)
    assert len(counts) >= 5


def test_recovering_the_id_through_the_uuid_bridge_is_load_bearing(db_session: Session) -> None:
    """A sale whose `fantacalcio_id` is null is a player auctioned before our last listone
    scrape. Dropping those rows shrinks their buyers, and a buyer under 23 refuses the whole
    room — so the recovery is an admission rule in disguise. Measured against the same read
    with the bridge withheld."""
    rooms, sales = BacktestCorpusRepository(db_session).corpus_rows()
    if not sales:
        pytest.skip("no harvested sales in this database")
    linked = set(
        db_session.execute(
            text(
                "select asta_id, buyer_team_id, fantacalcio_id from asta_assignment "
                "where fantacalcio_id is not null and buyer_team_id is not null"
            )
        ).all()
    )

    with_bridge = admit(rooms, sales)
    without = admit(
        rooms,
        [s for s in sales if (s.asta_id, s.buyer_team_id, s.player_id) in linked],
    )

    assert len(with_bridge.rooms) >= len(without.rooms)
    assert with_bridge.roster_count >= without.roster_count
    assert len(sales) > sum(1 for s in sales if (s.asta_id, s.buyer_team_id, s.player_id) in linked)


def test_the_classic_corpus_is_read_by_the_same_rule(db_session: Session) -> None:
    """Nothing replays it yet; a rule that silently emptied it would be invisible in a
    Mantra-only assertion, and it is nine times the size."""
    corpus = _corpus(db_session, "classic")

    assert len(corpus.rooms) == 162
    assert corpus.roster_count == 1267
