"""The auction tables against a real Postgres. Marked ``db``.

What is worth exercising here is not that rows land — it is that writing the
*same* rows twice changes nothing. The collector was killed eleven times on
2026-08-26 and each restart re-emitted the current state of every auction it
watched; a write path that duplicates would show phantom rungs in every ladder
rebuilt from those rows.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from fantabot.adapters.persistence.models.aste import Asta, AstaAssignment, AstaEvent
from fantabot.adapters.persistence.repositories.aste import AsteRepository

pytestmark = pytest.mark.db

AUCTION = "11111111-1111-1111-1111-111111111111"
OTHER = "22222222-2222-2222-2222-222222222222"

#: Only rows keyed to these are this module's business.
OURS = (AUCTION, OTHER)
NOW = datetime(2026, 8, 27, 6, 0, tzinfo=UTC)


def _auction(**over: object) -> dict[str, object]:
    row: dict[str, object] = {
        "id": AUCTION,
        "db_shard": "4",
        "asta_type": "classic",
        "name": "test",
        "num_teams": 8,
        "num_credits": 500,
        "first_seen_at": NOW,
        "last_seen_at": NOW,
    }
    row.update(over)
    return row


def _event(last_update: int | None, **over: object) -> dict[str, object]:
    row: dict[str, object] = {
        "asta_id": AUCTION,
        "last_update": last_update,
        "seen_at": NOW,
        "update_type": "raise",
        "payload": {"price": 7, "last_update": last_update},
    }
    row.update(over)
    return row


def _count(session: Session, model: type) -> int:
    """Rows of ``model`` for **this test's** auctions only.

    An earlier version counted the whole table and passed only because the
    database happened to be empty. Loading the recorded evening turned all five
    of these red at once — the tests were reading state they did not create, and
    the rollback fixture cannot undo what another process committed.

    **The model→filter map is explicit, and used to be a `hasattr` fallback.**
    ``model.asta_id if hasattr(model, "asta_id") else model.id`` fails *open*: when
    `asta_event.asta_id` became `asta_key` the filter silently degraded to
    ``id IN (<uuids>)``. Here that raised, because text and bigint are incompatible —
    but between two compatible types it would have matched nothing and made every
    idempotence assertion below pass vacuously, on the one table that cannot be
    re-collected. A KeyError on an unmapped model is the failure this wants.
    """
    filters = {
        "Asta": lambda m: m.id,
        "AstaEvent": lambda m: m.asta_key,
        "AstaAssignment": lambda m: m.asta_id,
    }
    column = filters[model.__name__](model)
    return int(
        session.execute(
            select(func.count())
            .select_from(model)
            .where(
                column.in_(
                    select(Asta.key).where(Asta.id.in_(OURS))
                    if model.__name__ == "AstaEvent"
                    else OURS
                )
            )
        ).scalar_one()
    )


def test_writing_the_same_events_twice_leaves_one_row_each(db_session: Session) -> None:
    repo = AsteRepository(db_session)
    repo.upsert_auctions([_auction()])
    rows = [_event(1000), _event(1001)]
    repo.upsert_events(rows)
    repo.upsert_events(rows)
    db_session.flush()
    assert _count(db_session, AstaEvent) == 2


def test_events_without_a_last_update_are_kept_not_collapsed(db_session: Session) -> None:
    """They cannot conflict — there is no key on which to call them the same
    observation — so they must land rather than be silently dropped."""
    repo = AsteRepository(db_session)
    repo.upsert_auctions([_auction()])
    repo.upsert_events([_event(None), _event(None)])
    db_session.flush()
    assert _count(db_session, AstaEvent) == 2


def test_re_registering_an_auction_does_not_rewrite_when_we_first_met_it(
    db_session: Session,
) -> None:
    repo = AsteRepository(db_session)
    repo.upsert_auctions([_auction()])
    later = datetime(2026, 8, 27, 9, 0, tzinfo=UTC)
    repo.upsert_auctions([_auction(first_seen_at=later, last_seen_at=later, name="renamed")])
    db_session.flush()
    stored = db_session.get(Asta, AUCTION)
    assert stored is not None
    assert stored.first_seen_at == NOW, "first_seen_at must not move forward"
    assert stored.last_seen_at == later
    assert stored.name == "renamed"


def test_re_registering_an_auction_keeps_its_key_and_its_league(
    db_session: Session,
) -> None:
    """The production break `upsert_auctions` would have shipped green.

    That method builds its `ON CONFLICT SET` by iterating the model, so every column
    added to `Asta` is enrolled automatically. `auction_rows` supplies neither `key`
    nor `fantaleague_id`, so without an explicit exclusion a rescan sets both to
    NULL — and `harvest load` calls this before `upsert_events` on every ten-second
    pass. The key would be renumbered under the events pointing at it and the league
    blanked on the row the payload reconstruction joins back to, continuously, on a
    green suite. Nothing else in the tree notices: the assertions above are about
    `first_seen_at` and `name`.
    """
    repo = AsteRepository(db_session)
    repo.upsert_auctions([_auction()])
    db_session.flush()

    first = db_session.get(Asta, AUCTION)
    assert first is not None
    first.fantaleague_id = "fl-42"
    db_session.flush()
    original_key = first.key

    # A rescan, exactly as `harvest scan` issues it.
    repo.upsert_auctions([_auction(name="renamed")])
    db_session.flush()
    db_session.expire_all()

    after = db_session.get(Asta, AUCTION)
    assert after is not None
    assert after.name == "renamed", "the rescan did happen"
    assert after.key == original_key, "the surrogate key was renumbered under its events"
    assert after.fantaleague_id == "fl-42", "the league was blanked by a rescan"


def test_a_reconstruction_can_be_corrected_by_rerunning_it(db_session: Session) -> None:
    """Assignments are derived. A fixed reducer must be able to overwrite what a
    broken one wrote, which is why this path is DO UPDATE and events are not."""
    repo = AsteRepository(db_session)
    repo.upsert_auctions([_auction()])
    base = {
        "asta_id": AUCTION,
        "player_uuid": "p-1",
        "fantacalcio_id": None,
        "price": 5,
        "buyer_team_id": "t-1",
        "closed_at_ms": 1000,
        "ladder": [{"price": 5, "team_id": "t-1", "at_ms": 999}],
    }
    repo.upsert_assignments([base])
    repo.upsert_assignments([{**base, "price": 42}])
    db_session.flush()
    assert _count(db_session, AstaAssignment) == 1
    stored = db_session.get(AstaAssignment, (AUCTION, "p-1"))
    assert stored is not None and stored.price == 42


def test_an_unknown_player_does_not_cost_us_the_assignment(db_session: Session) -> None:
    """2 of 407 players auctioned on 2026-08-26 were signings newer than our last
    scrape. A NOT NULL foreign key would have refused those rows."""
    repo = AsteRepository(db_session)
    repo.upsert_auctions([_auction()])
    repo.upsert_assignments(
        [
            {
                "asta_id": AUCTION,
                "player_uuid": "unknown",
                "fantacalcio_id": None,
                "price": 62,
                "buyer_team_id": None,
                "closed_at_ms": None,
                "ladder": [],
            }
        ]
    )
    db_session.flush()
    assert _count(db_session, AstaAssignment) == 1


def test_counting_can_filter_by_format(db_session: Session) -> None:
    repo = AsteRepository(db_session)
    other = OTHER
    before_all = repo.count_assignments()
    before_classic = repo.count_assignments("classic")
    before_mantra = repo.count_assignments("mantra")
    repo.upsert_auctions([_auction(), _auction(id=other, asta_type="mantra")])
    repo.upsert_assignments(
        [
            {"asta_id": AUCTION, "player_uuid": "a", "fantacalcio_id": None, "price": 1,
             "buyer_team_id": None, "closed_at_ms": None, "ladder": []},
            {"asta_id": other, "player_uuid": "b", "fantacalcio_id": None, "price": 2,
             "buyer_team_id": None, "closed_at_ms": None, "ladder": []},
        ]
    )
    db_session.flush()
    # Deltas, not absolutes: this database also holds a recorded evening, and a
    # test that asserts a table total is asserting on someone else's data.
    assert repo.count_assignments() - before_all == 2
    assert repo.count_assignments("classic") - before_classic == 1
    assert repo.count_assignments("mantra") - before_mantra == 1


class TestRecordedAuctionsExcludesTheSelfBidRoom:
    """"è morto malen" (`0752a384-0611-4df4-8c95-2f8aaa38425c`) is the room `asta room` bid
    real credits in on 2026-09-01 — it is genuinely in this database (Task 6/7's own
    evidence), so this reads the real row rather than writing one, to avoid upserting over
    production data with a test fixture that happens to share its id.
    """

    SELF_BID_ROOM = "0752a384-0611-4df4-8c95-2f8aaa38425c"

    def test_it_is_absent_from_the_default_corpus(self, db_session: Session) -> None:
        repo = AsteRepository(db_session)
        corpus = repo.recorded_auctions(num_teams=8, num_credits=500)

        assert self.SELF_BID_ROOM not in dict(corpus)

    def test_it_is_present_when_the_exclusion_is_lifted(self, db_session: Session) -> None:
        """Proves the exclusion is doing something — not that the room is simply absent
        from this database for an unrelated reason."""
        repo = AsteRepository(db_session)
        corpus = repo.recorded_auctions(num_teams=8, num_credits=500, exclude=frozenset())

        assert self.SELF_BID_ROOM in dict(corpus)
