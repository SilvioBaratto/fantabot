"""Reads and writes over the runtime-state tables."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from fantabot.db.models.runtime import BotState
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
