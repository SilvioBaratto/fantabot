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
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from fantabot.adapters.persistence.base import Base
from fantabot.adapters.persistence.models.aste import (
    ASTA_TYPES,
    Asta,
    AstaAssignment,
    AstaEvent,
)
from fantabot.adapters.persistence.repositories._base import RepositoryBase

if TYPE_CHECKING:
    from sqlalchemy.engine import CursorResult


@dataclass(frozen=True)
class EventWrite:
    """What ``upsert_events`` actually did, split by what each outcome means.

    A count is returned rather than a bare total because the three outcomes are not
    interchangeable. ``already_present`` is the normal state of a re-read window and
    says the loader is working. ``unknown_auction`` says rows were **thrown away** —
    the events named an auction with no ``asta`` row, so there was no key to hang
    them on.

    This type exists because the method used to return ``len(chunk)`` regardless: a
    load could discard every row it was handed and still report a full write. On
    2026-09-05 that hid 2.1 million Classic events for over a week — the loader read
    1.31 GB to EOF, advanced its offset, reported success, and stored nothing. The
    method that says how much it wrote must be able to be wrong out loud.
    """

    inserted: int = 0
    already_present: int = 0
    unknown_auction: int = 0

    @property
    def offered(self) -> int:
        return self.inserted + self.already_present + self.unknown_auction

    @property
    def discarded(self) -> bool:
        """True when rows were dropped for want of an auction to attach them to.

        Deliberately not true for ``already_present``: absorbing a repeat is the
        design, and a caller that warned about it would cry wolf every pass.
        """
        return self.unknown_auction > 0

    def summary(self) -> str:
        return (
            f"{self.inserted} new · {self.already_present} already stored · "
            f"{self.unknown_auction} with no auction row"
        )

#: Postgres refuses a statement with more bind parameters than this.
PARAMETER_LIMIT = 65_535

#: Excluded from `recorded_auctions` by default: "è morto malen", the room `asta room` bid
#: real credits in on 2026-09-01. It is auction 19 of its own corpus once recorded, and its
#: clearing prices already carry this bot's bids — grading a fix against a room it partly
#: decided the outcome of is not independent evidence (SPEC §6 gap 4).
SELF_BID_ROOMS = frozenset({"0752a384-0611-4df4-8c95-2f8aaa38425c"})

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


@dataclass(frozen=True)
class CorpusRow:
    """What is stored for one format, from the room down to the sale the planner reads.

    Seven counts, and the last one is the point of the other six. `planner_sales` is what
    `clearing_sales` returns — the only number a plan is ever built on — and the six above
    it say *where* a shortfall happened: rooms registered but never followed, frames
    collected but never reduced, sales recorded with no buyer or no player link.

    The distinction is not academic. On 2026-09-05 the Classic corpus read as 2.1 million
    events and zero sales for over a week, and there was no screen that could show the
    difference between "nothing was collected" and "everything was collected and nothing
    joined". Every field defaults to zero so a format nobody has collected yet reports
    itself rather than disappearing from the answer.
    """

    asta_type: str
    rooms: int = 0
    rooms_with_events: int = 0
    events: int = 0
    assignments: int = 0
    assignments_with_buyer: int = 0
    assignments_with_player: int = 0
    planner_sales: int = 0


