"""The three auction tables: the room, its frames, and what it sold.

The shape follows the pipeline rather than the wire. ``asta`` is the room's
configuration, read off its list card. ``asta_event`` is every observed state,
kept whole. ``asta_assignment`` is the interpretation, which can be rebuilt.

**Raw before interpreted, always.** ``asta_event.payload`` is ``jsonb`` holding
the state verbatim. Storing only the reconstruction would make a parser fix
require a re-collection, and an evening of auctions does not come back — the
2026-08-26 recording exists because nothing was thrown away at write time.

**``asta_type`` is a column, never a predicate.** The poller filtered Mantra at
collection and threw 85% of the population away. We play both formats
(``3584692`` Classic, ``4103937`` Mantra), so the format is data to be selected
on, not a decision baked into what gets stored.

**Identity is the platform's, not ours.** Auctions, teams and players are all
FantaLab UUIDs. ``fantacalcio_id`` is the bridge to ``players.id``, supplied
exactly by ``GET /v2/listone`` — no fuzzy name matching anywhere.
"""

from __future__ import annotations

from datetime import datetime
from typing import ClassVar

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    SmallInteger,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from fantabot.adapters.persistence.base import Base, TimestampMixin

#: The two formats FantaLab runs. Stored, not filtered on.
ASTA_TYPES: tuple[str, ...] = ("classic", "mantra")


class Asta(Base, TimestampMixin):
    """One auction room, as its list card describes it.

    Every field here comes from a single source — the card's React props — which
    is why the whole of the runtime-configuration table in
    ``docs/fantalab/00 §13`` is available per auction for free.

    ``db_shard`` is the piece without which none of the rest is reachable:
    auctions are spread across nineteen Firebase namespaces and the card is the
    only thing that says which.
    """

    __tablename__ = "asta"

    id: Mapped[str] = mapped_column(Text, primary_key=True)

    #: A surrogate, so `asta_event` can carry a 4-byte integer instead of 486,803
    #: copies of a 36-character UUID. `id` stays the primary key: it is the
    #: platform's own identifier, `asta_assignment`'s 43,298 rows point at it, and
    #: it is what a human matches against FantaLab. This is storage, not identity.
    key: Mapped[int] = mapped_column(BigInteger, Identity(always=False), unique=True)

    #: Lifted out of every event's payload. Measured strictly bijective with `id`
    #: over 486,803 rows and 224 auctions — no auction under two leagues, no league
    #: under two auctions — so on `asta_event` it was 486,803 copies of 224 things.
    fantaleague_id: Mapped[str | None] = mapped_column(Text)

    db_shard: Mapped[str] = mapped_column(Text, nullable=False)
    asta_type: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str | None] = mapped_column(Text)

    num_teams: Mapped[int | None] = mapped_column(Integer)
    num_credits: Mapped[int | None] = mapped_column(Integer)
    min_player: Mapped[int | None] = mapped_column(Integer)
    max_player: Mapped[int | None] = mapped_column(Integer)

    asta_mode: Mapped[str | None] = mapped_column(Text)
    raise_mode: Mapped[str | None] = mapped_column(Text)
    counter_time: Mapped[int | None] = mapped_column(Integer)
    counter_time_first: Mapped[int | None] = mapped_column(Integer)
    call_at_quotaz: Mapped[bool | None] = mapped_column(Boolean)

    first_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (Index("ix_asta_asta_type", "asta_type"),)

    def __repr__(self) -> str:
        return f"<Asta id={self.id[:8]} type={self.asta_type} {self.num_teams}x{self.num_credits}>"


