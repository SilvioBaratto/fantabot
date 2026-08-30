"""The match-grain chunking. No database — the batching is arithmetic.

Moved from ``tests/test_importers.py`` when ``upsert_two_passes`` moved out of
the seed package. The two-pass behaviour itself is exercised for real by every
``scrape_voti`` run and asserted by the coach-row counts in the integration
tier; what is pinned here is that no row is dropped or duplicated at a chunk
boundary.
"""

from __future__ import annotations

from fantabot.adapters.persistence.upserts import chunked


class TestChunking:
    def test_chunking_covers_every_row_exactly_once(self) -> None:
        rows = [{"n": i} for i in range(4501)]

        batches = list(chunked(rows, size=2000))

        assert [len(batch) for batch in batches] == [2000, 2000, 501]
        assert [row["n"] for batch in batches for row in batch] == list(range(4501))
