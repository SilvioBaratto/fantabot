"""Players who must never enter the plan, and why.

The listone is fantacalcio.it's and it lags reality. Rafael Leao's permanent transfer to
Galatasaray was announced on 2026-08-30; the site still carried him at MIL, quotazione
18, the next day. Buying a player who has left Serie A is dead money — he cannot score —
and nothing already in the engine prevents it:

* **Re-scraping does not.** The scraper faithfully reproduces the site, and the site
  still lists him.
* **The sentiment layer cannot, by design.** Its gate is floored at ``disp_floor=0.50``
  and ``tit_floor=0.40`` so that news tilts a value and never vetoes it —
  ``domain/asta/sentiment.py`` states the rule and the reason. Measured on the real pool:
  the worst reading the schema allows takes Leao from 65.47 to 38.24, and at a 1-credit
  observed price he stays in the optimal roster anyway.

So this is a separate fact and lives separately. One row per player, with the reason and
where it came from, because in six months "why is this id here" is the only question
anyone will have.

**It is not a price signal and not a sentiment reading.** Those are judgements about how
good a player is. This is a statement that he is not available to buy at all.
"""

from __future__ import annotations

from sqlalchemy import BigInteger, Text
from sqlalchemy.orm import Mapped, mapped_column

from fantabot.adapters.persistence.base import Base, TimestampMixin


class PlayerExclusion(Base, TimestampMixin):
    """One player kept out of the pool. Keyed by the fantacalcio player id."""

    __tablename__ = "player_exclusion"

    player_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)

    #: Free text, for a human. Say what happened and when, not "excluded".
    reason: Mapped[str] = mapped_column(Text, nullable=False)

    #: Where the claim came from — a URL, a report, a person. An exclusion with no
    #: provenance is indistinguishable from a typo, and this one removes a player from
    #: every plan the bot makes.
    source: Mapped[str] = mapped_column(Text, nullable=False, default="")
