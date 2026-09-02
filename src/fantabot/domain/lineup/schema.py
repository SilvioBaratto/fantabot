"""Module code -> ordered slot role-sets, GK first. The shape the builder lays `starts[]` into.

The platform sends `mdl` as a dashless code (`"343"`) and `starts[]` as 11 ids in slot order
with the goalkeeper first. The shipped `mantra_schemi.json` describes each schema by name
(`"3-4-3"`) and its 10 outfield slots. This module joins the two: strip the dashes to key by
code, canonicalise the roles (uppercase, via `asta.roles`), and prepend the implicit GK slot.

Reading the packaged JSON is the same "thin data-load edge" `asta.legality.load_compat` uses
— package data, deterministic, no database and no network — so it stays inside `domain`.
"""

from __future__ import annotations

import json
from functools import lru_cache

from fantabot.domain.asta.roles import normalize_role
from fantabot.domain.shared.resources import SCHEMI_FILENAME, data_dir

#: The goalkeeper slot, always `starts[0]`, implicit in `mantra_schemi.json`.
GK_ROLE = "POR"


@lru_cache(maxsize=1)
def _by_code() -> dict[str, tuple[frozenset[str], ...]]:
    raw = json.loads((data_dir() / SCHEMI_FILENAME).read_text(encoding="utf-8"))
    table: dict[str, tuple[frozenset[str], ...]] = {}
    for entry in raw["schemi"]:
        code = str(entry["nome"]).replace("-", "")
        outfield = tuple(
            frozenset(normalize_role(role) for role in slot) for slot in entry["slots"]
        )
        table[code] = (frozenset({GK_ROLE}), *outfield)
    return table


def modules() -> frozenset[str]:
    """The dashless codes of every known Mantra schema (the 11 the platform allows)."""
    return frozenset(_by_code())


def slots(module_code: str) -> tuple[frozenset[str], ...]:
    """The 11 ordered slot role-sets for a module, GK first — `starts[]` order.

    Raises `ValueError` for a code that is not one of the 11, rather than returning an empty
    schema that would silently accept any assignment.
    """
    try:
        return _by_code()[module_code]
    except KeyError:
        raise ValueError(f"unknown module code: {module_code!r}") from None
