"""Every query against the three auction tables. Upserts only.

A killed collector is restarted, never repaired — the same rule the importers
follow, and here it is not a preference. On 2026-08-26 the collector was killed
eleven times in eight hours; each restart re-emitted the current state of every
auction it was watching. If a write could duplicate, the ladder reconstructed
from those rows would show phantom rungs.

Chunked, because an evening is 144,518 events and a single ``INSERT`` statement
must keep its parameter list inside Postgres's 65,535 bound.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from sqlalchemy.dialects.postgresql import insert

from fantabot.db.models.aste import Asta, AstaAssignment, AstaEvent
from fantabot.db.repositories._base import RepositoryBase

#: 8 columns x 4000 rows stays far inside Postgres's parameter bound.
CHUNK = 4000


def _chunks(rows: Sequence[dict[str, Any]]) -> list[Sequence[dict[str, Any]]]:
    return [rows[i : i + CHUNK] for i in range(0, len(rows), CHUNK)]


class AsteRepository(RepositoryBase):
    """Reads and writes for `asta`, `asta_event` and `asta_assignment`."""

    def upsert_auctions(self, rows: Sequence[dict[str, Any]]) -> int:
        """Register or refresh auction rooms.

        ``last_seen_at`` moves forward and ``first_seen_at`` does not: a rescan
        that finds an auction still running must not rewrite when we first met it.
        """
        if not rows:
            return 0
        written = 0
        for chunk in _chunks(rows):
            statement = insert(Asta).values(list(chunk))
            updatable = {
                c.name: statement.excluded[c.name]
                for c in Asta.__table__.columns
                if c.name not in {"id", "created_at", "first_seen_at"}
            }
            self.session.execute(
                statement.on_conflict_do_update(index_elements=["id"], set_=updatable)
            )
            written += len(chunk)
        return written

    def upsert_events(self, rows: Sequence[dict[str, Any]]) -> int:
        """Append observed states, absorbing the repeats a restart produces.

        The conflict target is the *partial* index, so the statement has to
        repeat its predicate — a bare ``ON CONFLICT (asta_id, last_update)``
        raises ``there is no unique or exclusion constraint matching the
        ON CONFLICT specification``. ``voti`` hit the same wall first.

        Rows without a ``last_update`` cannot conflict and are inserted plainly:
        there is no key on which to call them the same observation.
        """
        if not rows:
            return 0
        written = 0
        for chunk in _chunks(rows):
            statement = insert(AstaEvent).values(list(chunk))
            self.session.execute(
                statement.on_conflict_do_nothing(
                    index_elements=["asta_id", "last_update"],
                    index_where=AstaEvent.__table__.c.last_update.isnot(None),
                )
            )
            written += len(chunk)
        return written

    def upsert_assignments(self, rows: Sequence[dict[str, Any]]) -> int:
        """Write reconstructions, replacing any earlier one for the same sale.

        ``DO UPDATE`` rather than ``DO NOTHING``: the reconstruction is derived,
        so re-running a fixed reducer over the same events must be able to
        correct what a previous one got wrong.
        """
        if not rows:
            return 0
        written = 0
        for chunk in _chunks(rows):
            statement = insert(AstaAssignment).values(list(chunk))
            updatable = {
                c.name: statement.excluded[c.name]
                for c in AstaAssignment.__table__.columns
                if c.name not in {"asta_id", "player_uuid", "created_at"}
            }
            self.session.execute(
                statement.on_conflict_do_update(
                    index_elements=["asta_id", "player_uuid"], set_=updatable
                )
            )
            written += len(chunk)
        return written

    def known_player_ids(self) -> frozenset[int]:
        """Every id `players` actually holds.

        The backfill needs this to avoid a foreign-key violation on a player the
        listone knows and our reference table does not — which is not
        hypothetical: it sank a full load on 2026-08-27 over Konaté A.
        """
        from sqlalchemy import select

        from fantabot.db.models.reference import Player

        return frozenset(self.session.execute(select(Player.id)).scalars())

    def count_assignments(self, asta_type: str | None = None) -> int:
        """How many sales are stored, optionally for one format.

        The format is a filter here and nowhere upstream — that is the whole
        point of storing `asta_type` as a column.
        """
        from sqlalchemy import func, select

        statement = select(func.count()).select_from(AstaAssignment)
        if asta_type is not None:
            statement = statement.join(Asta, Asta.id == AstaAssignment.asta_id).where(
                Asta.asta_type == asta_type
            )
        return int(self.session.execute(statement).scalar_one())
