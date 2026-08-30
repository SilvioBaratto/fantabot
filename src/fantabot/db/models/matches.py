"""Match-grain tables: one row per player per matchday.

The two largest tables in the schema, 50,634 rows each, and the only place where
a nullable foreign key is required.

**Why the primary key is surrogate.** SPEC asks for ``(stagione, giornata,
player_id)`` with ``player_id`` nullable. Postgres forbids a nullable column in a
primary key, and it would not work if it allowed it: 3039 rows per file are coach
(``Allenatore``) rows with an empty id, so they would all collide with each
other. The resolution — proven against a real database before any row was
imported — is a surrogate key plus two **disjoint** partial unique indexes, one
covering rows that have a player and one covering rows that do not. Every row is
covered by exactly one of them, and both were verified duplicate-free across all
50,634 rows before the constraint was written.

**Why ``squadra`` is called ``squadra_raw``.** The column is corrupt, by a
scraper bug that is still live: every row in a match block is labelled with the
fixture's *home* team, so the column cannot say which side a player played for.
(Measured in 2026 by ``scripts/analyze_qi_bias_by_team.py``, since deleted — the
finding is stated here rather than cited, so it survives the tool that found it.) What survives is the fixture — ``squadra_raw`` and
``avversario_raw`` together identify home and away correctly, and
``gol_squadra``/``gol_avversario`` are that fixture's score. A player's real club
for a season comes from ``quotazioni``, never from here. Nothing keys or joins on
these two columns, deliberately.
"""

from __future__ import annotations

from datetime import date, time
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Date,
    ForeignKey,
    Identity,
    Index,
    Numeric,
    SmallInteger,
    String,
    Text,
    Time,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from fantabot.db.base import Base, TimestampMixin

# Coach rows carry this instead of a player role, and no player id.
COACH_ROLE = "ALL"


def _partial_unique_indexes(table: str) -> tuple[Index, ...]:
    """The two disjoint keys that stand in for an impossible primary key."""
    return (
        Index(
            f"uq_{table}_player",
            "stagione",
            "giornata",
            "player_id",
            unique=True,
            postgresql_where=text("player_id IS NOT NULL"),
        ),
        Index(
            f"uq_{table}_coach",
            "stagione",
            "giornata",
            "nome",
            unique=True,
            postgresql_where=text("player_id IS NULL"),
        ),
    )


class Voto(Base, TimestampMixin):
    """One player's grades for one matchday, from all three grading sources.

    The six grade columns are nullable because a player can be ungraded, and a
    grade of zero is a real (terrible) grade rather than an absence.
    """

    __tablename__ = "voti"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)

    stagione: Mapped[str] = mapped_column(String(7), nullable=False)
    giornata: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    data: Mapped[date] = mapped_column(Date, nullable=False)
    ora: Mapped[time | None] = mapped_column(Time, nullable=True)

    squadra_raw: Mapped[str] = mapped_column(String(32), nullable=False)
    avversario_raw: Mapped[str] = mapped_column(String(32), nullable=False)
    gol_squadra: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    gol_avversario: Mapped[int] = mapped_column(SmallInteger, nullable=False)

    player_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("players.id"), nullable=True
    )
    nome: Mapped[str] = mapped_column(Text, nullable=False)
    ruolo_codice: Mapped[str] = mapped_column(String(3), nullable=False)
    ruolo: Mapped[str] = mapped_column(Text, nullable=False)

    voto_fc: Mapped[Decimal | None] = mapped_column(Numeric(4, 2), nullable=True)
    fantavoto_fc: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    voto_stat: Mapped[Decimal | None] = mapped_column(Numeric(4, 2), nullable=True)
    fantavoto_stat: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    voto_italia: Mapped[Decimal | None] = mapped_column(Numeric(4, 2), nullable=True)
    fantavoto_italia: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)

    __table_args__ = (
        *_partial_unique_indexes("voti"),
        Index("ix_voti_stagione_giornata", "stagione", "giornata"),
    )

    def __repr__(self) -> str:
        return f"<Voto {self.stagione} g{self.giornata} player={self.player_id}>"


class BonusMalus(Base, TimestampMixin):
    """One player's countable events for one matchday.

    Same grain and the same keying problem as ``voti``, and the same 3039 coach
    rows. The ten counters are NOT NULL: a player who scored no goals scored
    zero goals, which is a fact rather than a gap. There is no ``ora`` column —
    the source file does not carry one.
    """

    __tablename__ = "bonus_malus"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)

    stagione: Mapped[str] = mapped_column(String(7), nullable=False)
    giornata: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    data: Mapped[date] = mapped_column(Date, nullable=False)

    squadra_raw: Mapped[str] = mapped_column(String(32), nullable=False)
    avversario_raw: Mapped[str] = mapped_column(String(32), nullable=False)

    player_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("players.id"), nullable=True
    )
    nome: Mapped[str] = mapped_column(Text, nullable=False)
    ruolo_codice: Mapped[str] = mapped_column(String(3), nullable=False)
    ruolo: Mapped[str] = mapped_column(Text, nullable=False)

    ammonizione: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    espulsione: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    gol_segnati: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    gol_subiti: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    autoreti: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    rigori_segnati: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    rigori_sbagliati: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    rigori_parati: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    assist: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    mvp: Mapped[int] = mapped_column(SmallInteger, nullable=False)

    __table_args__ = (
        *_partial_unique_indexes("bonus_malus"),
        Index("ix_bonus_malus_stagione_giornata", "stagione", "giornata"),
    )

    def __repr__(self) -> str:
        return f"<BonusMalus {self.stagione} g{self.giornata} player={self.player_id}>"
