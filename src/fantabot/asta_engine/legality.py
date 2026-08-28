"""L1 — which of the 11 Mantra schemi a rosa can legally field. Pure combinatorics.

Bipartite matching: the schema's 11 slots (the fixed Por plus the 10 movement slots) on
one side, the rosa's players on the other. A player edges to a slot when one of his roles
is allowed there in the given mode — ``submission`` admits the ``ok``/``-1`` cells,
``substitution`` additionally admits ``-1*``. The schema is fieldable when a matching
saturates all 11 slots. ~30 players x 11 slots resolves in microseconds.

``-1*`` is kept a distinct state from ``-1`` and never folded in: it is refused when the
lineup is built and admitted only as the outcome of a forced substitution, so a matcher
that treats it as ``-1`` fields lineups the platform rejects. See ``mantra_grid.models``.

``build_legality`` and the matcher are pure — they take the parsed matrix. ``load_compat``
is the thin data-load edge (a static JSON file; no database, no network).
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from ..mantra_grid.models import ROLE_ORDER, CompatMatrix
from .roles import MantraPlayer

Mode = Literal["submission", "substitution"]

#: Cells placeable when building the lineup, and additionally after a forced substitution.
_SUBMISSION_CELLS: frozenset[str] = frozenset({"ok", "-1"})
_SUBSTITUTION_CELLS: frozenset[str] = frozenset({"ok", "-1", "-1*"})

_DATA_DIR = Path(__file__).resolve().parents[3] / "data"
#: The 12 role codes in column order, canonical (uppercase), aligned to a compat row.
_CANONICAL: tuple[str, ...] = tuple(code.upper() for code in ROLE_ORDER)


@dataclass(frozen=True)
class SlotRule:
    """One slot of a schema and the roles it admits, per mode.

    ``substitution`` is a superset of ``submission`` — it adds exactly the ``-1*`` roles.
    """

    name: str
    submission: frozenset[str]
    substitution: frozenset[str]


@dataclass(frozen=True)
class SchemaLegality:
    """A schema as the matcher needs it: its name and its 11 slot rules."""

    nome: str
    slots: tuple[SlotRule, ...]


def _allowed(compat: Sequence[str], cells: frozenset[str]) -> frozenset[str]:
    return frozenset(_CANONICAL[i] for i, value in enumerate(compat) if value in cells)


def build_legality(matrix: CompatMatrix) -> dict[str, SchemaLegality]:
    """Turn the parsed compat matrix into per-schema slot rules. Pure."""
    out: dict[str, SchemaLegality] = {}
    for formation in matrix.formazioni:
        slots = tuple(
            SlotRule(
                name=slot.slot,
                submission=_allowed(slot.compat, _SUBMISSION_CELLS),
                substitution=_allowed(slot.compat, _SUBSTITUTION_CELLS),
            )
            for slot in formation.slots
        )
        out[formation.schema_nome] = SchemaLegality(nome=formation.schema_nome, slots=slots)
    return out


def slot_allows(role: str, slot: SlotRule, mode: Mode) -> bool:
    """Whether a canonical role may fill this slot in the given mode."""
    allowed = slot.submission if mode == "submission" else slot.substitution
    return role in allowed


def can_field(
    players: Sequence[MantraPlayer], schema: SchemaLegality, mode: Mode = "submission"
) -> bool:
    """True iff a matching places a distinct player in every one of the 11 slots.

    Kuhn's augmenting-path algorithm over the slot→eligible-players bipartite graph.
    """
    eligible: list[list[int]] = [
        [p for p, player in enumerate(players) if any(slot_allows(r, slot, mode) for r in player.roles)]
        for slot in schema.slots
    ]
    player_to_slot: dict[int, int] = {}

    def augment(slot_idx: int, seen: set[int]) -> bool:
        for p in eligible[slot_idx]:
            if p in seen:
                continue
            seen.add(p)
            if p not in player_to_slot or augment(player_to_slot[p], seen):
                player_to_slot[p] = slot_idx
                return True
        return False

    return all(augment(slot_idx, set()) for slot_idx in range(len(schema.slots)))


def fieldable_schemi(
    players: Sequence[MantraPlayer],
    legality: dict[str, SchemaLegality],
    mode: Mode = "submission",
) -> frozenset[str]:
    """The names of every schema this rosa can field in the given mode."""
    return frozenset(nome for nome, schema in legality.items() if can_field(players, schema, mode))


def marginal_legality(
    players: Sequence[MantraPlayer],
    player: MantraPlayer,
    legality: dict[str, SchemaLegality],
    mode: Mode = "submission",
) -> frozenset[str]:
    """The schemi that become fieldable when ``player`` is added to ``players``."""
    before = fieldable_schemi(players, legality, mode)
    after = fieldable_schemi([*players, player], legality, mode)
    return after - before


def load_compat(data_dir: Path | None = None) -> CompatMatrix:
    """Read the shipped compat matrix from ``data/``. The data-load edge — no DB, no network."""
    path = (data_dir or _DATA_DIR) / "mantra_compat.json"
    return CompatMatrix.model_validate(json.loads(path.read_text(encoding="utf-8")))
