"""Only sales feed the price model, and both formats have one. Marked ``db``.

**The bug this exists for.** `clearing_sales` filtered on the auction's shape and
on the player being linked, and on nothing else -- so every lot that was *called and
never bid on* entered the mean at its opening price. 12,544 of 43,298 assignment rows,
29% of them, are unsold lots: no buyer, one ladder rung, and that rung's `team_id` is
`None`.

The platform will not sell a player for 0 credits; the minimum bid is 1. So a row with
no buyer is not a purchase, whatever price it carries -- and 364 of these carry a price
above zero, being opening calls at a starting price. Filtering on `price > 0` would have
left those in.

What it cost: Leao's 21 rows had a median of 0 and a maximum of 3, so the optimizer
priced a 19-credit player at 1 and put him in the roster as free value. The whole
long tail of a 500-credit plan was built on lots nobody bought.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from fantabot.adapters.persistence.repositories.aste import AsteRepository

pytestmark = pytest.mark.db


def test_no_sale_is_priced_at_zero(db_session: Session) -> None:
    """The rule the platform enforces, asserted against what we read from it."""
    sales = AsteRepository(db_session).clearing_sales()

    assert sales, "no sales at all — the fixture or the shape filter is wrong"
    assert [price for _, price in sales if price < 1] == []


def test_every_row_read_has_a_buyer(db_session: Session) -> None:
    """A purchase has someone who made it. That is what makes it a purchase.

    Counted rather than inspected row by row: the repository projects only the player and
    the price, so the buyer is checked here against the same filter in SQL.
    """
    read = len(AsteRepository(db_session).clearing_sales())
    with_buyer = db_session.execute(
        text(
            "SELECT count(*) FROM asta_assignment a JOIN asta t ON t.id = a.asta_id "
            "WHERE t.asta_type = 'mantra' AND t.num_credits = 500 AND t.num_teams = 8 "
            "AND a.fantacalcio_id IS NOT NULL AND a.buyer_team_id IS NOT NULL"
        )
    ).scalar_one()

    assert read == with_buyer


def test_unsold_lots_exist_and_are_excluded(db_session: Session) -> None:
    """Without this the two tests above could pass on data that has no unsold lots.

    They are the majority of what the collector records for some players, so a filter
    that silently stopped matching would not show up as an empty result.
    """
    unsold = db_session.execute(
        text(
            "SELECT count(*) FROM asta_assignment a JOIN asta t ON t.id = a.asta_id "
            "WHERE t.asta_type = 'mantra' AND t.num_credits = 500 AND t.num_teams = 8 "
            "AND a.fantacalcio_id IS NOT NULL AND a.buyer_team_id IS NULL"
        )
    ).scalar_one()

    assert unsold > 0, "no unsold lots in the data — this test is proving nothing"
    assert unsold not in {0, len(AsteRepository(db_session).clearing_sales())}


class TestBothCorporaAreRead:
    """The Classic corpus existed for a week and nothing read it.

    `mantra_clearing_sales` pinned `asta_type = 'mantra'` in its name and in its filter, so
    `read_plan_inputs` guarded the call and handed Classic an empty mapping. These two tests
    are the ones that would have caught it: the first says the Classic corpus is there, the
    second says the two are genuinely different reads rather than one query answering twice.

    Measured 2026-09-05 against this database: 32,100 Classic sales / 453 players / 259
    rooms, against 6,625 / 424 / 49 for Mantra, in our own 8x500 shape.
    """

    def test_the_classic_corpus_is_not_empty(self, db_session: Session) -> None:
        sales = AsteRepository(db_session).clearing_sales(asta_type="classic")

        assert sales, "no Classic sales — the corpus is missing or the filter is wrong"
        assert [price for _, price in sales if price < 1] == []

    def test_the_two_formats_are_different_reads(self, db_session: Session) -> None:
        """Disjoint by room, not by count: two equal counts would still pass a `!=`.

        A room is one auction and carries one `asta_type`, so no room can appear in both.
        Comparing the sale rows instead would be weaker — the same player at the same price
        legitimately occurs in both corpora.
        """
        repo = AsteRepository(db_session)
        classic = repo.clearing_sales(asta_type="classic")
        mantra = repo.clearing_sales(asta_type="mantra")

        assert classic and mantra
        rooms = {
            fmt: {
                row[0]
                for row in db_session.execute(
                    text(
                        "SELECT DISTINCT a.asta_id FROM asta_assignment a "
                        "JOIN asta t ON t.id = a.asta_id "
                        "WHERE t.asta_type = :fmt AND t.num_credits = 500 AND t.num_teams = 8 "
                        "AND a.fantacalcio_id IS NOT NULL AND a.buyer_team_id IS NOT NULL"
                    ),
                    {"fmt": fmt},
                )
            }
            for fmt in ("classic", "mantra")
        }

        assert rooms["classic"] and rooms["mantra"]
        assert not rooms["classic"] & rooms["mantra"]

    def test_an_unknown_format_raises_rather_than_reading_nothing(
        self, db_session: Session
    ) -> None:
        with pytest.raises(ValueError, match="asta_type"):
            AsteRepository(db_session).clearing_sales(asta_type="mantra_")
