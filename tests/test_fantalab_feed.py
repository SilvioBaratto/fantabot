"""The own-room ledger feed, with an injected node reader. **Zero sockets.**

``ledger_events`` is the live path's bootstrap: read ``purchases/<fl>`` once, hand the engine
the same ``AssignmentEvent``s a replay produces. The reader is injected, so this is exercised
without opening a socket — the whole point of keeping the transport behind a seam.
"""

from __future__ import annotations

from typing import Any

from fantabot.asta_engine.live import AssignmentEvent
from fantabot.fantalab import feed


def _fake_reader(node: dict[str, Any] | None) -> feed.Reader:
    def read(db: int | None, path: str) -> dict[str, Any] | None:
        assert path.startswith("purchases/")
        return node

    return read


def test_ledger_events_reads_and_converts() -> None:
    ledger = {
        "p1": {"player_id": "a", "price": 30, "fantateam_id": "t1", "created_at": 100},
        "p2": {"player_id": "b", "price": 0, "created_at": 200},  # unsold skip
    }
    events = feed.ledger_events(9, "L", read=_fake_reader(ledger))
    assert events == [
        AssignmentEvent("a", 30, "t1"),
        AssignmentEvent("b", 0, None),
    ]


def test_ledger_events_empty_ledger_is_no_events() -> None:
    assert feed.ledger_events(9, "L", read=_fake_reader(None)) == []
    assert feed.ledger_events(9, "L", read=_fake_reader({})) == []
