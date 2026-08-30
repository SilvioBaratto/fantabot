"""Mantra role codes and the player value type L1 matches on. Pure.

The 12 Mantra codes canonicalized to UPPERCASE — the form the ``quotazioni`` table stores
(``DC``/``DS``/``POR``/``PC``). The schemi/compat JSON use mixed case (``Dc``/``Ds``/``Por``),
the rules doc lowercase; ``normalize_role`` folds all three to the canonical form so the
matcher compares a player's DB roles against the JSON matrix without guessing at case.

The 12 codes come from ``mantra_grid.models.ROLE_ORDER`` so there is a single source of
truth for what a Mantra role is.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from fantabot.mantra_grid.models import ROLE_ORDER

#: The 12 Mantra role codes, canonical (uppercase).
MANTRA_ROLES: frozenset[str] = frozenset(code.upper() for code in ROLE_ORDER)


def normalize_role(code: str) -> str:
    """Fold a role code to its canonical uppercase form, or raise if it is not one of the 12."""
    upper = code.strip().upper()
    if upper not in MANTRA_ROLES:
        raise ValueError(f"not a Mantra role code: {code!r}")
    return upper


def normalize_roles(codes: Iterable[str]) -> frozenset[str]:
    """Canonicalize a player's role codes, dropping blanks."""
    return frozenset(normalize_role(code) for code in codes if code.strip())


@dataclass(frozen=True)
class MantraPlayer:
    """One player as L1 needs him: an id and his canonical Mantra roles."""

    id: str
    roles: frozenset[str]
