"""What we know about which auctions exist, and how a scan updates it. Pure.

Two shapes arrive here and leave as one. `GET /fantaleagues/live` returns
objects; the poller-era scan wrote lists, because it read the values straight
out of a page's React props. Normalising both at the edge means nothing
downstream has to know which era a row came from.

**A scan is not the whole truth, only the live part of it.** An auction absent
from one has ended — it has not stopped existing. Its events are already on
disk, unrepeatable, and the configuration here is what makes them
interpretable: without `num_credits` a price is a number without a scale, and
without `db_shard` the node cannot be addressed at all.

So merging adds and updates. It never removes.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any

from fantabot.aste.models import valid_shard

#: Field order of a seed row. Positional because the original file had no keys —
#: it was written straight from the page's props.
#:
#: ``asta_type`` is **last, and appended rather than inserted**. The poller-era
#: file has eleven fields and no format, so anything earlier in the tuple would
#: silently reinterpret every value in it. Appending keeps those rows readable
#: and lets new ones carry their own format.
SEED_FIELDS = (
    "auction_id",
    "db_shard",
    "num_teams",
    "num_credits",
    "min_player",
    "max_player",
    "asta_mode",
    "raise_mode",
    "counter_time",
    "counter_time_first",
    "name",
    "asta_type",
)


@dataclass(frozen=True, slots=True)
class AuctionConfig:
    """One auction room's settings, as its card describes them.

    Almost every field is optional because real cards carry nulls — an auction
    with no roster limit reports `min_player: null`. Refusing those would drop a
    live auction over a field that is legitimately absent.

    ``db_shard`` and ``asta_type`` are the two that are not: without the shard
    the node cannot be addressed, and ``asta_type`` is NOT NULL in the schema
    precisely so the format stays selectable rather than assumed.
    """

    auction_id: str
    db_shard: str
    asta_type: str
    name: str | None = None
    num_teams: int | None = None
    num_credits: int | None = None
    min_player: int | None = None
    max_player: int | None = None
    asta_mode: str | None = None
    raise_mode: str | None = None
    counter_time: int | None = None
    counter_time_first: int | None = None


def from_card(card: Mapping[str, Any]) -> AuctionConfig:
    """Build a config from a list card or an API row.

    ``db`` arrives as a number from the API and as a string from the props;
    stringified here so callers never have to ask which.
    """
    return AuctionConfig(
        auction_id=str(card["fantaleague_id"]),
        db_shard=valid_shard(card["db"]),
        asta_type=str(card["asta_type"]),
        name=card.get("fantaleague_name"),
        num_teams=card.get("num_teams"),
        num_credits=card.get("num_credits"),
        min_player=card.get("min_player"),
        max_player=card.get("max_player"),
        asta_mode=card.get("asta_mode"),
        raise_mode=card.get("raise_mode"),
        counter_time=card.get("counter_time"),
        counter_time_first=card.get("counter_time_first"),
    )


def from_seed_row(row: Sequence[Any], *, asta_type: str) -> AuctionConfig:
    """Build a config from a poller-era seed row.

    ``asta_type`` is a **fallback**, used only when the row does not carry one.
    The poller-era file predates storing the format because it only ever
    collected Mantra, so those rows need telling; rows written since carry their
    own and must ignore the argument.

    Getting that precedence backwards is not hypothetical: it labelled 185
    Classic auctions as Mantra, with the collection-time filter already removed.
    The format was being destroyed at persistence instead.
    """
    values = dict(zip(SEED_FIELDS, row, strict=False))
    return AuctionConfig(
        auction_id=str(values["auction_id"]),
        db_shard=valid_shard(values["db_shard"]),
        asta_type=str(values.get("asta_type") or asta_type),
        name=values.get("name"),
        num_teams=values.get("num_teams"),
        num_credits=values.get("num_credits"),
        min_player=values.get("min_player"),
        max_player=values.get("max_player"),
        asta_mode=values.get("asta_mode"),
        raise_mode=values.get("raise_mode"),
        counter_time=values.get("counter_time"),
        counter_time_first=values.get("counter_time_first"),
    )


def to_seed_rows(configs: Iterable[AuctionConfig]) -> list[list[Any]]:
    """Write configs back in the seed format the loaders already read."""
    return [[getattr(config, field) for field in SEED_FIELDS] for config in configs]


def merge(
    known: Iterable[AuctionConfig], scanned: Iterable[AuctionConfig]
) -> list[AuctionConfig]:
    """Everything we knew, plus everything just seen. Nothing is removed.

    Sorted by id so writing the result back produces a stable file: set order
    would make every rescan look like a rewrite to whoever reads the diff when
    something goes wrong.
    """
    registry = {config.auction_id: config for config in known}
    for config in scanned:
        previous = registry.get(config.auction_id)
        # A rescan is authoritative for what it reports and silent about what it
        # does not, so a field the new card left empty keeps its earlier value
        # rather than being blanked.
        registry[config.auction_id] = (
            config
            if previous is None
            else replace(
                previous,
                **{
                    field: value
                    for field, value in _vars_of(config).items()
                    if value is not None
                },
            )
        )
    return [registry[key] for key in sorted(registry)]


def _vars_of(config: AuctionConfig) -> dict[str, Any]:
    """Field values of a slotted dataclass, which has no ``__dict__``."""
    return {field: getattr(config, field) for field in AuctionConfig.__slots__}
