"""Read the sentiment time-series back out.

The rule that governs everything here: **rows with ``confidenza == 0`` are
excluded from every average.** That value means "no coverage was found", not
"this player is neutral". Folding its 0.0 into a mean invents a data point and
destroys the distinction the schema exists to preserve — so a player whose only
rows are silent has no trailing average at all, and says so by returning ``None``.

This is the read side of ``fantabot news-fetch``. ``strategy.py`` does not consume
it yet; wiring ``disponibilita``/``rigorista`` into ``decide_bid`` and
``titolarita`` into ``pick_starting_lineup`` is a later phase.

**No longer a snapshot.** The CSV version slurped the whole file at construction
and answered from that dict forever. ``auction.py``'s ``watch_and_bid`` polls for
hours, so it would have held a frozen reading for the whole duration of an asta.
Every call is a query now, and a row written after construction is visible to the
next one.

A missing file used to read as empty; an **unreachable database does not**. Those
are different facts: an empty table means nobody has been queried yet, while a
database that is down means the answer is unknown, and returning ``None`` for it
would let a lineup be picked on silence that was never measured.
"""

from __future__ import annotations

from datetime import date

from sqlalchemy.orm import Session

from fantabot.data_sources.models import (
    SCORES,
    RoleDrift,
    SentimentRow,
    TrailingSentiment,
)
from fantabot.db.repositories.sentiment import SentimentReadRepository

__all__ = [
    "SCORES",
    "NewsSentimentSource",
    "RoleDrift",
    "SentimentRow",
    "TrailingSentiment",
]


class NewsSentimentSource:
    """Query the stored sentiment series. Holds a session, never a cached table."""

    def __init__(self, session: Session) -> None:
        self._repo = SentimentReadRepository(session)

    def latest(self, player_id: str) -> SentimentRow | None:
        """His most recent reading, or ``None`` if he has never been queried."""
        return self._repo.latest(player_id)

    def all_latest(self, *, data_run: date | None = None) -> dict[str, SentimentRow]:
        """Every player's most recent reading, in one query. Silent rows included.

        ``data_run`` pins the read to one run; an unknown one is empty, never a fallback.
        """
        return self._repo.all_latest(data_run=data_run)

    def trailing(self, player_id: str, weeks: int = 4) -> TrailingSentiment | None:
        """Mean of each score over the last ``weeks`` runs, silent rows excluded."""
        return self._repo.trailing(player_id, weeks)

    def drift(self, player_id: str) -> RoleDrift | None:
        """The latest role drift for one player, or ``None`` if the tag still holds."""
        return self._repo.drift(player_id)

    def drifted(self) -> list[RoleDrift]:
        """Every player whose frozen Mantra tag no longer describes them, worst first.

        This is what a Mantra lineup engine actually wants: the platform will never
        correct these tags, so this list is the only warning that a schema slot is
        being filled by someone who no longer plays there.
        """
        return self._repo.drifted()
