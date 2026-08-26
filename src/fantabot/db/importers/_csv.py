"""Parsing primitives shared by every CSV importer. Pure: no engine, no session.

**The decimal separator is not consistent across the ten source files**, and the
inconsistency is silent rather than loud. Measured on the data on disk:

===========================  ======  =====  =======================
file                         comma   dot    no-data marker
===========================  ======  =====  =======================
``statistiche_*.csv``        13222   0      ``"0,0"`` (2846 cells)
``voti.csv``                 102100  0      none
``qi_bias_*.csv``            0       all    none
``target_price_*.csv``       0       all    ``""`` (523 per file)
===========================  ======  =====  =======================

A single parser has to guess which convention it is looking at, and guessing
wrong does not raise: ``"38.46"`` with commas swapped for dots is still
``38.46``, and ``"38,46"`` read as a plain decimal is ``3846``. So there are two
functions, and each **refuses** the other's format. A hundredfold error becomes a
crash on the first row instead of a number nobody questions.

``scripts/`` currently defines three divergent copies of this logic
(``target_price.py:145``, ``analyze_low_minutes_bias.py:53``,
``join_qi_bias_performance.py:48``). They collapse into these in phase P11.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

# Empty means the row has no measurement; "0,0" is the scraper's explicit
# no-data marker. Both are absent rather than zero — SPEC criterion 9 requires
# media_voto to be NULL 2846 times and 0 never.
_ITALIAN_NO_DATA = frozenset({"", "0,0"})


def italian_decimal(raw: str) -> Decimal | None:
    """Parse a comma-decimal cell from ``statistiche_*.csv`` or ``voti.csv``.

    ``"6,25"`` becomes ``Decimal("6.25")``; ``""`` and ``"0,0"`` become ``None``.
    A dot-decimal raises rather than silently reading ``"38.46"`` as ``3846``.
    """
    value = raw.strip()
    if value in _ITALIAN_NO_DATA:
        return None
    if "." in value:
        raise ValueError(
            f"{value!r} uses a dot decimal separator; this column is comma-decimal. "
            "Use plain_decimal for qi_bias and target_price."
        )
    try:
        return Decimal(value.replace(",", "."))
    except InvalidOperation as exc:
        raise ValueError(f"{value!r} is not a comma-decimal number") from exc


def plain_decimal(raw: str) -> Decimal | None:
    """Parse a dot-decimal cell from ``qi_bias_*.csv`` or ``target_price_*.csv``.

    ``""`` becomes ``None``. ``"0.0"`` does **not** — unlike the Italian files
    these use a blank for no-data, so zero is a real measurement and
    ``pct_delta`` can legitimately be it. A comma-decimal raises.
    """
    value = raw.strip()
    if not value:
        return None
    if "," in value:
        raise ValueError(
            f"{value!r} uses a comma decimal separator; this column is dot-decimal. "
            "Use italian_decimal for statistiche and voti."
        )
    try:
        return Decimal(value)
    except InvalidOperation as exc:
        raise ValueError(f"{value!r} is not a dot-decimal number") from exc


def split_codes(raw: str) -> list[str]:
    """``"B;DS;E"`` becomes ``["B", "DS", "E"]``; empty becomes ``[]``.

    Upper-cased because the same codes appear in three casings across the
    project: ``quotazioni_mantra.csv`` stores ``DC``, ``mantra_schemi.json``
    stores ``Dc``, and ``rules/sistema-mantra.md`` uses lowercase. Classic shares
    this column and stores a single element.
    """
    return [part.strip().upper() for part in raw.split(";") if part.strip()]


def split_flags(raw: str) -> list[str]:
    """``"floor_qi;team_discount(MIL)"`` -> both, **case preserved**.

    Separate from ``split_codes`` on purpose. Role codes are normalised to
    upper case because three files write them three ways; flags are opaque
    strings produced by ``scripts/target_price.py`` and upper-casing them would
    change ``team_discount(MIL)`` into something that no longer matches the
    script that emits it.
    """
    return [part.strip() for part in raw.split(";") if part.strip()]
