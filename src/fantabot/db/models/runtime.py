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

from sqlalchemy import BigInteger, SmallInteger, Text
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
