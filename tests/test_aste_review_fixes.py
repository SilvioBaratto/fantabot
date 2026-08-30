"""Three findings from the 2026-08-27 review, each pinned by a test.

Grouped in one file because they were found in one pass and share no code; each
would otherwise be a two-test module.
"""

from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import urlparse

import pytest

from fantabot.adapters.http.harvest.stream import stream_url
from fantabot.adapters.persistence.models.aste import Asta, AstaAssignment, AstaEvent
from fantabot.adapters.persistence.repositories.aste import PARAMETER_LIMIT, chunk_size
from fantabot.domain.harvest.models import ShardError, valid_shard
from fantabot.domain.harvest.registry import from_card, from_seed_row

# --- 1. SSRF: the shard reaches a URL and comes from untrusted content -----

HOSTILE = ["evil.com#", "evil.com/a?", "../../x", "18.evil.com", "", "18 ", "-1"]


@pytest.mark.parametrize("shard", HOSTILE)
def test_a_hostile_shard_never_reaches_a_url(shard: str) -> None:
    """`shard` is read off an auction card — observed content, not ours. A `#`
    truncates the rest of the template and the connection lands on an attacker's
    host: `evil.com#` produced `fantalab-evil.com` before this check existed."""
    with pytest.raises(ShardError):
        stream_url("auction-id", shard)


@pytest.mark.parametrize("shard", HOSTILE)
def test_a_hostile_shard_is_refused_at_the_card_boundary(shard: str) -> None:
    """Refused where it enters, not only where it is used, so a bad value cannot
    sit in the registry waiting for a code path that forgot to check."""
    with pytest.raises(ShardError):
        from_card({"fantaleague_id": "a", "db": shard, "asta_type": "mantra"})


@pytest.mark.parametrize("shard", ["0", "4", "18", "19", 15])
def test_real_shards_are_accepted(shard: object) -> None:
    assert valid_shard(shard) == str(shard)


def test_an_accepted_shard_stays_inside_the_firebase_domain() -> None:
    host = urlparse(stream_url("auction-id", "18")).netloc
    assert host.endswith(".europe-west1.firebasedatabase.app")
    assert host.startswith("fantalab-")


# --- 2. Chunking must respect Postgres's parameter bound -------------------


@pytest.mark.parametrize("model", [Asta, AstaEvent, AstaAssignment])
def test_a_chunk_never_exceeds_the_parameter_limit(model: type) -> None:
    """The original constant was 4000 for every table, justified by a column
    count that only held for `asta_event`. `asta` has 17 columns: 68,000
    parameters against a 65,535 bound. Latent only because no batch has yet
    carried 4,000 auctions."""
    columns = len(model.__table__.columns)
    assert chunk_size(model) * columns <= PARAMETER_LIMIT


def test_chunking_is_derived_rather_than_assumed() -> None:
    """A wide table gets a smaller chunk. A single constant cannot be right for
    tables of different widths, and being wrong is silent until the batch is big."""
    assert chunk_size(Asta) < chunk_size(AstaEvent)


# --- 3. One seed parser, not two ------------------------------------------


def test_the_backfill_and_the_registry_read_a_seed_identically() -> None:
    """Two independent parsers of one positional format drift silently. The
    backfill used index constants and `entry[-1]`; the registry used a field
    tuple. Same file, two readings."""
    from fantabot.domain.harvest.backfill import auction_rows

    seed = json.loads(
        Path("data/aste_live/seed_2026-08-26.json").read_text(encoding="utf-8")
    )
    rows = auction_rows(seed, "mantra")
    configs = [from_seed_row(entry, asta_type="mantra") for entry in seed]

    assert len(rows) == len(configs)
    for row, config in zip(rows, configs, strict=True):
        assert row["id"] == config.auction_id
        assert row["db_shard"] == config.db_shard
        assert row["name"] == config.name
        assert row["num_credits"] == config.num_credits
        assert row["counter_time_first"] == config.counter_time_first
