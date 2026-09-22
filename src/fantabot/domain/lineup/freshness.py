"""Is the history fresh enough to project on, and which voti to fetch? Pure. SPEC A20.

The projection plans giornata `cmday` from the voti of every giornata before it, so it is
**fresh** when two things hold:

* **the history reaches the previous giornata**: `max(giornata) >= cmday - 1`; and
* **those voti are final**: the refresh marker records a voti success scraped *after* the
  lega's previous round read `calculated` — voti published before then can still change.
  The caller reads that from the marker; `previous_round_calculated` is its calendar half.

A giornata short of its 10 matches is a **warning**, named in the run record (`g16 6/10
(postponed?)`), and **never** a fallback. 2025/26 giornata 16 had 6 of 10 when giornata 17
opened, the other four played on 14-15 January; refusing to project for a month over four
matches would throw away a season's worth of the evidence in hand.

`voti_range` is what the refresh re-fetches: every giornata before this one still short of
10 matches, oldest first, at most 8 per run so a cold start does not stall an hourly job.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from fantabot.domain.lega.models import Fixture as LegaFixture

#: Matches in a Serie A giornata.
SERIE_A_MATCHES = 10
#: Giornate the voti refresh fetches in one run.
VOTI_GETS_PER_RUN = 8


@dataclass(frozen=True, slots=True)
class Freshness:
    fresh: bool
    #: Why it is stale; empty when fresh.
    reasons: tuple[str, ...]
    #: Short giornate, fresh or not: `g16 6/10 (postponed?)`.
    warnings: tuple[str, ...]


def previous_round_calculated(calendar: Iterable[LegaFixture], *, cmday: int) -> bool:
    """Whether the lega's round on Serie A giornata `cmday - 1` reads calculated.

    False when the lega has no round there — its first matchday comes after Serie A's —
    because nothing on the calendar can then vouch for those voti: fail closed.
    """
    flags = [f.calculated for f in calendar if f.championship_matchday == cmday - 1]
    return bool(flags) and all(flags)


def staleness(
    *,
    max_giornata: int,
    cmday: int,
    fixtures_per_giornata: Mapping[int, int],
    voti_refreshed: bool,
) -> Freshness:
    """The verdict on this season's history for planning giornata `cmday`."""
    previous = cmday - 1
    reasons: list[str] = []
    if max_giornata < previous:
        reasons.append(
            f"the voti end at g{max_giornata}; planning g{cmday} needs g{previous}"
        )
    if not voti_refreshed:
        reasons.append(
            f"no voti refresh recorded after g{previous} was calculated: its voti may still "
            "change"
        )
    warnings = tuple(
        f"g{g} {fixtures_per_giornata.get(g, 0)}/{SERIE_A_MATCHES} (postponed?)"
        for g in _short(cmday, fixtures_per_giornata)
    )
    return Freshness(fresh=not reasons, reasons=tuple(reasons), warnings=warnings)


def voti_range(*, cmday: int, fixtures_per_giornata: Mapping[int, int]) -> list[int]:
    """The giornate to re-fetch: short ones before `cmday`, oldest first, capped per run."""
    return _short(cmday, fixtures_per_giornata)[:VOTI_GETS_PER_RUN]


def _short(cmday: int, fixtures_per_giornata: Mapping[int, int]) -> list[int]:
    return [
        g for g in range(1, cmday) if fixtures_per_giornata.get(g, 0) < SERIE_A_MATCHES
    ]
