"""Mantra role codes, and the drift between the frozen tag and reality.

``rules/sistema-mantra.md``: roles are assigned around late July and *not
revisited for the rest of the year* — the page says so outright, and admits that
"a team's or player's tactical role can evolve mid-season without the platform's
role tag following along".

So ``quotazioni_mantra.csv`` is guaranteed to drift, and it will never
self-correct. A W-tagged player six weeks into playing as a T is still W in every
file we own, and every Mantra lineup built from that tag is wrong, silently.

The model reports what recent coverage says he is playing as. This module decides
what that means. The model is never asked whether the tag is stale — it does not
know what tag we hold, and asking it to guess would invent an answer.
"""

from __future__ import annotations

from collections.abc import Iterable

# Twelve, not eleven. rules/sistema-mantra.md heads its table "Roles (11 codes)"
# and then lists twelve rows, repeating all twelve in its closing line. The table
# is right and the heading is a typo.
MANTRA_CODES: frozenset[str] = frozenset(
    {"POR", "DC", "B", "DD", "DS", "E", "M", "C", "T", "W", "A", "PC"}
)


class UnknownRoleCode(ValueError):
    """A role code that is not one of the twelve Mantra codes."""


def parse_codes(raw: str) -> frozenset[str]:
    """Parse the CSV's ``;``-joined multi-role form into a set of known codes.

    Accepts the uppercase form the quotazioni CSVs store (``"DD;DC"``) and the
    lowercase form the rules doc uses (``"dd;dc"``). An unrecognised code raises:
    scoring it as "no drift" would hide a broken join behind a column of zeroes.
    """
    return _normalize(part for part in raw.split(";"))


def drift(observed: Iterable[str], tagged: str, confidenza: float) -> float:
    """How stale the platform's role tag looks, on the model's own confidence.

    ``0.0`` when the observation is empty or already covered by the tag; otherwise
    ``confidenza`` — how sure we are the tag is stale is exactly how sure the model
    was of the reporting behind it.

    An empty ``observed`` is **not** confirmation that the tag still holds. It
    means the sources were silent about his position, which is a different fact
    and must not be recorded as agreement.
    """
    seen = _normalize(observed)
    if not seen:
        return 0.0
    return 0.0 if seen <= parse_codes(tagged) else confidenza


def _normalize(codes: Iterable[str]) -> frozenset[str]:
    cleaned = {code.strip().upper() for code in codes if code.strip()}
    unknown = sorted(cleaned - MANTRA_CODES)
    if unknown:
        raise UnknownRoleCode(f"not Mantra role codes: {', '.join(unknown)}")
    return frozenset(cleaned)
