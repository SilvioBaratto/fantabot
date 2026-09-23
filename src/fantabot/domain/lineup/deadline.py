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
lineup once its matchday has started.

**`mstr` is the first kickoff, in UTC.** It was read conservatively as Italian wall-clock time
for a week, because summer time cannot tell that apart from "a deadline two hours before
kickoff". 2026-09-18 settled it four ways: giornate 1, 3 and 5 each sit exactly the CEST offset
behind their real first kickoff (`16:30`/18:30, `18:45`/20:45, `18:45`/20:45 Monza-Sassuolo); a
lineup **saved at 19:13 Rome was accepted**, so 18:45 Rome was never a deadline;
`league_snapshot.stopped` was `False` with the giornata-4 lineup open and `True` once giornata
5 had kicked off; and `league_status.sto` is that same lock flag.

The Rome reading was safe and cost two hours — it would have stopped the scheduled run at 18:45
Rome while the platform accepted saves until 20:45. That window is exactly where late team news
lands, which is why the operator asked for the last run to be close to kickoff.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta


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

#: Why the news step stood down. Same kind of stable code, for the same reason: the refresh
#: marker stores the detail and the operator reads it back an hour later.
NEWS_WINDOW_EARLY = "news-window-early"
NEWS_WINDOW_LATE = "news-window-late"

#: The zone the kickoff is *shown* in. `mstr` itself is UTC; this is only so the reason a
#: scheduled run gives reads like the clock on the operator's wall.
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
    2. **The start cannot be read.** Fail closed: nothing known is not permission, and the
       refusal lands in the run record, where the operator looks.
    3. **The matchday has started.** `now` at or past `mstr`, read as UTC.

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

    start = _kickoff(mstr)
    if start is None:
        return Cutoff(
            NO_START_TIME,
            f"matchday {status_mday}'s start is unreadable ({mstr!r}) — nothing says the "
            "lineup is not already in play",
        )
    if now.astimezone(UTC) >= start:
        return Cutoff(
            MATCHDAY_STARTED,
            f"matchday {status_mday} kicked off at {_on_the_wall(start)} — a scheduled run "
            "does not touch a lineup in play",
        )
    return None


def news_window_cutoff(
    *,
    mstr: str,
    status_mday: int,
    plan_cmday: int,
    now: datetime,
    opens_before: float = 6,
    closes_before: float = 2,
) -> Cutoff | None:
    """Why the news step must not run now, or `None` when the window is open. Pure (A13).

    The window runs from `opens_before` hours before the first kickoff up to but not
    including `closes_before` hours before it. Four refusals, in `scheduled_cutoff`'s order
    and for its reasons:

    1. **The start is for another matchday** — `mstr` describes `league_status`'s own `mday`.
    2. **The start cannot be read.** Fail closed, as everywhere on this path.
    3. **Too early**, and 4. **too late**.

    **This returns the reason and `in_news_window` is defined from it**, rather than the
    caller re-deriving why a `False` was false. Two guards on one question make the first
    unobservable, and the refresh has to print something an operator can act on: "opens
    2026-10-10 09:00 (Rome)" is a wait, "unreadable" is a bug.

    Why a window exists at all: the refresh marker spends one news success per giornata
    (`refresh.Marker.succeeded` matches on `cmday`), so a run outside the window does not
    merely waste an agent call — it consumes the one call this matchday had and suppresses
    the run in the hours when injuries and starters are actually known.
    """
    if not opens_before > closes_before >= 0:
        raise ValueError(
            f"the news window must open before it closes, and close by kickoff: opens "
            f"{opens_before}h and closes {closes_before}h before it"
        )
    if status_mday != plan_cmday:
        return Cutoff(
            MATCHDAY_MISMATCH,
            f"the platform's current matchday is {status_mday} but the refresh is for "
            f"{plan_cmday} — its posted start ({mstr or 'none'}) is not this matchday's",
        )
    start = _kickoff(mstr)
    if start is None:
        return Cutoff(
            NO_START_TIME,
            f"matchday {status_mday}'s start is unreadable ({mstr!r}) — nothing says the "
            "news window is open",
        )
    moment = now.astimezone(UTC)
    opens = start - timedelta(hours=opens_before)
    closes = start - timedelta(hours=closes_before)
    if moment < opens:
        return Cutoff(
            NEWS_WINDOW_EARLY,
            f"matchday {status_mday}'s news window opens {_on_the_wall(opens)}",
        )
    if moment >= closes:
        return Cutoff(
            NEWS_WINDOW_LATE,
            f"matchday {status_mday}'s news window closed {_on_the_wall(closes)}",
        )
    return None


def in_news_window(
    *,
    mstr: str,
    status_mday: int,
    plan_cmday: int,
    now: datetime,
    opens_before: float = 6,
    closes_before: float = 2,
) -> bool:
    """Whether `now` is in the news step's window (SPEC A13). Pure.

    The predicate half of `news_window_cutoff`, and derived from it so the two cannot drift.
    """
    return (
        news_window_cutoff(
            mstr=mstr,
            status_mday=status_mday,
            plan_cmday=plan_cmday,
            now=now,
            opens_before=opens_before,
            closes_before=closes_before,
        )
        is None
    )


def _kickoff(mstr: str) -> datetime | None:
    """`mstr` as an aware instant, or `None` when it cannot be read. Zoneless means UTC,
    which is what the platform posts; a zone it carries itself is taken at its word."""
    try:
        start = datetime.fromisoformat(mstr)
    except (ValueError, TypeError):
        return None
    return start.replace(tzinfo=UTC) if start.tzinfo is None else start


def _on_the_wall(start: datetime) -> str:
    """The kickoff as the operator reads a clock: Rome, or UTC when the zone is unavailable.

    Presentation only, so a missing `tzdata` costs a nicer string and never a refusal — the
    comparison above is in UTC either way.
    """
    try:
        from zoneinfo import ZoneInfo

        return f"{start.astimezone(ZoneInfo(KICKOFF_ZONE)):%Y-%m-%d %H:%M} (Rome)"
    except (ImportError, KeyError, OSError, ValueError):
        return f"{start:%Y-%m-%d %H:%M} (UTC)"


__all__ = [
    "KICKOFF_ZONE",
    "MATCHDAY_MISMATCH",
    "MATCHDAY_STARTED",
    "NEWS_WINDOW_EARLY",
    "NEWS_WINDOW_LATE",
    "NO_START_TIME",
    "Cutoff",
    "in_news_window",
    "is_past_deadline",
    "news_window_cutoff",
    "scheduled_cutoff",
]
