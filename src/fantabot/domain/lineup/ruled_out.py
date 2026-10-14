"""Who the platform itself rules out, read off the lineup DTO's own row. Pure and total.

`teamLineup_read` marks a rostered player with `status == 2`. **The platform does not document
that value; what it means was observed.** It showed on four players, two on 2026-09-22 (the
golden fixture) and two on 2026-10-14, every one with `percent` 0 and a `descr` naming an
injury and a return date, and on no one who was playing; the other 28 rows of each read carried
`status` 1 with a `percent` between 20 and 90. The projection never read it, and a news reading
from before the injury kept one of them at p = 0.90 into the XI.

**Only that one value is read.** Another `status`, and `percent` as a probability, were never
measured, so neither is a verdict: `percent` beside `status` 1 is left exactly where it was, and
`status` has to be the integer 2 — not `2.0`, not `"2"`, not `True` — because a field that
changes type has changed meaning.

**An observed meaning can stop holding, so there is a tripwire** (`implausible`): when more
than a quarter of the roster says "out" at once, the field has probably changed meaning and the
caller is expected not to act on it at all.

**This function never raises.** The planner calls it for every roster row on the default path,
whatever the model, so a row it cannot read must cost one player's flag and never the hourly
lineup. The reason it carries is the platform's free text and is cleaned before it is kept: it
reaches a terminal and a JSONL record.

The name avoids "availability", which already means the news dimension `disponibilita`: a
probability from the feed, and a different thing from this verdict.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

#: The one `status` value observed to mean the platform has ruled the player out.
RULED_OUT_STATUS = 2
#: The longest reason kept, ellipsis included: the platform's `descr` is free text, and it
#: ends up in a one-line note and in a JSON run record.
MAX_REASON = 100
#: The share of the priced roster past which `status` 2 is not read as injuries any more.
#: Both live reads showed 2 of 30 (7%), and a quarter is nearly four times that. The judgment,
#: not a measurement: a week that really has a quarter of the squad out at once is rarer than
#: the platform repurposing a value nobody documented. The two mistakes are not equal either:
#: skipping the guard is the behaviour the lineup had before it existed, while acting on a
#: misread field would bench and exclude a quarter of the team.
TRIPWIRE_SHARE = 0.25


@dataclass(frozen=True, slots=True)
class RuledOut:
    """A player the platform marked out."""

    #: The platform's own words (`descr`, else `comment`), on one line, printable, and at most
    #: `MAX_REASON` characters. Empty when the row stated none.
    reason: str


def ruled_out_from_row(row: Mapping[str, Any]) -> RuledOut | None:
    """The platform's verdict on one roster player, or `None` when it states none.

    Args:
        row: One `lineUpInfo` entry of a `teamLineup_read` response.

    Returns:
        `RuledOut` when `status` is the integer 2, whatever `percent` says. `None` for every
        other value, type or absence.
    """
    status = row.get("status")
    if not isinstance(status, int) or status != RULED_OUT_STATUS:
        return None
    return RuledOut(reason=_reason(row))


def implausible(ruled: int, priced: int) -> bool:
    """Whether so many of the roster say "out" that the field has probably changed meaning.

    Args:
        ruled: How many of the priced roster players carry `status` 2.
        priced: How many roster players the projection prices.

    Returns:
        True when `ruled` is more than `TRIPWIRE_SHARE` of `priced`. Exactly that share still
        passes, and an empty roster never trips it.
    """
    return ruled > TRIPWIRE_SHARE * priced


def _reason(row: Mapping[str, Any]) -> str:
    """`descr`, else `comment`, cleaned to one printable line and bounded; `""` otherwise."""
    for key in ("descr", "comment"):
        value = row.get(key)
        if isinstance(value, str) and (text := _one_line(value)):
            return text if len(text) <= MAX_REASON else text[: MAX_REASON - 1].rstrip() + "…"
    return ""


def _one_line(value: str) -> str:
    """Whitespace of every kind collapses to one space; any other unprintable character, which
    covers terminal escapes, zero-width and bidi marks and lone surrogates, is dropped.

    Dropped before the collapse, so a character removed between two spaces does not leave a
    double one behind.
    """
    kept = "".join(ch for ch in value if ch.isprintable() or ch.isspace())
    return " ".join(kept.split())
