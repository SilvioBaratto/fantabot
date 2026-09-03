"""Classic macro roles and the player value type. Pure.

Four roles — P/D/C/A — and a Classic player carries **exactly one** of them, so eligibility is
equality, not the set-intersection Mantra needs (`domain/asta/roles.MantraPlayer`). The code
scale is the platform's own: `fcrle` on `/league/players`, and `custom-roles`, both use the
integers `{1:P, 2:D, 3:C, 4:A}` — `domain/lega/parse.CLASSIC_ROLE_CODES`, the single source for
that mapping. Measured live 2026-09-03 (`docs/classic/task0-capture.md`): fcrle=1 goalkeepers,
2 defenders, 3 midfielders, 4 attackers.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from fantabot.domain.lega.parse import CLASSIC_ROLE_CODES


class Role(StrEnum):
    """The four Classic macro roles, canonical uppercase (the `quotazioni` listone form)."""

    P = "P"  # portiere
    D = "D"  # difensore
    C = "C"  # centrocampista
    A = "A"  # attaccante


#: The four codes as plain strings, for membership tests without the enum.
CLASSIC_ROLES: frozenset[str] = frozenset(role.value for role in Role)

#: The goalkeeper role — the one band that is a platform floor, not a per-league choice.
GOALKEEPER: str = Role.P.value


def normalize_role(code: str) -> str:
    """Fold a role code to its canonical uppercase letter, or raise if it is not P/D/C/A.

    Mirrors `domain/asta/roles.normalize_role` for Mantra: the one place a Classic role is
    validated, so a stray listone code fails loudly here rather than silently mis-bucketing a
    player downstream.
    """
    upper = code.strip().upper()
    if upper not in CLASSIC_ROLES:
        raise ValueError(f"not a Classic role code: {code!r}")
    return upper


def role_from_fcrle(value: Any) -> str:
    """A platform `fcrle`/`custom-roles` integer as its canonical Classic letter.

    Fail-closed: an unmapped code raises rather than guessing, matching the role-drift rule —
    a player whose role cannot be resolved never reaches a decision module.
    """
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise ValueError(f"not a Classic role integer: {value!r}") from None
    letter = CLASSIC_ROLE_CODES.get(number)
    if letter is None:
        raise ValueError(f"not a Classic role integer: {value!r}")
    return letter


@dataclass(frozen=True)
class ClassicPlayer:
    """One player as the Classic engine needs him: an id and his single macro role.

    Deliberately not a `frozenset` (the Mantra shape) — a Classic player has exactly one role,
    and modelling that as a one-element set would invite the intersection logic Classic does not
    use.
    """

    id: str
    role: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "role", normalize_role(self.role))


def build_classic_pool(roles_by_id: Mapping[str, Sequence[str]]) -> list[ClassicPlayer]:
    """A Classic pool from the listone's per-player role codes. The counterpart to
    `domain/asta/report.build_pool` for Mantra.

    A Classic listone row carries a single macro role (`ruoli_codice` is one element); the
    first code is taken and validated by `ClassicPlayer`. A row with no code is skipped — it
    cannot be placed in any band — rather than crashing the whole build.
    """
    return [
        ClassicPlayer(id=player_id, role=codes[0])
        for player_id, codes in roles_by_id.items()
        if codes
    ]
