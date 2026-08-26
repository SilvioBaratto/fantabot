"""Match-grain tables.

Today this holds only ``ProbeMatchGrain``, which is **throwaway**: it exists so
the Alembic scaffold can prove, against a real Postgres and before a single row
is imported, that autogenerate round-trips the two constructs the real schema
cannot avoid. It is retired in T20, when the real ``voti`` and ``bonus_malus``
tables replace it. It must never reach a database that anyone keeps.

The shape it probes is the resolution of a problem SPEC states but does not
solve. SPEC asks for ``voti`` keyed ``(stagione, giornata, player_id)`` with
``player_id`` nullable — and Postgres forbids a nullable column in a primary
key. It also would not work if it were allowed: all 3039 coach rows carry an
empty ``id``, so they would collide with each other. The resolution is a
surrogate primary key plus two *disjoint* partial unique indexes, one for rows
that have a player and one for rows that do not.
"""

from __future__ import annotations

from sqlalchemy import ARRAY, BigInteger, Identity, Index, SmallInteger, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from fantabot.db.base import Base


class ProbeMatchGrain(Base):
    """THROWAWAY probe — see the module docstring. Retired in T20."""

    __tablename__ = "_probe_match_grain"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    stagione: Mapped[str] = mapped_column(String(7), nullable=False)
    giornata: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    player_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    nome: Mapped[str] = mapped_column(Text, nullable=False)
    ruoli_codice: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False)

    __table_args__ = (
        # Rows with a player: unique on the platform id.
        Index(
            "uq_probe_match_grain_player",
            "stagione",
            "giornata",
            "player_id",
            unique=True,
            postgresql_where=text("player_id IS NOT NULL"),
        ),
        # Coach rows: no id to key on, so the display name carries it.
        Index(
            "uq_probe_match_grain_coach",
            "stagione",
            "giornata",
            "nome",
            unique=True,
            postgresql_where=text("player_id IS NULL"),
        ),
    )
