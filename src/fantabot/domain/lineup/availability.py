"""Who the platform itself rules out, read off the lineup DTO's own row. Pure and total.

`teamLineup_read` marks a rostered player out with `status == 2`: seen on four players, two on
2026-09-22 (the golden fixture) and two on 2026-10-14, every one with `percent` 0 and a
`descr` naming an injury and a return date; the other 28 rows of each read carried `status` 1
with a `percent` between 20 and 90. The projection never read it, and a news reading from
before the injury kept one of them at p = 0.90 into the XI.

**Only that one value is read.** Another `status`, and `percent` as a probability, were never
measured, so neither is a verdict: `percent` beside `status` 1 is left exactly where it was, and
`status` has to be the integer 2 — not `2.0`, not `"2"`, not `True` — because a field that
changes type has changed meaning.

**This function never raises.** The planner calls it for every roster row on the default path,
whatever the model, so a row it cannot read must cost one player's flag and never the hourly
lineup.
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


@dataclass(frozen=True, slots=True)
class RuledOut:
    """A player the platform marked out."""

    #: The platform's own words (`descr`, else `comment`), on one line and at most
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
    if isinstance(status, bool) or not isinstance(status, int) or status != RULED_OUT_STATUS:
        return None
    return RuledOut(reason=_reason(row))


def _reason(row: Mapping[str, Any]) -> str:
    """`descr`, else `comment`, collapsed to one line and bounded; `""` for anything but text."""
    for key in ("descr", "comment"):
        value = row.get(key)
        if isinstance(value, str) and (text := " ".join(value.split())):
            return text if len(text) <= MAX_REASON else text[: MAX_REASON - 1].rstrip() + "…"
    return ""
