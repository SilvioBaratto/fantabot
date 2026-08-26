"""Queries over the reference tables.

Everything here is a read of data the scrapers produced. Writes belong to the
importers, and to the scripts once they are ported.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select

from fantabot.db.models.reference import Player, Quotazione
from fantabot.db.repositories._base import RepositoryBase


@dataclass(frozen=True)
class QuotazioneRow:
    """One valuation, joined to the player's name. Pure value type."""

    player_id: str
    nome: str
    squadra: str
    ruoli_codice: tuple[str, ...]
    ruoli: tuple[str, ...]


class ReferenceRepository(RepositoryBase):
    """Reads over players, teams and the season-scoped valuation tables."""

    def quotazioni(self, stagione: str, listone: str) -> dict[str, QuotazioneRow]:
        """One season's valuations for one listone, keyed by player id as a string.

        ``nome`` comes from ``players`` rather than from this table: the name is
        stored once so the two cannot disagree. The id is returned as ``str``
        because every consumer of the pool — the prompt, the resume filter, the
        CSV history — has always held it that way.
        """
        rows = self.session.execute(
            select(
                Quotazione.player_id,
                Player.nome,
                Quotazione.squadra,
                Quotazione.ruoli_codice,
                Quotazione.ruoli,
            )
            .join(Player, Player.id == Quotazione.player_id)
            .where(Quotazione.stagione == stagione, Quotazione.listone == listone)
        ).all()

        return {
            str(row.player_id): QuotazioneRow(
                player_id=str(row.player_id),
                nome=row.nome,
                squadra=row.squadra,
                ruoli_codice=tuple(row.ruoli_codice),
                ruoli=tuple(row.ruoli),
            )
            for row in rows
        }
