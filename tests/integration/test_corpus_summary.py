"""The corpus panel's numbers, against a real Postgres. Marked ``db``.

The panel this feeds is the instrument every later collection run is graded on, so the
thing worth pinning is not that it returns numbers — it is that its headline number is
*the same* number a plan is built on. The Classic-corpus loss records what a
plausible-looking
count would have cost: the Classic corpus read as 2.1 million events and zero sales for
over a week, and no screen could tell "nothing collected" from "collected and never
joined".

Two classes, two databases, on purpose. The synthetic one writes rows and so must run
against the tier's own database; the live one asserts against the recorded 614k-row seed
and carries the ``dbdata`` marker that sends it to the canonical one. Both roll back.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy.orm import Session

from fantabot.adapters.persistence.models.aste import ASTA_TYPES
from fantabot.adapters.persistence.repositories.aste import AsteRepository

NOW = datetime(2026, 9, 5, 21, 0, tzinfo=UTC)

#: Ours, and nobody else's. The tier database is shared across the whole `db` tier, so
#: every assertion below is a *delta* rather than an absolute — the mistake
#: `test_aste_db.py` records having made, where counting the whole table passed only
#: while the database happened to be empty.
SHAPED = "aaaaaaaa-0000-4000-8000-000000000001"
WRONG_SHAPE = "aaaaaaaa-0000-4000-8000-000000000002"


def _auction(auction_id: str, *, num_teams: int) -> dict[str, object]:
    return {
        "id": auction_id,
        "db_shard": "4",
        "asta_type": "mantra",
        "name": "corpus-summary-test",
        "num_teams": num_teams,
        "num_credits": 500,
        "first_seen_at": NOW,
        "last_seen_at": NOW,
    }


def _assignment(auction_id: str, uuid: str, **over: object) -> dict[str, object]:
    row: dict[str, object] = {
        "asta_id": auction_id,
        "player_uuid": uuid,
        "fantacalcio_id": None,
        "price": 12,
        "buyer_team_id": None,
        "closed_at_ms": 1,
        "ladder": [],
    }
    row.update(over)
    return row


@pytest.mark.db
class TestTheCountsMoveByExactlyWhatWasWritten:
    """Deltas, not absolutes: the tier database is shared and never guaranteed empty."""

    def test_a_write_moves_only_its_own_format_and_only_by_what_it_wrote(
        self, db_session: Session, synthetic_players: object
    ) -> None:
        repo = AsteRepository(db_session)
        player_a, player_b = (int(pid) for pid in synthetic_players(2))  # type: ignore[operator]
        before = {row.asta_type: row for row in repo.corpus_summary()}

        repo.upsert_auctions([_auction(SHAPED, num_teams=8), _auction(WRONG_SHAPE, num_teams=10)])
        repo.upsert_events(
            [
                {
                    "asta_id": SHAPED,
                    "last_update": update,
                    "seen_at": NOW,
                    "update_type": "raise",
                    "payload": {"price": update},
                }
                for update in (1, 2)
            ]
        )
        repo.upsert_assignments(
            [
                # A sale: both halves present, and the room is our shape.
                _assignment(SHAPED, "p-sold", fantacalcio_id=player_a, buyer_team_id="t1"),
                # Called and never bought — 29% of the real table looks like this.
                _assignment(SHAPED, "p-unsold", fantacalcio_id=player_b),
                # Bought, but the listone never bridged him to a fantacalcio id.
                _assignment(SHAPED, "p-unlinked", buyer_team_id="t2"),
                # A real sale in a room that is not our shape.
                _assignment(WRONG_SHAPE, "p-other", fantacalcio_id=player_a, buyer_team_id="t3"),
            ]
        )
        db_session.flush()

        after = {row.asta_type: row for row in repo.corpus_summary()}
        mantra, was = after["mantra"], before["mantra"]

        assert mantra.rooms - was.rooms == 2
        assert mantra.rooms_with_events - was.rooms_with_events == 1
        assert mantra.events - was.events == 2
        assert mantra.assignments - was.assignments == 4
        assert mantra.assignments_with_buyer - was.assignments_with_buyer == 3
        assert mantra.assignments_with_player - was.assignments_with_player == 3
        # One of the four: the other three each fail exactly one half of the filter.
        assert mantra.planner_sales - was.planner_sales == 1

        classic, classic_was = after["classic"], before["classic"]
        assert classic == classic_was, "a mantra write moved the classic counts"

    def test_a_room_registered_but_never_followed_is_visible_as_such(
        self, db_session: Session
    ) -> None:
        """The distinction the panel exists for: registered is not collected.

        A seed grows on every scan, so `rooms` rises whether or not a single frame was
        ever received. Without `rooms_with_events` beside it, an evening on which the
        collector never connected reads exactly like one on which it did.
        """
        repo = AsteRepository(db_session)
        before = {row.asta_type: row for row in repo.corpus_summary()}["mantra"]

        repo.upsert_auctions([_auction(SHAPED, num_teams=8)])
        db_session.flush()

        after = {row.asta_type: row for row in repo.corpus_summary()}["mantra"]
        assert after.rooms - before.rooms == 1
        assert after.rooms_with_events == before.rooms_with_events


@pytest.mark.db
@pytest.mark.dbdata
class TestAgainstTheRecordedCorpus:
    """The headline number is the number a plan is built on, or it is worse than nothing."""

    @pytest.mark.parametrize("asta_type", ASTA_TYPES)
    def test_planner_sales_is_exactly_what_clearing_sales_returns(
        self, db_session: Session, asta_type: str
    ) -> None:
        repo = AsteRepository(db_session)
        summary = {row.asta_type: row for row in repo.corpus_summary()}

        assert summary[asta_type].planner_sales == len(repo.clearing_sales(asta_type=asta_type))

    def test_both_formats_are_reported_and_both_hold_a_corpus(
        self, db_session: Session
    ) -> None:
        """A parametrized equality alone would pass on two zeros."""
        summary = {row.asta_type: row for row in AsteRepository(db_session).corpus_summary()}

        assert sorted(summary) == sorted(ASTA_TYPES)
        assert all(summary[fmt].planner_sales > 0 for fmt in ASTA_TYPES)

    def test_a_shape_nobody_played_reports_zero_sales_but_keeps_its_rooms(
        self, db_session: Session
    ) -> None:
        """The shape is a parameter, and asking about another one must change the answer.

        `rooms` and `events` are shape-independent by construction, so they stay put; only
        the planner-filtered count moves. That asymmetry is what says the filter is being
        applied where it is meant to be and nowhere else.
        """
        repo = AsteRepository(db_session)
        ours = {row.asta_type: row for row in repo.corpus_summary()}
        other = {
            row.asta_type: row
            for row in repo.corpus_summary(num_credits=7, num_teams=3)
        }

        for fmt in ASTA_TYPES:
            assert other[fmt].planner_sales == 0
            assert other[fmt].rooms == ours[fmt].rooms
            assert other[fmt].events == ours[fmt].events
