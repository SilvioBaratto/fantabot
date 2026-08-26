"""Runtime state: what the bot has already done, and what it has already spent.

Replaces ``data/state.json``, which was an untyped ``dict[str, Any]`` with three
keys and no schema. Two changes are deliberate rather than incidental.

**Keyed by league.** The account is in two leghe (3584692 and 4103937) and a
single flat file could not tell them apart — one matchday guard served both, so
submitting in one marked the other done.

**``processed_bids`` is not ported.** ``state.py`` declared it and
``auction.py:65`` reset it, but nothing anywhere appended to it: it was state
that was persisted and never used. Meanwhile ``role_budget``, decremented at
``auction.py:85``, lived only in memory and was lost on any crash. ``auction_bids``
persists what actually needs persisting.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    SmallInteger,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from fantabot.db.base import Base, TimestampMixin


class BotState(Base, TimestampMixin):
    """One row per lega: what has already been done there."""

    __tablename__ = "bot_state"

    league_id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=False
    )

    last_lineup_matchday: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    last_auction_session_id: Mapped[str | None] = mapped_column(Text, nullable=True)

    def __repr__(self) -> str:
        return f"<BotState league={self.league_id} matchday={self.last_lineup_matchday}>"


# pending: the bid was recorded before it was placed and the result is unknown.
# won / lost: the room has settled it.
BID_OUTCOMES: tuple[str, ...] = ("pending", "won", "lost")


class AuctionBid(Base, TimestampMixin):
    """One bid, durable the moment it is decided.

    **This is what ``processed_bids`` was supposed to be.** That column was
    persisted and never appended to, while the number that mattered —
    ``role_budget``, decremented at ``auction.py:85`` — lived only in memory and
    was lost on any crash. Remaining budget is derived from these rows instead,
    so it is correct after any number of restarts.

    ``outcome`` is what makes the accounting fixable. The in-memory version
    subtracted the *bid* amount whether or not the bid won, so a losing bidding
    war permanently shrank the budget for a player somebody else bought.
    """

    __tablename__ = "auction_bids"

    league_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    session_id: Mapped[str] = mapped_column(Text, primary_key=True)
    player_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("players.id"), primary_key=True
    )
    placed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), primary_key=True, server_default=func.now()
    )

    role: Mapped[str] = mapped_column(String(1), nullable=False)
    amount: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    outcome: Mapped[str] = mapped_column(Text, nullable=False, server_default="pending")
    # What the player actually cost, once the room settles. Differs from amount
    # whenever the winning bid was not ours or the price moved after we bid.
    price: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)

    __table_args__ = (
        CheckConstraint("role IN ('P', 'D', 'C', 'A')", name="role"),
        CheckConstraint(
            "outcome IN ('pending', 'won', 'lost')", name="outcome"
        ),
        CheckConstraint("amount > 0", name="amount_positive"),
        # The budget query sums over this on every poll of a live auction.
        Index("ix_auction_bids_session_role", "league_id", "session_id", "role"),
    )

    def __repr__(self) -> str:
        return f"<AuctionBid {self.session_id} player={self.player_id} {self.outcome}>"
