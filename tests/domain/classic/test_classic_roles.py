"""Classic macro-role model: the four codes, fail-closed parsing, and the fcrle scale."""

from __future__ import annotations

import pytest

from fantabot.domain.classic.roles import (
    CLASSIC_ROLES,
    ClassicPlayer,
    Role,
    normalize_role,
    role_from_fcrle,
)


def test_the_four_macro_roles_are_pdca() -> None:
    assert {"P", "D", "C", "A"} == CLASSIC_ROLES
    assert Role.P == "P" and Role.A == "A"  # StrEnum: members are the canonical letters


def test_normalize_folds_case_and_rejects_non_classic() -> None:
    assert normalize_role("p") == "P"
    assert normalize_role(" D ") == "D"
    for good in ("P", "D", "C", "A"):
        assert normalize_role(good) == good
    with pytest.raises(ValueError):
        normalize_role("Por")  # a Mantra code, not a Classic macro role


def test_fcrle_maps_the_classic_scale() -> None:
    assert [role_from_fcrle(n) for n in (1, 2, 3, 4)] == ["P", "D", "C", "A"]
    assert role_from_fcrle("2") == "D"  # a stringified integer still resolves


def test_fcrle_is_fail_closed_on_the_marle_scale() -> None:
    # 6..16/19 are marle (Mantra granular) codes — a P/D/C/A read of one must RAISE, never
    # silently mis-bucket. This is the scale-collision guard CLAUDE.md warns of.
    for marle_only in (0, 5, 6, 9, 16, 19):
        with pytest.raises(ValueError):
            role_from_fcrle(marle_only)
    with pytest.raises(ValueError):
        role_from_fcrle(None)


def test_classic_player_carries_one_normalized_role() -> None:
    assert ClassicPlayer("123", "a").role == "A"
    with pytest.raises(ValueError):
        ClassicPlayer("123", "Dc")  # a Mantra slot code, not a Classic macro role
