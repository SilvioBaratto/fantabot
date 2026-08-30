"""The own-room event feed: read the sale ledger, hand the engine ``AssignmentEvent``s.

The live advisory keys off the ``purchases/<fl>`` node — the authoritative sale record — not
``close_auction`` (``docs/fantalab/06-asta-write-path.md`` §10, and ``asta_engine.live``'s note).
This module is the thin I/O shell: read the ledger over unauthenticated HTTPS, convert with the
pure ``purchases_to_events``. The node read is injectable so the suite never opens a socket.

A continuous subscription belongs to the room loop; this is the one-shot ledger read the
advisory bootstraps from and re-reads each cycle. No ``fantabot.adapters.persistence`` import — the capture path
must survive a database outage.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fantabot.adapters.http.fantalab import rtdb
from fantabot.domain.asta.live import AssignmentEvent, purchases_to_events

#: The signature of a node reader — ``rtdb.read_snapshot`` in production, a fake in tests.
Reader = Callable[[int | None, str], "dict[str, Any] | None"]


def ledger_events(
    db: int | None,
    fantaleague_id: str,
    *,
    read: Reader = rtdb.read_snapshot,
) -> list[AssignmentEvent]:
    """Read the ``purchases/<fl>`` ledger once → the sales so far, in write order.

    ``read`` defaults to the real one-shot RTDB GET but is injected in tests, so this is
    exercised with no socket. An empty/absent ledger yields no events, not an error.
    """
    node = read(db, f"purchases/{fantaleague_id}")
    return purchases_to_events(node or {})


__all__ = ["Reader", "ledger_events"]
