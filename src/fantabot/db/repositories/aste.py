"""Every query against the three auction tables. Upserts only.

A killed collector is restarted, never repaired — the same rule every writer
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

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from fantabot.db.base import Base
from fantabot.db.models.aste import Asta, AstaAssignment, AstaEvent
from fantabot.db.repositories._base import RepositoryBase

#: Postgres refuses a statement with more bind parameters than this.
PARAMETER_LIMIT = 65_535

#: Never chunk larger than this regardless of width, so one statement stays a
#: reasonable unit of work and a failure costs a bounded amount of progress.
MAX_CHUNK = 4000


def chunk_size(model: type[Base]) -> int:
    """Rows per statement for ``model``.

    Derived rather than fixed. A single constant cannot be right for tables of
    different widths, and being wrong is silent until a batch grows: 4000 was
    justified by a column count that held for ``asta_event`` and not for
    ``asta``, whose 17 columns would have asked for 68,000 parameters against a
    65,535 bound.
    """
    columns = len(model.__table__.columns)
    return max(1, min(MAX_CHUNK, PARAMETER_LIMIT // columns - 1))


def _chunks(
    rows: Sequence[dict[str, Any]], model: type[Base]
) -> list[Sequence[dict[str, Any]]]:
    size = chunk_size(model)
    return [rows[i : i + size] for i in range(0, len(rows), size)]


class AsteRepository(RepositoryBase):
    """Reads and writes for `asta`, `asta_event` and `asta_assignment`."""

    def mantra_clearing_sales(
        self, *, budget: int = 500, num_teams: int = 8
    ) -> list[tuple[str, int]]:
        """`(player_id, price)` for every Mantra sale in auctions of a given shape.

        Restricted to `asta_type = 'mantra'` and the league shape (credits, team count)
        so the prices are directly comparable to ours without budget normalization; sales
        with no linked player (`fantacalcio_id IS NULL`) are dropped. Read-only.

        **The `ORDER BY` is load-bearing even though the reducer does not need it.**
        `prices.mean_prices` sums ints, so the mean is order-independent — but the golden
        harness pins a fixture captured from exactly these rows, and Postgres has no
        inherent order. Without a total order, re-capturing the fixture produces a diff
        indistinguishable from real drift. `(fantacalcio_id, price)` is total over the
        projected columns; `fantacalcio_id` alone is not, since a player sold in several
        auctions has several rows.
        """
        rows = self.session.execute(
            select(AstaAssignment.fantacalcio_id, AstaAssignment.price)
            .join(Asta, Asta.id == AstaAssignment.asta_id)
            .where(
                Asta.asta_type == "mantra",
                Asta.num_credits == budget,
                Asta.num_teams == num_teams,
                AstaAssignment.fantacalcio_id.is_not(None),
            )
            .order_by(AstaAssignment.fantacalcio_id, AstaAssignment.price)
        ).all()
        return [(str(fantacalcio_id), price) for fantacalcio_id, price in rows]

    def upsert_auctions(self, rows: Sequence[dict[str, Any]]) -> int:
        """Register or refresh auction rooms.

        ``last_seen_at`` moves forward and ``first_seen_at`` does not: a rescan
        that finds an auction still running must not rewrite when we first met it.

        **The exclusion set is an allowlist by omission, and that is a trap.** The
        ``SET`` clause is built by iterating the model, so *any column added to*
        ``Asta`` is enrolled automatically — and ``auction_rows`` supplies neither
        ``key`` nor ``fantaleague_id``, so a rescan would set both to NULL.
        ``harvest load`` calls this before ``upsert_events`` on every ten-second
        pass, so the damage would be continuous, silent and green: ``key``
        renumbered under the events pointing at it, ``fantaleague_id`` blanked on the
        row the payload reconstruction joins back to. Both are excluded, and
        ``test_re_registering_an_auction_keeps_its_key_and_its_league`` fails without
        it — verified by removing the exclusion and watching the key change.
        """
        if not rows:
            return 0
        written = 0
        for chunk in _chunks(rows, Asta):
            statement = insert(Asta).values(list(chunk))
            updatable = {
                c.name: statement.excluded[c.name]
                for c in Asta.__table__.columns
                if c.name not in {
                    "id",
                    "created_at",
                    "first_seen_at",
                    # Neither is in `auction_rows`; see the docstring.
                    "key",
                    "fantaleague_id",
                }
            }
            self.session.execute(
                statement.on_conflict_do_update(index_elements=["id"], set_=updatable)
            )
            written += len(chunk)
        return written

    def upsert_events(self, rows: Sequence[dict[str, Any]]) -> int:
        """Append observed states, absorbing the repeats a restart produces.

        The conflict target is the *partial* index, so the statement has to
        repeat its predicate — a bare ``ON CONFLICT (asta_key, last_update)``
        raises ``there is no unique or exclusion constraint matching the
        ON CONFLICT specification``. The match-grain tables hit the same wall first.

        Rows without a ``last_update`` cannot conflict and are inserted plainly:
        there is no key on which to call them the same observation.

        **Callers still pass ``asta_id``, the platform UUID, and that is deliberate.**
        The surrogate ``asta.key`` is a storage detail; ``aste/loader.py`` and
        ``aste/backfill.py`` know auctions by the id FantaLab gives them, and a test
        walks the capture modules' imports to prove none of them can reach the
        database at all. So the translation happens here, in the one place that is
        already talking to Postgres, and the collection path is unchanged.
        """
        if not rows:
            return 0

        keys = self._keys_for(sorted({str(r["asta_id"]) for r in rows}))
        translated = [
            {k: v for k, v in row.items() if k != "asta_id"} | {"asta_key": keys[str(row["asta_id"])]}
            for row in rows
            if str(row["asta_id"]) in keys
        ]
        if not translated:
            return 0

        written = 0
        for chunk in _chunks(translated, AstaEvent):
            statement = insert(AstaEvent).values(list(chunk))
            self.session.execute(
                statement.on_conflict_do_nothing(
                    index_elements=["asta_key", "last_update"],
                    index_where=AstaEvent.__table__.c.last_update.isnot(None),
                )
            )
            written += len(chunk)
        return written

    def _keys_for(self, asta_ids: Sequence[str]) -> dict[str, int]:
        """``asta.id`` -> ``asta.key`` for the auctions named, ids absent omitted.

        An unknown auction is dropped rather than raising, matching what the loader
        already does with events for auctions its seed has not heard of — and the
        loader counts those drops, which is why they are not silent.
        """
        if not asta_ids:
            return {}
        rows = self.session.execute(
            select(Asta.id, Asta.key).where(Asta.id.in_(list(asta_ids)))
        ).all()
        return {row.id: row.key for row in rows}

    def upsert_assignments(self, rows: Sequence[dict[str, Any]]) -> int:
        """Write reconstructions, replacing any earlier one for the same sale.

        ``DO UPDATE`` rather than ``DO NOTHING``: the reconstruction is derived,
        so re-running a fixed reducer over the same events must be able to
        correct what a previous one got wrong.
        """
        if not rows:
            return 0
        written = 0
        for chunk in _chunks(rows, AstaAssignment):
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