class AsteRepository(RepositoryBase):
    """Reads and writes for `asta`, `asta_event` and `asta_assignment`."""

    def corpus_summary(
        self, *, num_credits: int = 500, num_teams: int = 8
    ) -> list[CorpusRow]:
        """One `CorpusRow` per format, in `ASTA_TYPES` order. Read-only.

        **The last count must agree with `clearing_sales` exactly**, so it repeats that
        method's filter rather than approximating it: the buyer and the player link both
        present, and the room's own shape. A panel whose headline number is *nearly* the
        corpus is worse than no panel — it would have shown a plausible figure through the
        week the Classic corpus was actually empty.

        `num_credits`/`num_teams` are parameters for `read_plan_inputs`' reason: 8x500 is
        our room, and the next asta is the riparazione in January or a friend's league.

        **Four statements, not one, and the split is deliberate.** `rooms_with_events` is
        an `EXISTS` over `asta` (1,411 index probes on `ix_asta_event_asta_key_seen_at`)
        rather than a `count(DISTINCT asta_key)` folded into the event count, which would
        make the whole panel wait on a distinct aggregate over 3.5 million rows. The one
        unavoidable scan is the exact event count, and nothing else is queued behind it.
        """
        from sqlalchemy import exists, func

        rooms = {
            asta_type: total
            for asta_type, total in self.session.execute(
                select(Asta.asta_type, func.count()).group_by(Asta.asta_type)
            ).all()
        }
        with_events = {
            asta_type: total
            for asta_type, total in self.session.execute(
                select(Asta.asta_type, func.count())
                .where(exists().where(AstaEvent.asta_key == Asta.key))
                .group_by(Asta.asta_type)
            ).all()
        }
        events = {
            asta_type: total
            for asta_type, total in self.session.execute(
                select(Asta.asta_type, func.count())
                .join(AstaEvent, AstaEvent.asta_key == Asta.key)
                .group_by(Asta.asta_type)
            ).all()
        }
        sales: dict[str, tuple[int, int, int, int]] = {
            row[0]: (row[1], row[2], row[3], row[4])
            for row in self.session.execute(
                select(
                    Asta.asta_type,
                    func.count(),
                    func.count().filter(AstaAssignment.buyer_team_id.is_not(None)),
                    func.count().filter(AstaAssignment.fantacalcio_id.is_not(None)),
                    func.count().filter(
                        AstaAssignment.buyer_team_id.is_not(None),
                        AstaAssignment.fantacalcio_id.is_not(None),
                        Asta.num_credits == num_credits,
                        Asta.num_teams == num_teams,
                    ),
                )
                .join(AstaAssignment, AstaAssignment.asta_id == Asta.id)
                .group_by(Asta.asta_type)
            ).all()
        }

        return [
            CorpusRow(
                asta_type=asta_type,
                rooms=rooms.get(asta_type, 0),
                rooms_with_events=with_events.get(asta_type, 0),
                events=events.get(asta_type, 0),
                assignments=counted[0],
                assignments_with_buyer=counted[1],
                assignments_with_player=counted[2],
                planner_sales=counted[3],
            )
            for asta_type, counted in (
                (name, sales.get(name, (0, 0, 0, 0))) for name in ASTA_TYPES
            )
        ]

    def clearing_sales(
        self, *, asta_type: str = "mantra", budget: int = 500, num_teams: int = 8
    ) -> list[tuple[str, int]]:
        """`(player_id, price)` for every sale in auctions of a given format and shape.

        Restricted to one `asta_type` and the league shape (credits, team count) so the
        prices are directly comparable to ours without budget normalization; sales with no
        linked player (`fantacalcio_id IS NULL`) are dropped. Read-only.

        **The format was in the name and in the filter, and that cost a corpus.** This was
        `mantra_clearing_sales`, so `read_plan_inputs` guarded the call with
        `if listone == "mantra" ... else []` and a Classic run got no prices at all.
        Correct when written — no Classic asta had been recorded — and silently wrong from
        the moment one was, because an empty corpus is a legal input to `mean_prices`.

        What it cost, measured 2026-09-05 over the live 570-player Classic pool: with no
        prices the optimizer's budget constraint is vacuous, so the plan bought a 25-man
        roster for **25 credits of 500** and left 475 unspent, and 22 of its 25 slots
        differ from the plan the corpus produces. Every player also carried the
        `no_history` variance band (16.0 against 4.0), which flattens `lam`. The corpus
        itself is 32,100 Classic sales over 453 players in 259 rooms of our own 8x500
        shape, against 6,625 over 424 in 49 for Mantra: the larger one was unread.

        An unrecognised `asta_type` raises rather than returning `[]`. The column is free
        text, so a typo would otherwise reproduce exactly the silence above.

        **A row with no buyer is not a sale, and this used to return them.** The collector
        records every lot the room calls, and 12,544 of 43,298 assignment rows -- 29% --
        are lots that were called and never bid on: no `buyer_team_id`, one ladder rung,
        and that rung's `team_id` is `None`. They entered the mean at their opening price
        and dragged it toward zero. In our own league shape it was 3,076 rows of 9,559.

        The filter is on the buyer, not on the price. The platform will not sell a player
        for 0 credits -- the minimum bid is 1 -- so a row with no buyer is not a purchase
        whatever price it carries, and 364 of them carry a price above zero, being opening
        calls at a starting price. `price > 0` would have left those in.

        **The `ORDER BY` is load-bearing even though the reducer does not need it.**
        `prices.mean_prices` sums ints, so the mean is order-independent — but the golden
        harness pins a fixture captured from exactly these rows, and Postgres has no
        inherent order. Without a total order, re-capturing the fixture produces a diff
        indistinguishable from real drift. `(fantacalcio_id, price)` is total over the
        projected columns; `fantacalcio_id` alone is not, since a player sold in several
        auctions has several rows.
        """
        if asta_type not in ASTA_TYPES:
            raise ValueError(f"unknown asta_type {asta_type!r}; expected one of {ASTA_TYPES}")
        rows = self.session.execute(
            select(AstaAssignment.fantacalcio_id, AstaAssignment.price)
            .join(Asta, Asta.id == AstaAssignment.asta_id)
            .where(
                Asta.asta_type == asta_type,
                Asta.num_credits == budget,
                Asta.num_teams == num_teams,
                AstaAssignment.fantacalcio_id.is_not(None),
                AstaAssignment.buyer_team_id.is_not(None),
            )
            .order_by(AstaAssignment.fantacalcio_id, AstaAssignment.price)
        ).all()
        return [(str(fantacalcio_id), price) for fantacalcio_id, price in rows]

    def recorded_auctions(
        self,
        *,
        asta_type: str = "mantra",
        num_credits: int = 500,
        num_teams: int = 8,
        exclude: frozenset[str] = SELF_BID_ROOMS,
    ) -> list[tuple[str, list[tuple[str, int, int]]]]:
        """Every recorded auction of a shape, with its sales in closing order. Read-only.

        The calibration corpus: `(asta_id, [(fantacalcio_id, price, closed_at_ms), ...])`.
        Rows with no buyer are dropped for the same reason `mantra_clearing_sales` drops
        them — a lot the room called and nobody bid on is not a sale, and 29% of the
        assignment table is exactly that.

        `exclude` defaults to `SELF_BID_ROOMS` — a room this bot bid real credits in is not
        independent evidence for calibrating the thing that bid in it. Pass `frozenset()` to
        grade against the full corpus anyway (e.g. to see the room's own effect by comparison).

        Returned as plain tuples rather than as the application's `RecordedAuction`, so this
        module keeps knowing nothing about who reads it.
        """
        rows = self.session.execute(
            select(
                AstaAssignment.asta_id,
                AstaAssignment.fantacalcio_id,
                AstaAssignment.price,
                AstaAssignment.closed_at_ms,
            )
            .join(Asta, Asta.id == AstaAssignment.asta_id)
            .where(
                Asta.asta_type == asta_type,
                Asta.num_credits == num_credits,
                Asta.num_teams == num_teams,
                Asta.id.notin_(exclude),
                AstaAssignment.fantacalcio_id.is_not(None),
                AstaAssignment.buyer_team_id.is_not(None),
            )
            # Total, so the corpus is the same list on every run: `closed_at_ms` is null for
            # some rows and ties for others, and an unordered replay is a different evening.
            .order_by(
                AstaAssignment.asta_id,
                AstaAssignment.closed_at_ms.nulls_last(),
                AstaAssignment.fantacalcio_id,
            )
        ).all()

        by_auction: dict[str, list[tuple[str, int, int]]] = {}
        for asta_id, fantacalcio_id, price, closed_at_ms in rows:
            by_auction.setdefault(asta_id, []).append(
                (str(fantacalcio_id), price, closed_at_ms or 0)
            )
        return list(by_auction.items())

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

    def upsert_events(self, rows: Sequence[dict[str, Any]]) -> EventWrite:
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
            return EventWrite()

        keys = self._keys_for(sorted({str(r["asta_id"]) for r in rows}))
        translated = [
            {k: v for k, v in row.items() if k != "asta_id"} | {"asta_key": keys[str(row["asta_id"])]}
            for row in rows
            if str(row["asta_id"]) in keys
        ]
        # Counted, not merely skipped. These rows are gone: nothing downstream will
        # ever see them again, because the byte offset advances whether or not they
        # landed.
        unknown = len(rows) - len(translated)
        if not translated:
            return EventWrite(unknown_auction=unknown)

        inserted = 0
        for chunk in _chunks(translated, AstaEvent):
            statement = insert(AstaEvent).values(list(chunk))
            result = self.session.execute(
                statement.on_conflict_do_nothing(
                    index_elements=["asta_key", "last_update"],
                    index_where=AstaEvent.__table__.c.last_update.isnot(None),
                )
            )
            # `rowcount` after ON CONFLICT DO NOTHING is the number of rows that
            # actually went in — `len(chunk)` is what was offered, which is the
            # distinction this method used to lose. Narrowed rather than ignored,
            # like `LeagueTokenRepository.delete`: `Session.execute` is typed
            # `Result`, and only `CursorResult` carries a rowcount.
            inserted += int(cast("CursorResult[Any]", result).rowcount or 0)
        return EventWrite(
            inserted=inserted,
            already_present=len(translated) - inserted,
            unknown_auction=unknown,
        )

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

        from fantabot.adapters.persistence.models.reference import Player

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
