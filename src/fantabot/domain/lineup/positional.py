"""Each starter judged at the platform's own slot i — the check the platform itself makes.

The builder matches on role **sets** and `asta.legality` asks whether a rosa can field a
schema at all; neither can see order. The platform can: it reads `starts[i]` against its
slot i (`S.schemes.mantra`, pinned in `mantra_starts_order.json`) and looks the player up
in that schema's row. A `no` or `-1*` there is a `LUP009`. A `-1` is worse, because the
platform **accepts** it and scores the malus, so its answer can never be the check for it.

This reads one `mantra_compat.json` cell per position, in the order `schema.slots` gives,
and an XI passes only when all eleven are `ok`. A player with several roles is judged by
his best one, as the platform's `getMinRoleMalus` does.

Package data, deterministic, no database and no network — the same thin data-load edge
`schema` and `asta.legality.load_compat` use.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from functools import lru_cache

from fantabot.domain.asta.legality import load_compat
from fantabot.domain.lineup import schema

#: A cell's cost, best first. Only `ok` passes; `-1` is accepted by the platform with a
#: malus, and `-1*`/`no` are refused at submission.
_RANK = {"ok": 0, "-1": 1, "-1*": 2, "no": 3}


@dataclass(frozen=True, slots=True)
class Cell:
    """One starter, where the platform will judge him."""

    position: int
    slot: frozenset[str]
    #: The player's role the cell was read for — his best one.
    role: str
    value: str

    def describe(self) -> str:
        """`[6] C: M -1` — the position, the slot, the role and the cell."""
        return f"[{self.position}] {'/'.join(sorted(self.slot))}: {self.role} {self.value}"


@lru_cache(maxsize=1)
def _rows() -> dict[str, dict[frozenset[str], dict[str, str]]]:
    """`module code -> slot role-set -> role -> cell`, from the schema's own rows.

    The cells themselves, which `schema.admissions` cannot give back: it folds them into
    `submission`/`substitution` sets, and `ok` and `-1` are indistinguishable once folded
    — while the whole job here is to tell them apart. The *pairing* of a row to a slot is
    the shared decision and is `schema.rows_by_slot`, which is also where the reason for
    both of its rules is written down.
    """
    matrix = load_compat()
    roles = [role.upper() for role in matrix.ruoli]
    return {
        entry.schema_nome.replace("-", ""): schema.rows_by_slot(
            entry.schema_nome,
            ((row.slot, dict(zip(roles, row.compat, strict=True))) for row in entry.slots),
        )
        for entry in matrix.formazioni
    }


def cells(
    module_code: str,
    starter_roles: Sequence[frozenset[str]],
    *,
    slots_provider: Callable[[str], tuple[frozenset[str], ...]] = schema.slots,
) -> tuple[Cell, ...]:
    """The eleven cells of an XI, `starter_roles[i]` judged at the module's slot i."""
    slot_sets = slots_provider(module_code)
    if len(starter_roles) != len(slot_sets):
        raise ValueError(
            f"{module_code}: {len(starter_roles)} starters, expected {len(slot_sets)}"
        )
    rows = _rows()[module_code]
    out = []
    for position, (slot, roles) in enumerate(zip(slot_sets, starter_roles, strict=True)):
        row = rows[slot]
        role = min(sorted(roles), key=lambda r: _RANK[row[r]])
        out.append(Cell(position, slot, role, row[role]))
    return tuple(out)


def violations(module_code: str, starter_roles: Sequence[frozenset[str]]) -> tuple[Cell, ...]:
    """Every cell that is not `ok`. Empty means the platform takes this XI as it is."""
    return tuple(c for c in cells(module_code, starter_roles) if c.value != "ok")


def refusal(module_code: str, starter_roles: Sequence[frozenset[str]]) -> str:
    """Why the guard refuses this XI, or `""` when it passes."""
    return "; ".join(c.describe() for c in violations(module_code, starter_roles))
