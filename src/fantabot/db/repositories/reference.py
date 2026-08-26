"""Queries over the reference tables.

Everything here is a read of data the scrapers produced. Writes belong to the
importers, and to the scripts once they are ported.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

from sqlalchemy import select, union
from sqlalchemy.dialects.postgresql import insert

from fantabot.club_names import build_mapping
from fantabot.db.models.matches import BonusMalus, Voto
from fantabot.db.models.reference import Player, Quotazione, Statistica, Team
from fantabot.db.repositories._base import RepositoryBase

if TYPE_CHECKING:
    from sqlalchemy import CursorResult


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

    def backfill_team_names(self) -> int:
        """Resolve ``teams.nome_completo`` from the two vocabularies in Postgres.

        The codes come from ``quotazioni``/``statistiche``; the full names from
        ``voti``/``bonus_malus``. Nothing in the data states the correspondence,
        so it is derived by ``club_names.build_mapping`` and **gated**: a partial
        mapping leaves ``nome_completo`` NULL, later joins silently drop those
        rows, and every table still looks populated.

        Why this exists at all: the scraper path writes the three-letter *code*
        into the name column (``scripts/_db.py:320-322``) so the foreign key is
        satisfied the moment a listone lands. That placeholder is correct and
        deliberate — on a rebuild ``quotazioni`` is written before ``voti``
        exists, so resolving names inline would fail closed and abort the
        scrape. This runs afterwards, once names are available.

        Returns the number of rows whose name changed. **Zero when no names are
        available yet**, so a July ``scrape_quotazioni`` against a fresh database
        still succeeds rather than dying on an empty ``voti``.

        Raises ``TeamMappingError`` — and writes nothing — on a prefix collision
        or a code with no name.
        """
        names = set(
            self.session.execute(
                union(
                    select(Voto.squadra_raw).distinct(),
                    select(Voto.avversario_raw).distinct(),
                    select(BonusMalus.squadra_raw).distinct(),
                    select(BonusMalus.avversario_raw).distinct(),
                )
            ).scalars()
        )
        names.discard(None)
        if not names:
            # A fresh database, or one scraped listone-first. Not an error: the
            # placeholder codes stay until fixtures exist to resolve them.
            return 0

        pairs = set(
            self.session.execute(
                union(
                    select(Quotazione.stagione, Quotazione.squadra).distinct(),
                    select(Statistica.stagione, Statistica.squadra).distinct(),
                )
            ).all()
        )
        if not pairs:
            return 0

        # Raises rather than returning a partial mapping.
        mapping = build_mapping(names, {code for _, code in pairs})

        changed = 0
        for stagione, codice in sorted(pairs):
            nome = mapping[codice.strip().upper()]
            statement = insert(Team).values(
                stagione=stagione, codice=codice, nome_completo=nome
            )
            result = self.session.execute(
                statement.on_conflict_do_update(
                    index_elements=[Team.stagione, Team.codice],
                    set_={"nome_completo": statement.excluded.nome_completo},
                    where=Team.nome_completo.is_distinct_from(statement.excluded.nome_completo),
                )
            )
            # Session.execute is typed as returning Result; only CursorResult
            # carries rowcount. Narrowed explicitly rather than ignored, so a
            # change that stops returning a cursor result fails the type check.
            changed += cast("CursorResult[Any]", result).rowcount or 0
        return changed
