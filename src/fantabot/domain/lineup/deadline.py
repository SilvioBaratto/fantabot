"""Is the matchday past its posted timestamp? Pure.

Lived in `interface/lineup.py` until 3.2, when `application/lineup_submit.py` needed it —
and an application module may not import the command layer. It is a date predicate over a
string the platform sends, which is a domain fact and never was a presentation one.

**It warns and never blocks**, and the caller enforces that. `mstr` is not confirmed to be
the *lineup* deadline — only that it looks like kickoff — so the platform stays the
authority. A guess that blocked would lose a matchday to our own caution, which is the more
expensive of the two mistakes.
"""

from __future__ import annotations

from datetime import datetime


def is_past_deadline(mstr: str, now: datetime) -> bool:
    """Whether `now` is past the `mstr` timestamp. Pure. Both compared naive (mstr carries no
    zone; a warning does not need zone precision). Unparseable `mstr` is treated as not-past."""
    try:
        deadline = datetime.fromisoformat(mstr)
    except (ValueError, TypeError):
        return False
    return now.replace(tzinfo=None) > deadline.replace(tzinfo=None)


__all__ = ["is_past_deadline"]
