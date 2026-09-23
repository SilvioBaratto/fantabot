"""Module code -> ordered slot role-sets, GK first. The shape the builder lays `starts[]` into.

The platform sends `mdl` as a dashless code (`"343"`) and `starts[]` as 11 ids, and it judges
`starts[i]` against **its own** slot i. The order is read from `mantra_starts_order.json`,
which pins the platform's `S.schemes.mantra` verbatim (SPEC A22). It is deliberately not
`mantra_schemi.json`'s slot order: that is the PDF table's rows, and laying `starts[]` out in
it drew `LUP009` on 7 of 11 modules and could have taken a `-1` on the rest without a word.
The gate `mantra.gates.check_starts_order` holds the file to a permutation of the schemi.

Reading the packaged JSON is the same "thin data-load edge" `asta.legality.load_compat` uses
— package data, deterministic, no database and no network — so it stays inside `domain`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache

from fantabot.domain.asta.legality import SlotRule, build_legality, load_compat
from fantabot.domain.asta.roles import normalize_role
from fantabot.domain.classic.formations import FORMATIONS
from fantabot.domain.shared.resources import STARTS_ORDER_FILENAME, data_dir

#: The Classic goalkeeper role — its `starts[0]`, the counterpart to Mantra's `POR`.
CLASSIC_GK_ROLE = "P"


@lru_cache(maxsize=1)
def _by_code() -> dict[str, tuple[frozenset[str], ...]]:
    raw = json.loads((data_dir() / STARTS_ORDER_FILENAME).read_text(encoding="utf-8"))
    table: dict[str, tuple[frozenset[str], ...]] = {}
    for entry in raw["moduli"]:
        code = str(entry["nome"]).replace("-", "")
        table[code] = tuple(
            frozenset(normalize_role(role) for role in label.split("/"))
            for label in entry["order"]
        )
    return table


def modules() -> frozenset[str]:
    """The dashless codes of every known Mantra schema (the 11 the platform allows)."""
    return frozenset(_by_code())


def slots(module_code: str) -> tuple[frozenset[str], ...]:
    """The 11 ordered slot role-sets for a Mantra module, GK first — the platform's order.

    Raises `ValueError` for a code that is not one of the 11, rather than returning an empty
    schema that would silently accept any assignment.
    """
    try:
        return _by_code()[module_code]
    except KeyError:
        raise ValueError(f"unknown module code: {module_code!r}") from None


def classic_slots(module_code: str) -> tuple[frozenset[str], ...]:
    """The 11 ordered slot role-sets for a Classic formation, GK first — `starts[]` order.

    The Classic counterpart to `slots`: a formation is a per-role **count** (`352` = 3 D, 5 C,
    2 A) over single-role buckets, so each slot admits exactly one macro role. Dispatched by
    format at the builder rather than merged with the Mantra table — the codes `343`/`352`/…
    exist in **both** and keying by code alone would silently return the Mantra schema.
    """
    try:
        counts = FORMATIONS[module_code]
    except KeyError:
        raise ValueError(f"unknown Classic formation code: {module_code!r}") from None
    ordered: list[frozenset[str]] = [frozenset({CLASSIC_GK_ROLE})]
    for role in ("D", "C", "A"):
        ordered.extend(frozenset({role}) for _ in range(counts[role]))
    return tuple(ordered)


@dataclass(frozen=True, slots=True)
class SlotAdmission:
    """What one `starts[]` slot admits, split by how much it costs.

    `natural` is the slot's "ok" cells — exactly what `slots` returns. `submission` adds the
    `-1` cells, which the platform accepts at submission and scores with a malus.
    `substitution` adds the `-1*` cells on top, which it refuses at submission and allows
    only as the outcome of a forced substitution.
    """

    natural: frozenset[str]
    submission: frozenset[str]
    substitution: frozenset[str]


@lru_cache(maxsize=1)
def _admissions_by_code() -> dict[str, tuple[SlotAdmission, ...]]:
    """`mantra_compat.json`'s rows re-laid into `mantra_starts_order.json`'s order.

    The two files are two views of the same 11 schemi: the compat matrix is the published
    PDF's row order, and the starts order is the platform's own `starts[]` order (A22). The
    substitution engine judges the player it is about to place at **the platform's** slot i,
    so the compat row has to be fetched by that index and not by the PDF's.

    They are matched by **role set**, not by label. 4-2-3-1 is the one module where the two
    files spell the same slot differently — `T/W` in the starts order against `W/T` in the
    compat matrix — so a `str` key silently drops a slot on exactly one of the eleven, and
    the one it drops is the module whose `T` slot carries the `-1*` W cell. Measured
    2026-09-23; `tests/domain/lineup/test_schema.py` holds both files to the agreement.
    """
    legality = build_legality(load_compat())
    table: dict[str, tuple[SlotAdmission, ...]] = {}
    for nome, schema_legality in legality.items():
        code = nome.replace("-", "")
        order = _by_code().get(code)
        if order is None:  # pragma: no cover - the gate holds the two files to one key set
            continue
        by_roles: dict[frozenset[str], list[SlotRule]] = {}
        for rule in schema_legality.slots:
            by_roles.setdefault(_roles_of(rule.name), []).append(rule)
        admissions: list[SlotAdmission] = []
        for natural in order:
            rules = by_roles.get(natural)
            if not rules:  # pragma: no cover - same gate
                break
            rule = rules.pop(0)
            admissions.append(
                SlotAdmission(
                    natural=natural,
                    submission=rule.submission,
                    substitution=rule.substitution,
                )
            )
        if len(admissions) == len(order):
            table[code] = tuple(admissions)
    return table


def _roles_of(label: str) -> frozenset[str]:
    return frozenset(normalize_role(role) for role in label.split("/"))


def admissions(module_code: str) -> tuple[SlotAdmission, ...]:
    """The 11 slots of a Mantra module as `SlotAdmission`s, in the platform's `starts[]` order.

    Raises `ValueError` for a code that is not one of the 11, for the same reason `slots`
    does: an empty schema would silently accept every assignment.
    """
    try:
        return _admissions_by_code()[module_code]
    except KeyError:
        raise ValueError(f"unknown module code: {module_code!r}") from None
