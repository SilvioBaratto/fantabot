"""Classic roster composition rules. Pure.

The Classic counterpart to `domain/asta/state.RosterRules`. Where Mantra models two
super-roles (goalkeepers vs everyone-else, `sroles=2`), Classic has a real **four**-role band
over P/D/C/A (`sroles=1`) with an independent floor and ceiling per role. For the `static`
selection the platform serves (measured live 3584692, `docs/classic/task0-capture.md`), floor
== ceiling: `{P:3, D:8, C:8, A:6}`, a fixed 25-man rosa.

Frozen and hashable (the bands are a tuple, not a dict) so it can key the per-cycle plan memo
in the room tracker, exactly as `RosterRules` does.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import ClassVar

#: The confirmed Classic band for lega 3584692: 3 keepers, 8 defenders, 8 mids, 6 attackers.
DEFAULT_BANDS: tuple[tuple[str, int, int], ...] = (("P", 3, 3), ("D", 8, 8), ("C", 8, 8), ("A", 6, 6))


@dataclass(frozen=True)
class ClassicRosterRules:
    """Four per-role bands (role, min, max) over P/D/C/A, and the total roster size.

    ``kind`` is a class-level discriminator the optimizer dispatches on without importing this
    type into a hot branch; it is not a dataclass field, so it never touches equality or the
    hash that keys the plan memo.
    """

    size: int = 25
    bands: tuple[tuple[str, int, int], ...] = DEFAULT_BANDS
    kind: ClassVar[str] = "classic"

    def roles(self) -> tuple[str, ...]:
        return tuple(role for role, _lo, _hi in self.bands)

    def min_of(self, role: str) -> int:
        return next(lo for r, lo, _hi in self.bands if r == role)

    def max_of(self, role: str) -> int:
        return next(hi for r, _lo, hi in self.bands if r == role)


def classic_rules(counts: Mapping[str, int], *, size: int | None = None) -> ClassicRosterRules:
    """Build rules from a parsed ``static`` band (``players_settings_data`` / ``minrl``).

    ``counts`` is the exact per-role composition (e.g. ``{"P":3,"D":8,"C":8,"A":6}``); under the
    ``static`` selection min == max, so each becomes a pinned band. ``size`` defaults to the sum,
    matching the platform's own invariant that the per-role counts total the roster size.
    """
    bands = tuple((role, counts[role], counts[role]) for role in ("P", "D", "C", "A") if role in counts)
    total = size if size is not None else sum(counts.values())
    return ClassicRosterRules(size=total, bands=bands)
