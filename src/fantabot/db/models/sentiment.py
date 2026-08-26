"""The weekly news-sentiment time series.

The column set is ``news/store.py:COLUMNS``, name for name, with one rename:
``id`` becomes ``player_id`` so it can carry the foreign key. SPEC is explicit
that the set matches, so nothing is dropped as derivable — ``n_fonti`` stays even
though it is ``cardinality(fonti)``, because dropping it is a deviation to be
asked about rather than a free simplification.

``(data_run, player_id)`` is the primary key, and it **is** the existing resume
index: ``store.existing_keys`` returns exactly those pairs, and ``cli.py`` filters
the pool against them. So resume becomes an ``ON CONFLICT DO NOTHING`` upsert
with the same observable behaviour, and ``--force`` finally means update rather
than append a duplicate.

``deriva_ruolo`` is ``numeric(3,2)`` and not boolean. ``mantra.drift`` returns
``0.0`` or the model's own ``confidenza``, and ``drifted()`` ranks players by it;
a flag collapses that ranking to arbitrary order. SPEC said boolean, was ruled
against on 2026-08-26, and amended.

``ruolo_campo`` and ``ruoli_mantra`` stay ``;``-joined text rather than becoming
arrays. SPEC's departures table lists only ``ruoli_codice``, ``fonti`` and
``flags`` as ``text[]``, and ``store.build_row`` deliberately normalises and
sorts these two so the stored cell is comparable to its neighbour.
"""

from __future__ import annotations

from datetime import date as date_type
from decimal import Decimal

from sqlalchemy import ARRAY, BigInteger, Date, ForeignKey, Index, Numeric, SmallInteger, Text
from sqlalchemy.orm import Mapped, mapped_column

from fantabot.data_sources.models import SCORES
from fantabot.db.base import Base, TimestampMixin

# The eight model-produced scores, defined once in data_sources.models.
# Re-exported under the old name so callers here read naturally.
SCORE_COLUMNS: tuple[str, ...] = SCORES


def _score() -> Mapped[Decimal]:
    """A model score. Two decimal places, exactly as build_row writes them."""
    return mapped_column(Numeric(3, 2), nullable=False)


class PlayerSentiment(Base, TimestampMixin):
    """One player's news reading for one run day."""

    __tablename__ = "player_sentiment"

    data_run: Mapped[date_type] = mapped_column(Date, primary_key=True)
    player_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("players.id"), primary_key=True
    )

    giorni_lookback: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    stagione: Mapped[str] = mapped_column(Text, nullable=False)
    nome: Mapped[str] = mapped_column(Text, nullable=False)
    squadra: Mapped[str] = mapped_column(Text, nullable=False)
    ruolo: Mapped[str] = mapped_column(Text, nullable=False)
    ruoli_mantra: Mapped[str] = mapped_column(Text, nullable=False)
    ruolo_campo: Mapped[str] = mapped_column(Text, nullable=False)
    deriva_ruolo: Mapped[Decimal] = mapped_column(Numeric(3, 2), nullable=False)

    sentiment: Mapped[Decimal] = _score()
    disponibilita: Mapped[Decimal] = _score()
    titolarita: Mapped[Decimal] = _score()
    mercato: Mapped[Decimal] = _score()
    forma: Mapped[Decimal] = _score()
    rigorista: Mapped[Decimal] = _score()
    piazzati: Mapped[Decimal] = _score()
    confidenza: Mapped[Decimal] = _score()

    riassunto: Mapped[str] = mapped_column(Text, nullable=False)
    n_fonti: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    fonti: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False)
    modello: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (
        # drifted() scans for stale tags across the latest run.
        Index("ix_player_sentiment_data_run_deriva", "data_run", "deriva_ruolo"),
        Index("ix_player_sentiment_player", "player_id", "data_run"),
    )

    def __repr__(self) -> str:
        return f"<PlayerSentiment {self.data_run} player={self.player_id}>"
