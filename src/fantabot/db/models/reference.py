"""Reference tables: the season-scoped data the scrapers produce.

``players`` is the referential root. Everything else in this module, plus both
match-grain tables, points at it.
"""

from __future__ import annotations

from sqlalchemy import BigInteger, Text
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
