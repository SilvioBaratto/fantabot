"""RTDB read (and, later, write) transport for our own room.

`docs/fantalab/06-asta-write-path.md` §4/§10: the ``auction/`` / ``assign/`` / ``purchases/``
nodes are readable — and participant bids writable — over plain HTTPS, on a **per-shard** host.
``shard_url`` resolves a room's ``db`` index to that host; ``read_snapshot`` is the one-shot GET
the advisory bootstraps from. The streaming subscription is wired in the feed (reusing ``aste``'s
socket internals); this module is the addressing + one-shot read.

Reads are unauthenticated (06 §10), and — like ``aste`` — nothing here imports ``fantabot.db``:
an outage must cost catch-up time, never a bid. ``test_fantalab_rtdb`` proves it structurally.
"""

from __future__ import annotations

from typing import Any

import httpx

#: Firebase's regional host; only the shard varies. Mirrors ``aste.stream.HOST``.
REGIONAL = "https://fantalab-{shard}.europe-west1.firebasedatabase.app"
#: The default namespace — where a room with ``db: null`` lives. **Not** shard 0.
DEFAULT = "https://fantalab-79eaa-default-rtdb.europe-west1.firebasedatabase.app"

DEFAULT_TIMEOUT = 15.0


def shard_url(db: int | None) -> str:
    """The Firebase host for a room's shard. ``None`` → the default namespace, not shard 0."""
    if db is None:
        return DEFAULT
    return REGIONAL.format(shard=db)


def node_url(db: int | None, path: str) -> str:
    """The full ``.json`` URL for a node path like ``auction/<fl>`` on a room's shard."""
    return f"{shard_url(db)}/{path.strip('/')}.json"


def read_snapshot(
    db: int | None,
    path: str,
    *,
    transport: httpx.BaseTransport | None = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> dict[str, Any] | None:
    """One-shot GET of a node → its live snapshot, or ``None`` if the node is empty/absent.

    Firebase returns ``null`` for a node that does not exist, so ``None`` and an empty mapping
    are kept distinct: "no lot on the block" is not the same as "a lot whose fields are unset".
    Unauthenticated; ``transport`` is injectable so tests never build a real one.
    """
    with httpx.Client(timeout=timeout, transport=transport) as client:
        response = client.get(node_url(db, path))
    response.raise_for_status()
    body = response.json()
    return body if isinstance(body, dict) else None


__all__ = ["DEFAULT", "REGIONAL", "node_url", "read_snapshot", "shard_url"]
