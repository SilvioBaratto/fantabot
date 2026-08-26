"""Reads and writes over the runtime-state tables."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert

from fantabot.db.models.runtime import AuctionBid, BotState
from fantabot.db.repositories._base import RepositoryBase


class RuntimeRepository(RepositoryBase):
    """What the bot has already done in one lega."""

    def last_lineup_matchday(self, league_id: int) -> int | None:
        """The matchday whose lineup was last submitted, or ``None``.

        ``None`` for a lega with no row at all, which is the same answer
        ``state.load()`` gave for a missing file — a bot that has done nothing
        yet and one whose file is missing are the same situation.
        """
        return self.session.execute(
            select(BotState.last_lineup_matchday).where(BotState.league_id == league_id)
        ).scalar_one_or_none()

    def record_lineup_submitted(self, league_id: int, matchday: int) -> None:
        """Mark a matchday done. Upsert: the first submission creates the row."""
        statement = insert(BotState).values(
            league_id=league_id, last_lineup_matchday=matchday
        )
        self.session.execute(
            statement.on_conflict_do_update(
                index_elements=[BotState.league_id],
                set_={"last_lineup_matchday": statement.excluded.last_lineup_matchday},
            )
        )

    def last_auction_session_id(self, league_id: int) -> str | None:
        return self.session.execute(
            select(BotState.last_auction_session_id).where(BotState.league_id == league_id)
        ).scalar_one_or_none()

    def record_auction_session(self, league_id: int, session_id: str) -> None:
        statement = insert(BotState).values(
            league_id=league_id, last_auction_session_id=session_id
        )
        self.session.execute(
            statement.on_conflict_do_update(
                index_elements=[BotState.league_id],
                set_={
                    "last_auction_session_id": statement.excluded.last_auction_session_id
                },
            )
        )


class AuctionRepository(RepositoryBase):
    """Bids, and the budget derived from them.

    The semantics are stated here rather than inferred, because the in-memory
    version got them wrong in a way nobody would notice:

    * **won** reduces the allocation by ``price`` — what the player actually
      cost — falling back to ``amount`` if the room has not reported a price.
    * **lost** reduces nothing. ``auction.py:85`` subtracted the bid whether it
      won or not, so a losing bidding war permanently shrank the budget for a
      player somebody else bought.
    * **pending** reserves ``amount``. A bid is recorded before it is placed, so
      between the two the credits must be treated as committed — otherwise a
      crash mid-bid frees money that may already be spent.
    """

    def record_bid(
        self,
        *,
        league_id: int,
        session_id: str,
        player_id: int,
        role: str,
        amount: int,
        placed_at: datetime,
    ) -> None:
        """Write the bid before it is placed. Idempotent on a retry."""
        statement = insert(AuctionBid).values(
            league_id=league_id,
            session_id=session_id,
            player_id=player_id,
            placed_at=placed_at,
            role=role,
            amount=amount,
            outcome="pending",
        )
        self.session.execute(
            statement.on_conflict_do_update(
                index_elements=[
                    AuctionBid.league_id,
                    AuctionBid.session_id,
                    AuctionBid.player_id,
                    AuctionBid.placed_at,
                ],
                set_={"amount": statement.excluded.amount},
            )
        )

    def settle_bid(
        self,
        *,
        league_id: int,
        session_id: str,
        player_id: int,
        placed_at: datetime,
        outcome: str,
        price: int | None = None,
    ) -> None:
        """Record what the room decided. ``price`` is what it actually cost."""
        self.session.execute(
            update(AuctionBid)
            .where(
                AuctionBid.league_id == league_id,
                AuctionBid.session_id == session_id,
                AuctionBid.player_id == player_id,
                AuctionBid.placed_at == placed_at,
            )
            .values(outcome=outcome, price=price)
        )

    def committed_by_role(self, league_id: int, session_id: str) -> dict[str, int]:
        """Credits already spent or reserved, per role. One statement."""
        rows = self.session.execute(
            select(
                AuctionBid.role,
                # Both terms are coalesced: a FILTER that matches nothing sums
                # to NULL, and NULL + 0 is NULL, so a session with only pending
                # bids would report nothing committed.
                func.coalesce(
                    func.sum(
                        func.coalesce(AuctionBid.price, AuctionBid.amount)
                    ).filter(AuctionBid.outcome == "won"),
                    0,
                )
                + func.coalesce(
                    func.sum(AuctionBid.amount).filter(AuctionBid.outcome == "pending"),
                    0,
                )
            )
            .where(
                AuctionBid.league_id == league_id,
                AuctionBid.session_id == session_id,
            )
            .group_by(AuctionBid.role)
        ).all()
        return {role: int(committed or 0) for role, committed in rows}

    def remaining_budget(
        self, league_id: int, session_id: str, allocation: dict[str, int]
    ) -> dict[str, int]:
        """What is left per role, given the allocation the strategy chose.

        This is what a restart recovers. The in-memory counter it replaces was
        lost on any crash, and SPEC criterion 12 is exactly this function
        returning the same answer after one.
        """
        committed = self.committed_by_role(league_id, session_id)
        return {
            role: max(0, total - committed.get(role, 0))
            for role, total in allocation.items()
        }
