"""Queries over the reference tables.

Most of it is a read of data the scrapers produced, and the scrapers still own the
bulk write path. Three writes have moved here as they were ported, and this said
there were none — and said "belong to the the scrapers" — until 2026-09-24:
``exclude_player`` and ``unexclude_player`` maintain ``player_exclusion``, which no
scrape produces, and ``backfill_team_names`` upserts the resolved ``teams.nome_completo``.
"""

from __future__ import annotations

from collections.abc import Collection
from typing import TYPE_CHECKING, Any, cast

from sqlalchemy import delete, select, union
from sqlalchemy.dialects.postgresql import insert

from fantabot.adapters.persistence.models.exclusions import PlayerExclusion
from fantabot.adapters.persistence.models.matches import MatchGrain
from fantabot.adapters.persistence.models.reference import Player, Quotazione, Statistica, Team
from fantabot.adapters.persistence.repositories._base import RepositoryBase
from fantabot.domain.shared.club_names import build_mapping
from fantabot.domain.shared.values import QuotazioneRow

if TYPE_CHECKING:
    from sqlalchemy import CursorResult


class ReferenceRepository(RepositoryBase):
    """Reads over players, teams and the season-scoped valuation tables."""

    def excluded_player_ids(self) -> set[str]:
        """Players who must never enter a plan. Ids as `str`, like every other id here.

        Read on every planning run rather than cached: the whole point is that it is
        editable between runs, and the table holds single digits of rows.
        """
        rows = self.session.execute(select(PlayerExclusion.player_id)).scalars().all()
        return {str(player_id) for player_id in rows}

    def exclude_player(self, player_id: int, *, reason: str, source: str = "") -> None:
        """Record one exclusion, replacing any earlier reason for the same player.

        An upsert, like every other write here: re-running a correction is not an error.
        """
        self.session.execute(
            insert(PlayerExclusion)
            .values(player_id=player_id, reason=reason, source=source)
            .on_conflict_do_update(
                index_elements=[PlayerExclusion.player_id],
                set_={"reason": reason, "source": source},
            )
        )

    def unexclude_player(self, player_id: int) -> None:
        """Remove one exclusion. The only write here that is not an upsert.

        The `league_*` tables are append-only so that drift between captures stays
        visible; this table is not a capture. It is a standing statement that a player
        cannot be bought, and a statement that was wrong has to be withdrawable —
        until this existed a typo'd id was removed with `psql` and in no other way.

        Whether a row was there is the caller's to decide and it does so by reading
        first: `application/exclusions.remove_exclusion` needs the row's own reason
        anyway, which is the half of it nothing else holds.
        """
        self.session.execute(
            delete(PlayerExclusion).where(PlayerExclusion.player_id == player_id)
        )

    def exclusions(self) -> list[tuple[int, str, str]]:
        """`(player_id, reason, source)` for every exclusion, ordered by id."""
        rows = self.session.execute(
            select(PlayerExclusion.player_id, PlayerExclusion.reason, PlayerExclusion.source)
            .order_by(PlayerExclusion.player_id)
        ).all()
        return [(pid, reason, source) for pid, reason, source in rows]

    def player_names(self, player_ids: Collection[int]) -> dict[int, str]:
        """`id -> nome`, for the ids `players` carries. An absent id is simply missing.

        Missing rather than a placeholder: whether an id is on `players` at all is a
        fact the caller has to be able to tell — an exclusion keyed on an id nothing
        resolves is either a typo or a season nobody scraped, and the remedies differ.
        See `application/exclusions.py`, which is the one caller.

        An empty input runs no query rather than an `IN ()`.
        """
        if not player_ids:
            return {}
        rows = self.session.execute(
            select(Player.id, Player.nome).where(Player.id.in_(player_ids))
        ).all()
        return {player_id: nome for player_id, nome in rows}

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
                Quotazione.fvm,
            )
            .join(Player, Player.id == Quotazione.player_id)
            .where(Quotazione.stagione == stagione, Quotazione.listone == listone)
            .order_by(Quotazione.player_id)  # stable order: downstream tie-breaks depend on it
        ).all()

        return {
            str(row.player_id): QuotazioneRow(
                player_id=str(row.player_id),
                nome=row.nome,
                squadra=row.squadra,
                ruoli_codice=tuple(row.ruoli_codice),
                ruoli=tuple(row.ruoli),
                fvm=int(row.fvm),
            )
            for row in rows
        }

    def backfill_team_names(self) -> int:
        """Resolve ``teams.nome_completo`` from the two vocabularies in Postgres.

        The codes come from ``quotazioni``/``statistiche``; the full names from
        ``match_grain``. Nothing in the data states the correspondence,
        so it is derived by ``club_names.build_mapping`` and **gated**: a partial
        mapping leaves ``nome_completo`` NULL, later joins silently drop those
        rows, and every table still looks populated.

        Why this exists at all: the scraper path writes the three-letter *code*
        into the name column (``adapters/persistence/scraping.upsert_quotazioni``;
        it was ``scripts/_db.py`` when this was written) so the foreign key is
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
                    select(MatchGrain.squadra_raw).distinct(),
                    select(MatchGrain.avversario_raw).distinct(),
                )
            ).scalars()
        )
        # No `discard(None)`: both columns are `nullable=False` (`models/matches.py:118-119`),
        # so the union cannot return one, and mypy reads the set as `set[str]`.
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
