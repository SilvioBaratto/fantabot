"""The bridge from FantaLab's player UUIDs to fantacalcio ids.

**Why this is load-bearing rather than a convenience.** FantaLab identifies players
by its own UUID; everything else in this repository — `quotazioni`, `players`, the
value model, the legality matrix — is keyed by the integer id fantacalcio.it uses.
Without a translation the two halves cannot meet, and the failure is not subtle:
`asta-bid` folds the live sale ledger into `AstaState.owned`, `optimize_roster`
raises `InfeasibleRoster` for any owned id absent from the pool, and the command
therefore crashed on the first lot it won. It had been shipping that way.

The harvest side has always had the bridge — `aste/cli.py` reads a cached copy for
exactly this reason, and `asta_assignment.fantacalcio_id` is the result. The asta
engine never saw it.

`GET /v2/listone` is unauthenticated and returns the whole listone, `fantacalcio_id`
included, so this is an exact join rather than a fuzzy name match. The cache is a
plain file because a live room does not want an HTTP round trip it can avoid, and
because the mapping changes only when the platform adds a player.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

LISTONE_URL = "https://api.fantalab.it/v2/listone"

#: Where the harvest side already keeps its copy. Shared deliberately: two caches of
#: one mapping is two things to go stale.
DEFAULT_CACHE = Path("data/aste_live/listone_map.json")


def parse(payload: Mapping[str, Any]) -> dict[str, int]:
    """`uuid -> fantacalcio_id` from a listone response. Pure.

    Players the platform lists without a `fantacalcio_id` are omitted rather than
    mapped to `None`: a missing entry means "cannot be valued", which the caller must
    handle, while a `None` id would be a value the pool lookup silently fails on.
    """
    out: dict[str, int] = {}
    for player in payload.get("players", ()):
        uuid = player.get("player_id")
        fid = player.get("fantacalcio_id")
        if isinstance(uuid, str) and isinstance(fid, int):
            out[uuid] = fid
    return out


def from_cache(path: Path = DEFAULT_CACHE) -> dict[str, int]:
    """The mapping the harvest side has already fetched, or `{}`.

    Its file stores the whole listone entry per uuid; only the id is wanted here.
    """
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(raw, Mapping):
        return {}
    return {
        str(uuid): entry["fantacalcio_id"]
        for uuid, entry in raw.items()
        if isinstance(entry, Mapping) and isinstance(entry.get("fantacalcio_id"), int)
    }


def fetch(cache: Path | None = DEFAULT_CACHE, *, refresh: bool = False) -> dict[str, int]:
    """The mapping, from the cache if there is one and the network otherwise.

    The fetched payload is written to the cache in the shape the harvest side reads,
    so a refresh here serves `harvest load` too.
    """
    if cache is not None and not refresh:
        cached = from_cache(cache)
        if cached:
            return cached

    import httpx

    payload = httpx.get(LISTONE_URL, timeout=30).json()
    if cache is not None:
        entries = {
            p["player_id"]: {
                "fantacalcio_id": p.get("fantacalcio_id"),
                "name": p.get("name"),
                "team": p.get("team_name"),
                "role": p.get("role"),
                "mantra_roles": p.get("mantra_roles"),
            }
            for p in payload.get("players", ())
            if isinstance(p.get("player_id"), str)
        }
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(entries, ensure_ascii=False), encoding="utf-8")
    return parse(payload)