class AstaEvent(Base):
    """One observed state of an auction node, kept verbatim.

    **Why a surrogate key.** The natural identity is ``(asta_key, last_update)``,
    but ``last_update`` is absent from a few states and Postgres forbids a
    nullable column in a primary key. ``voti`` solved the same problem the same
    way — a surrogate plus a partial unique index — and copying that precedent
    keeps one idea in the schema instead of two.

    The partial index is what makes the write an upsert: a restarted collector
    re-emits the current state of every auction it watches, and on 2026-08-26 it
    restarted eleven times. Those repeats carry the same ``last_update`` and must
    land on the same row.
    """

    __tablename__ = "asta_event"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    #: `TimestampMixin` is deliberately not used here, and this is the only model that
    #: opts out. It supplies `created_at` *and* `updated_at`, and `updated_at` differed
    #: from `created_at` on **0 of 486,803 rows** — this table is append-only, so the
    #: second timestamp was 3.8 MB saying what the first already said. `created_at`
    #: stays: its lag from `seen_at` runs 0.098 s to 12h29m, median 7h55m, so it
    #: records when the loader wrote a row as distinct from when the collector saw it,
    #: and nothing else in the schema holds that.
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    #: `asta.key`, not `asta.id`. The UUID was 36 characters repeated 486,803 times
    #: over 224 distinct auctions, in the heap and in both indexes. Writers still pass
    #: `asta_id`; `AsteRepository.upsert_events` resolves it, so nothing on the
    #: collection path knows this changed.
    asta_key: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("asta.key", ondelete="CASCADE"), nullable=False
    )
    last_update: Mapped[int | None] = mapped_column(BigInteger)
    seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    update_type: Mapped[str | None] = mapped_column(Text)

    #: Promoted out of `payload`. JSON keys were 38.6% of every payload's bytes, and
    #: these three are the ones present on 83–100% of rows with a single JSON type
    #: each. `last_update` and `update_type` were *already* columns and duplicated in
    #: the payload on every row with zero mismatches, so they are simply stripped;
    #: `fantaleague_id` moved to `asta`, being bijective with it.
    last_update_asta: Mapped[int | None] = mapped_column(BigInteger)
    last_bid_time: Mapped[int | None] = mapped_column(BigInteger)
    timer_in_sec: Mapped[int | None] = mapped_column(SmallInteger)

    #: Everything not promoted. Still verbatim, still the irreplaceable part.
    payload: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)

    __table_args__ = (
        Index(
            "uq_asta_event_asta_key_last_update",
            "asta_key",
            "last_update",
            unique=True,
            postgresql_where=last_update.isnot(None),
        ),
        Index("ix_asta_event_asta_key_seen_at", "asta_key", "seen_at"),
    )

    #: The keys this table promoted out of `payload`, and where each went. The
    #: reconstruction below is derived from it, so the two cannot drift.
    PROMOTED: ClassVar[dict[str, str]] = {
        "last_update_asta": "last_update_asta",
        "last_bid_time": "last_bid_time",
        "timer_in_sec": "timer_in_sec",
        "last_update": "last_update",
        "update_type": "update_type",
    }

    def full_payload(self, fantaleague_id: str | None = None) -> dict[str, object]:
        """The payload as it was collected, promoted keys folded back in.

        **This is why the split is safe rather than merely small.** A reconstruction
        that exists only inside a migration is one nobody can run afterwards, and
        `models/aste.py` already records that storing only the reconstruction would
        lose the thing worth keeping. `fantaleague_id` is passed in because it lives
        on `asta` now — one value per auction rather than one per event.
        """
        restored = dict(self.payload)
        for key, attribute in self.PROMOTED.items():
            value = getattr(self, attribute)
            if value is not None:
                restored[key] = value
        if fantaleague_id is not None:
            restored["fantaleague_id"] = fantaleague_id
        return restored

    def __repr__(self) -> str:
        return f"<AstaEvent asta={self.asta_key} {self.update_type} @{self.last_update}>"


class AstaAssignment(Base, TimestampMixin):
    """A player sold, and the bidding that got there.

    ``fantacalcio_id`` is **nullable on purpose**. On 2026-08-26, 2 of 407
    auctioned players — Macchioni and Konaté A. — were signings more recent than
    our last scrape. A NOT NULL column would have refused those rows and thrown
    away the prices, which is the one thing that cannot be recollected.

    ``ladder`` holds every rung with the team that pushed it. The clearing price
    alone is what polling already gave us; this column is the reason the phase
    exists, and what makes an opponent model fittable rather than invented.
    """

    __tablename__ = "asta_assignment"

    asta_id: Mapped[str] = mapped_column(
        Text, ForeignKey("asta.id", ondelete="CASCADE"), primary_key=True
    )
    player_uuid: Mapped[str] = mapped_column(Text, primary_key=True)

    fantacalcio_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("players.id"))
    price: Mapped[int] = mapped_column(Integer, nullable=False)
    buyer_team_id: Mapped[str | None] = mapped_column(Text)
    closed_at_ms: Mapped[int | None] = mapped_column(BigInteger)
    ladder: Mapped[list[dict[str, object]]] = mapped_column(JSONB, nullable=False)

    # No unique constraint on (asta_id, player_uuid): that pair *is* the primary
    # key. A first draft declared both, which `alembic check` caught as a
    # divergence — autogenerate rightly refuses to emit a constraint the key
    # already enforces.
    __table_args__ = (Index("ix_asta_assignment_fantacalcio_id", "fantacalcio_id"),)

    def __repr__(self) -> str:
        return f"<AstaAssignment asta={self.asta_id[:8]} player={self.player_uuid[:8]} {self.price}>"
