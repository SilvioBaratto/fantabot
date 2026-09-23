"""What the hourly refresh still owes, and what it has already done. Pure.

The marker is a record per source: when it last succeeded, and for which matchday. A source
is **due** when it has no success for the matchday being planned — so a success is spent
once and a failure is retried next hour, which is the whole scheduling rule and the reason
it is four lines rather than a cron entry per source.

**Keyed by matchday, not by a clock.** "Once a day" would re-run on a Tuesday that needs
nothing and skip a Thursday postponement; the projection's unit of work is a giornata, and
that is what the marker counts. A `cmday` that goes *backwards* (the operator replanning an
earlier round) is due again, because the record does not match.

`SourceRecord.at` stays a string: it is written to JSON and read back for the freshness
verdict, which compares it against when the lega's previous round read `calculated`. Parsing
it here would put a clock's shape into a module that must not have one.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

#: The three sources the refresh can run, in the order it runs them.
SOURCES: tuple[str, ...] = ("voti", "lega", "news")

#: What one source did. `skipped` is neither: it did not run and does not owe a retry the
#: way a failure does — news with the setting off is the case it exists for.
SourceState = Literal["ok", "failed", "skipped"]


@dataclass(frozen=True, slots=True)
class SourceRecord:
    """One source's last success."""

    at: str
    cmday: int
    detail: str = ""


@dataclass(frozen=True, slots=True)
class SourceOutcome:
    """What one source did this run."""

    source: str
    state: SourceState
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.state == "ok"


@dataclass(frozen=True, slots=True)
class Marker:
    """Every source's last success, as the file holds it."""

    records: Mapping[str, SourceRecord]

    def succeeded(self, source: str, cmday: int) -> bool:
        record = self.records.get(source)
        return record is not None and record.cmday == cmday

    def with_success(self, source: str, *, cmday: int, at: str, detail: str = "") -> Marker:
        """A new marker recording this success. Frozen, so nothing shares a record."""
        return Marker({**self.records, source: SourceRecord(at=at, cmday=cmday, detail=detail)})


def due(marker: Marker, *, cmday: int, only: tuple[str, ...] = (), force: bool = False) -> tuple[str, ...]:
    """Which sources this run should attempt, in `SOURCES` order.

    `only` narrows to what the operator named; an unknown name selects nothing rather than
    everything, because "run just the voti" typed as `--source voto` must not silently
    become a full refresh against a live site.
    """
    wanted = tuple(s for s in SOURCES if not only or s in only)
    if force:
        return wanted
    return tuple(s for s in wanted if not marker.succeeded(s, cmday))


def voti_giornate(
    *, cmday: int, short: tuple[int, ...], previous_calculated: bool, cap: int
) -> tuple[int, ...]:
    """Which giornate the voti scrape asks for, ascending.

    `short` is `freshness.voti_range` — every giornata before this one still missing
    fixtures. **`cmday - 1` is added to it**, and that is not a detail: the projection plans
    `cmday` from the giornate before it, so the one it cannot do without is `cmday - 1`, and
    a giornata already holding its ten fixtures is *absent* from `short`. Asking only for
    `short` would mean the refresh never fetches the giornata it exists for, never records a
    voti success, and runs again every hour for ever.

    Nothing is asked for before the previous round reads `calculated`: voti published before
    then can still change (A20), so a scrape then is requests spent on rows that will be
    rewritten — and a success recorded against them would make the history read fresh when
    it is not.

    The cap is `freshness.VOTI_GETS_PER_RUN`, and `cmday - 1` is kept when it bites: a cold
    start with eight older gaps must not push the one giornata that matters out of the run.
    """
    if not previous_calculated or cmday < 2:
        return ()
    needed = cmday - 1
    rest = [g for g in sorted(set(short)) if g != needed and 1 <= g < cmday]
    return (needed, *rest[: max(cap - 1, 0)])


def voti_succeeded(counts: Mapping[int, int], *, cmday: int) -> bool:
    """Whether a voti run may be recorded as a success: rows landed for `cmday - 1`.

    Not "the command exited 0", and not "some rows landed". A scrape that fetched three
    older gaps and found the giornata we plan from still ungraded has done useful work and
    has *not* made the history fresh, so it is owed again next hour.
    """
    return counts.get(cmday - 1, 0) > 0
