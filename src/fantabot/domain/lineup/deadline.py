"""Is the matchday past its posted timestamp? Pure.

Lived in `interface/lineup.py` until 3.2, when `application/lineup_submit.py` needed it —
and an application module may not import the command layer. It is a date predicate over a
string the platform sends, which is a domain fact and never was a presentation one.

**It warns and never blocks**, and the caller enforces that. `mstr` is not confirmed to be
the *lineup* deadline — only that it looks like kickoff — so the platform stays the
authority. A guess that blocked would lose a matchday to our own caution, which is the more
expensive of the two mistakes.

**An unattended run is the exception, and `scheduled_cutoff` is its rule.** A `launchd` job has
nobody to read a warning, and the operator asked on 2026-09-11 for it never to reshuffle a
lineup once its matchday has started. Against the recorded Serie A kickoffs, `mstr` is two
hours early on both matchdays that can be checked — `16:30` for giornata 1's 18:30 first
kickoff (`docs/leghe-api.md`), `18:45` for giornata 3's 20:45 (`league_snapshot`). Summer time
cannot separate the two readings that fits: a lineup deadline two hours before kickoff, or the
kickoff itself in UTC (CEST is UTC+2). **Read as Italian wall-clock time, `mstr` is at or
before the real kickoff under both**, so that is the reading the cutoff uses. The cost if it is
UTC is stopping two hours early; the first matchday on CET settles it, a one-hour gap meaning
UTC.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


def is_past_deadline(mstr: str, now: datetime) -> bool:
    """Whether `now` is past the `mstr` timestamp. Pure. Both compared naive (mstr carries no
    zone; a warning does not need zone precision). Unparseable `mstr` is treated as not-past."""
    try:
        deadline = datetime.fromisoformat(mstr)
    except (ValueError, TypeError):
        return False
    return now.replace(tzinfo=None) > deadline.replace(tzinfo=None)


#: Why an unattended run refused. Stable strings: the run record stores them and the app reads
#: them back, so they are codes, not sentences — the sentence travels beside them.
MATCHDAY_STARTED = "matchday-started"
MATCHDAY_MISMATCH = "matchday-mismatch"
NO_START_TIME = "no-start-time"

#: The zone a zoneless `mstr` is read in. The platform is Italian and posts wall-clock times.
KICKOFF_ZONE = "Europe/Rome"


@dataclass(frozen=True, slots=True)
class Cutoff:
    """An unattended run's refusal: a stable `code`, and the `reason` a person reads."""

    code: str
    reason: str


def scheduled_cutoff(
    *, mstr: str, status_mday: int, plan_cmday: int, now: datetime
) -> Cutoff | None:
    """Why a scheduled run must not submit now, or `None` when it may. Pure: `now` is given.

    Three refusals, in this order:

    1. **The start is for another matchday.** `mstr` describes `league_status`'s own
       `mday`; the lineup read back is for its `cmday`. They part in the gap after the
       platform advances and before the lineup does — `mstr` then belongs to the *next*
       matchday, in the future, while the lineup is still the one in play. Acting on the next
       matchday's clock would reshuffle this one mid-matchday.
    2. **The start cannot be placed in time** — unreadable, or no zone data for Rome. Fail
       closed: nothing known is not permission, and the refusal lands in the run record,
       where the operator looks.
    3. **The matchday has started.** `now` at or past `mstr` read as Italian wall-clock time.

    A naive `now` is taken as this machine's local time (`astimezone`'s own rule), which is
    what `interface/lineup.py::_now` returns. The clock is not read here.
    """
    if status_mday != plan_cmday:
        return Cutoff(
            MATCHDAY_MISMATCH,
            f"the platform's current matchday is {status_mday} but the lineup read back is for "
            f"{plan_cmday} — its posted start ({mstr or 'none'}) is not this lineup's, so it "
            "cannot say this one is not already in play",
        )

    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

    try:
        rome = ZoneInfo(KICKOFF_ZONE)
    except ZoneInfoNotFoundError:
        return Cutoff(
            NO_START_TIME,
            f"no timezone data for {KICKOFF_ZONE} (install `tzdata`) — matchday "
            f"{status_mday}'s start cannot be placed in time",
        )
    try:
        start = datetime.fromisoformat(mstr)
    except (ValueError, TypeError):
        return Cutoff(
            NO_START_TIME,
            f"matchday {status_mday}'s start is unreadable ({mstr!r}) — nothing says the "
            "lineup is not already in play",
        )

    start = start.replace(tzinfo=rome) if start.tzinfo is None else start.astimezone(rome)
    if now.astimezone(rome) >= start:
        return Cutoff(
            MATCHDAY_STARTED,
            f"matchday {status_mday} started at {start:%Y-%m-%d %H:%M} (Rome) — a scheduled "
            "run does not touch a lineup in play",
        )
    return None


__all__ = [
    "KICKOFF_ZONE",
    "MATCHDAY_MISMATCH",
    "MATCHDAY_STARTED",
    "NO_START_TIME",
    "Cutoff",
    "is_past_deadline",
    "scheduled_cutoff",
]
