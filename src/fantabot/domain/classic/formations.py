"""Classic formations and count-based legality. Pure.

A Classic starting XI is counts over the four macro roles: exactly one P, then a D-C-A split
summing to 10. The seven valid splits, confirmed live 2026-09-03 from lega 3584692's
`lineup_settings.mods` (`docs/classic/task0-capture.md`): 343 352 433 442 451 532 541. The three
digits of a code are the D, C, A counts.

Legality is **counting**, not the bipartite matching Mantra needs (`domain/asta/legality.py`): a
rosa fields a module iff it holds at least the module's count in each of P/D/C/A. This is why the
Classic engine is a separate seam — forcing a count check through the Kuhn matcher buys nothing.
"""

from __future__ import annotations

from collections.abc import Mapping

from fantabot.domain.classic.roles import Role

#: The seven Classic module codes (D-C-A digits), verbatim from the platform's `mods`.
FORMATION_CODES: tuple[str, ...] = ("343", "352", "433", "442", "451", "532", "541")


def _counts(code: str) -> dict[str, int]:
    """A three-digit D-C-A code to its per-role count vector (with the implicit single keeper)."""
    d, c, a = (int(ch) for ch in code)
    return {Role.P: 1, Role.D: d, Role.C: c, Role.A: a}


#: Module code -> {P,D,C,A: count}. The starting-XI shape for each of the seven modules.
FORMATIONS: dict[str, dict[str, int]] = {code: _counts(code) for code in FORMATION_CODES}


def formation_counts(code: str) -> dict[str, int]:
    """The per-role starting counts for a module, or raise if the code is not one of the seven."""
    try:
        return dict(FORMATIONS[code])
    except KeyError:
        raise ValueError(f"not a Classic formation code: {code!r}") from None


def fieldable_formations(role_counts: Mapping[str, int]) -> frozenset[str]:
    """The modules a rosa with these per-role counts can field a legal XI for.

    A module is fieldable iff the rosa meets its count in **every** role bucket — the Classic
    analogue of `fieldable_schemi`, resolved by comparison rather than matching.
    """
    return frozenset(
        code
        for code, need in FORMATIONS.items()
        if all(role_counts.get(role, 0) >= n for role, n in need.items())
    )


def classic_fieldable(role_counts: Mapping[str, int]) -> bool:
    """Whether the rosa can field any Classic module at all."""
    return bool(fieldable_formations(role_counts))
