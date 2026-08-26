"""Reference tables: the season-scoped data the scrapers produce.

``players`` is the referential root. Everything else in this module, plus both
match-grain tables, points at it.
"""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import (
    ARRAY,
    BigInteger,
    CheckConstraint,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Numeric,
    SmallInteger,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from fantabot.db.base import Base, TimestampMixin


class Player(Base, TimestampMixin):
    """One footballer, identified by the platform's own id.

    ``id`` is **not** surrogate: it is the id leghe.fantacalcio.it uses, which
    every CSV and every API response keys on. Generating our own would mean
    maintaining a mapping for no benefit.

    ``nome`` is a display name and is deliberately not unique — 94 of the 1474
    ids spell theirs more than one way across seasons (``SORIANO``/``Soriano``,
    ``Lucumi'``/``Lucumì``), so the importer picks one deterministically rather
    than the schema pretending the collision does not exist.
    """

    __tablename__ = "players"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    nome: Mapped[str] = mapped_column(Text, nullable=False)

    def __repr__(self) -> str:
        return f"<Player id={self.id} nome={self.nome!r}>"


class Team(Base, TimestampMixin):
    """One Serie A club in one season.

    Season-scoped, not global: 27 distinct clubs appear across the five seasons
    on disk but only 20 in any one of them, because of promotion and relegation.

    This table exists because the source files use two incompatible vocabularies
    for the same thing. ``quotazioni``, ``statistiche``, ``qi_bias`` and
    ``target_price`` write a three-letter code (``ATA``, ``MIL``); ``voti`` and
    ``bonus_malus`` write the full name (``Fiorentina``). Without the bridge, a
    join between them returns zero rows and no error.
    """

    __tablename__ = "teams"

    stagione: Mapped[str] = mapped_column(String(7), primary_key=True)
    codice: Mapped[str] = mapped_column(String(3), primary_key=True)
    nome_completo: Mapped[str] = mapped_column(Text, nullable=False)

    def __repr__(self) -> str:
        return f"<Team {self.stagione} {self.codice} {self.nome_completo!r}>"


# Both listoni share every fact table; this is the discriminator.
LISTONI: tuple[str, ...] = ("classic", "mantra")
_LISTONE_CHECK = "listone IN ('classic', 'mantra')"


class Quotazione(Base, TimestampMixin):
    """One player's valuation for one season, on one listone.

    Classic and Mantra share this table because the grain is identical and the
    only difference is the role column. ``ruoli_codice`` holds a single-element
    array for Classic (``{P}``) and the full set for Mantra (``{B,DS,E}``).

    The source CSVs store those ``;``-joined. The array is a deliberate
    departure: ``;``-splitting in SQL is not something a query should have to do,
    and role membership is the most common filter this table will serve.
    """

    __tablename__ = "quotazioni"

    stagione: Mapped[str] = mapped_column(String(7), primary_key=True)
    player_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("players.id"), primary_key=True
    )
    listone: Mapped[str] = mapped_column(String(7), primary_key=True)

    squadra: Mapped[str] = mapped_column(String(3), nullable=False)
    ruoli_codice: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False)
    ruoli: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False)
    qi: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    qa: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    fvm: Mapped[int] = mapped_column(SmallInteger, nullable=False)

    __table_args__ = (
        # Composite FK, not a plain one: a club code only means something within
        # a season, because promotion and relegation change the set of 20.
        ForeignKeyConstraint(
            ["stagione", "squadra"], ["teams.stagione", "teams.codice"]
        ),
        CheckConstraint(_LISTONE_CHECK, name="listone"),
        Index("ix_quotazioni_stagione_squadra", "stagione", "squadra"),
    )

    def __repr__(self) -> str:
        return f"<Quotazione {self.stagione} {self.listone} player={self.player_id}>"


# The three grading sources the site publishes side by side.
FONTI: tuple[str, ...] = ("fantacalcio", "italia", "statistico")


class Statistica(Base, TimestampMixin):
    """One player's season totals, per listone and per grading source.

    Three ``fonte`` values — fantacalcio, italia, statistico — publish different
    averages for the same player, so the grain is four-way, not three.

    ``media_voto`` and ``media_fantavoto`` are **nullable**. The source writes
    ``"0,0"`` for a player it has no average for, which is absent rather than a
    grade of zero: 2846 of the 8034 rows per listone carry it. Storing that as 0
    would drag every average that reads this table toward zero and nothing would
    look wrong. The counter columns really are zero when they say zero, so they
    are NOT NULL.
    """

    __tablename__ = "statistiche"

    stagione: Mapped[str] = mapped_column(String(7), primary_key=True)
    fonte: Mapped[str] = mapped_column(String(12), primary_key=True)
    player_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("players.id"), primary_key=True
    )
    listone: Mapped[str] = mapped_column(String(7), primary_key=True)

    squadra: Mapped[str] = mapped_column(String(3), nullable=False)
    ruoli_codice: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False)
    ruoli: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False)

    partite_giocate: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    media_voto: Mapped[Decimal | None] = mapped_column(Numeric(4, 2), nullable=True)
    media_fantavoto: Mapped[Decimal | None] = mapped_column(Numeric(4, 2), nullable=True)
    gol: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    gol_subiti: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    rigori_segnati: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    rigori_tirati: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    rigori_parati: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    assist: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    ammonizioni: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    espulsioni: Mapped[int] = mapped_column(SmallInteger, nullable=False)

    __table_args__ = (
        ForeignKeyConstraint(
            ["stagione", "squadra"], ["teams.stagione", "teams.codice"]
        ),
        CheckConstraint(_LISTONE_CHECK, name="listone"),
        CheckConstraint(
            "fonte IN ('fantacalcio', 'italia', 'statistico')", name="fonte"
        ),
        Index("ix_statistiche_stagione_player", "stagione", "player_id"),
    )

    def __repr__(self) -> str:
        return f"<Statistica {self.stagione} {self.fonte} player={self.player_id}>"


class QiBias(Base, TimestampMixin):
    """How far a player's actual auction price drifted from the initial quote.

    ``delta`` is ``qa - qi`` and ``pct_delta`` is that as a percentage — both
    derived, both stored, because three analysis scripts read them and
    recomputing in every query is worse than one denormalised column.

    The source files are **dot**-decimal, unlike ``statistiche`` and ``voti``.
    The importer uses ``plain_decimal`` for exactly that reason.
    """

    __tablename__ = "qi_bias"

    stagione: Mapped[str] = mapped_column(String(7), primary_key=True)
    player_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("players.id"), primary_key=True
    )
    listone: Mapped[str] = mapped_column(String(7), primary_key=True)

    squadra: Mapped[str] = mapped_column(String(3), nullable=False)
    ruoli_codice: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False)
    qi: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    qa: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    fvm: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    delta: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    pct_delta: Mapped[Decimal] = mapped_column(Numeric(8, 2), nullable=False)

    __table_args__ = (
        ForeignKeyConstraint(
            ["stagione", "squadra"], ["teams.stagione", "teams.codice"]
        ),
        CheckConstraint(_LISTONE_CHECK, name="listone"),
        CheckConstraint("delta = qa - qi", name="delta_is_derived"),
    )

    def __repr__(self) -> str:
        return f"<QiBias {self.stagione} {self.listone} player={self.player_id}>"
