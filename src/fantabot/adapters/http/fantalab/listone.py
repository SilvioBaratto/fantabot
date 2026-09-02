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
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

LISTONE_URL = "https://api.fantalab.it/v2/listone"

#: Where the harvest side already keeps its copy. Shared deliberately: two caches of
#: one mapping is two things to go stale.
DEFAULT_CACHE = Path("data/aste_live/listone_map.json")

#: Bumped when the cache envelope's own shape changes, not when the listone data does — a
#: version check is for "can this reader understand the file," not "is the mapping current"
#: (that is what `fetched_at` answers). 1 is the first envelope; before it the file was a bare
#: `{uuid: entry}` dict with no metadata at all.
CACHE_VERSION = 1


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


def entries_only(raw: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    """The uuid -> entry pairs in a cache payload, the envelope's own metadata filtered out.

    `version`, `count`, `season` and `fetched_at` sit **alongside** the entries, not nested
    under a key of their own — a version number, a count, a season string and a timestamp are
    never themselves a `Mapping`, so the same structural check that already told a real
    listone entry from garbage tells one from the envelope beside it, without this function
    having to name the four keys and needing an edit every time the envelope grows a fifth.
    Every caller of a cache file this old routes through here — `from_cache` below, and
    `interface/harvest.py`'s two readers — so a version-1 file and a bare pre-version file
    both parse to the same shape.
    """
    return {str(uuid): entry for uuid, entry in raw.items() if isinstance(entry, Mapping)}


def _read_cache(path: Path) -> dict[str, Mapping[str, Any]]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(raw, Mapping):
        return {}
    return entries_only(raw)


def from_cache(path: Path = DEFAULT_CACHE) -> dict[str, int]:
    """The mapping the harvest side has already fetched, or `{}`.

    Its file stores the whole listone entry per uuid; only the id is wanted here.
    """
    return {
        uuid: entry["fantacalcio_id"]
        for uuid, entry in _read_cache(path).items()
        if isinstance(entry.get("fantacalcio_id"), int)
    }


def cache_version(path: Path = DEFAULT_CACHE) -> int | None:
    """The envelope version a cache file was written with, or `None` for a pre-version file
    (or one that does not exist / does not parse)."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    version = raw.get("version") if isinstance(raw, Mapping) else None
    return version if isinstance(version, int) else None


def cache_age(path: Path = DEFAULT_CACHE, *, now: datetime | None = None) -> float | None:
    """Seconds since the cache was fetched, or `None` when that cannot be known — the file is
    missing, unparseable, or predates the envelope (`fetched_at` did not exist yet).

    `now` is injectable so the operator-facing "cache is N hours old" line is testable without
    a real clock; the default reads one, because this is the adapter layer and a cache file's
    age is exactly the kind of fact `domain/` is not allowed to ask for itself.
    """
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    fetched_at = raw.get("fetched_at") if isinstance(raw, Mapping) else None
    if not isinstance(fetched_at, str):
        return None
    try:
        stamp = datetime.fromisoformat(fetched_at)
    except ValueError:
        return None
    return ((now or datetime.now(UTC)) - stamp).total_seconds()


def fetch(
    cache: Path | None = DEFAULT_CACHE,
    *,
    refresh: bool = False,
    transport: httpx.BaseTransport | None = None,
) -> dict[str, int]:
    """The mapping, from the cache if there is one and the network otherwise.

    The fetched payload is written to the cache in the shape the harvest side reads (plus the
    envelope: `version`, `count`, `season`, `fetched_at`, alongside the entries), so a refresh
    here serves `harvest load` too.

    `transport` is injectable so tests never construct a real client — the same seam
    `rest.fetch_league` already uses, and for the same reason: it keeps this in the
    socket-free default tier.
    """
    if cache is not None and not refresh:
        cached = from_cache(cache)
        if cached:
            return cached

    with httpx.Client(transport=transport) as client:
        payload = client.get(LISTONE_URL, timeout=30).json()
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
        envelope = {
            "version": CACHE_VERSION,
            "count": len(entries),
            "season": payload.get("season"),
            "fetched_at": datetime.now(UTC).isoformat(),
            **entries,
        }
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(envelope, ensure_ascii=False), encoding="utf-8")
    return parse(payload)
