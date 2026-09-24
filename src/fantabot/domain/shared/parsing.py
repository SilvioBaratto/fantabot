"""Parsing primitives for the scraped site data. Pure: no engine, no session.

**The decimal separator is not consistent across the site's pages**, and the
inconsistency is silent rather than loud. Measured on the 2026-08-19 capture of
the ten source files, which is where these two conventions were first pinned
down:

===========================  ======  =====  =======================
source                       comma   dot    no-data marker
===========================  ======  =====  =======================
statistiche pages            13222   0      ``"0,0"`` (2846 cells)
voti pages                   102100  0      none
qi_bias (derived)            0       all    none
target_price (derived)       0       all    ``""`` (523 per listone)
===========================  ======  =====  =======================

A single parser has to guess which convention it is looking at, and guessing
wrong does not raise: ``"38.46"`` with commas swapped for dots is still
``38.46``, and ``"38,46"`` read as a plain decimal is ``3846``. So
``italian_decimal`` **refuses** a dot outright rather than parsing it: a
hundredfold error becomes a crash on the first row instead of a number nobody
questions.

Only the comma convention is still parsed here. A ``plain_decimal`` twin covered
the two derived dot-decimal columns and was deleted on 2026-09-24 — ``qi_bias``
and ``target_price`` were CSV-importer columns, and the importers are gone
(``application/pricing.py`` computes those values rather than reading them back).
The dot refusal below is **not** about that twin and does not go with it: it is
about the site's own two conventions, which is why the table above is kept.

The scrapers write straight to Postgres, so this runs at scrape time now rather
than at import time. The rules are unchanged: they are facts about how the site
renders numbers, not about how a file was stored.
"""

from __future__ import annotations

from datetime import date, datetime, time
from decimal import Decimal, InvalidOperation

# Empty means the row has no measurement; "0,0" is the site's explicit no-data
# marker. Both are absent rather than zero — media_voto must be NULL 2846 times
# and 0 never.
_ITALIAN_NO_DATA = frozenset({"", "0,0"})


def italian_decimal(raw: str) -> Decimal | None:
    """Parse a comma-decimal cell from the statistiche or voti pages.

    ``"6,25"`` becomes ``Decimal("6.25")``; ``""`` and ``"0,0"`` become ``None``.
    A dot-decimal raises rather than silently reading ``"38.46"`` as ``3846``.
    """
    value = raw.strip()
    if value in _ITALIAN_NO_DATA:
        return None
    if "." in value:
        raise ValueError(
            f"{value!r} uses a dot decimal separator; this column is comma-decimal. "
            "The statistiche and voti pages render every number with a comma, so a dot "
            "here means the wrong column is being parsed."
        )
    try:
        return Decimal(value.replace(",", "."))
    except InvalidOperation as exc:
        raise ValueError(f"{value!r} is not a comma-decimal number") from exc



def split_codes(raw: str) -> list[str]:
    """``"B;DS;E"`` becomes ``["B", "DS", "E"]``; empty becomes ``[]``.

    Upper-cased because the same codes appear in three casings across the
    project: the Mantra listone stores ``DC``, ``mantra_schemi.json``
    stores ``Dc``, and ``rules/sistema-mantra.md`` uses lowercase. Classic shares
    this column and stores a single element.
    """
    return [part.strip().upper() for part in raw.split(";") if part.strip()]


def split_flags(raw: str) -> list[str]:
    """``"floor_qi;team_discount(MIL)"`` -> both, **case preserved**.

    Separate from ``split_codes`` on purpose. Role codes are normalised to
    upper case because three sources write them three ways; flags are opaque
    strings produced by ``application/pricing.py`` — ``scripts/target_price.py``
    when this was written — and upper-casing them would change
    ``team_discount(MIL)`` into something that no longer matches the code that
    emits it.
    """
    return [part.strip() for part in raw.split(";") if part.strip()]


def parse_date(raw: str) -> date:
    """``"01/02/2025"`` -> ``date(2025, 2, 1)``. Italian order, not American.

    Read the American way, every match in the first twelve days of a month is
    silently misfiled — 01/02 becomes January 2nd — and nothing raises.
    """
    return datetime.strptime(raw.strip(), "%d/%m/%Y").date()


def parse_time(raw: str) -> time | None:
    """``"12:30"`` -> ``time(12, 30)``; empty -> ``None``.

    bonus_malus has no kick-off time at all, and voti has one for every row.
    """
    value = raw.strip()
    return datetime.strptime(value, "%H:%M").time() if value else None
