"""The Classic branches of the live-room helpers: roster shrink, the drop, and the bargain gate."""

from __future__ import annotations

from fantabot.domain.asta.reservation import opportunistic_walkaway
from fantabot.domain.asta.state import AstaState, drop_unvaluable
from fantabot.domain.classic.roles import ClassicPlayer
from fantabot.domain.classic.state import ClassicRosterRules

TINY = ClassicRosterRules(size=4, bands=(("P", 1, 1), ("D", 1, 1), ("C", 1, 1), ("A", 1, 1)))


def test_shrunk_stays_feasible() -> None:
    r = ClassicRosterRules().shrunk(3)  # 25 -> 22, trim floors so sum(min) fits
    assert r.size == 22
    assert sum(r.min_of(role) for role in r.roles()) <= 22


def test_drop_unvaluable_shrinks_the_classic_band() -> None:
    pool = [ClassicPlayer("known", "D")]
    state = AstaState(owned=("known", "ghost"), spent=50.0)  # ghost is not in the pool
    kept, shrunk, dropped = drop_unvaluable(state, pool, ClassicRosterRules())

    assert dropped == ["ghost"]
    assert "ghost" not in kept.owned
    assert isinstance(shrunk, ClassicRosterRules)
    assert shrunk.size == 24
    assert sum(shrunk.min_of(role) for role in shrunk.roles()) <= 24


def test_opportunistic_walkaway_refuses_a_role_at_its_ceiling() -> None:
    owned = [ClassicPlayer("d1", "D")]  # D is already at its max of 1
    cap = opportunistic_walkaway(
        ClassicPlayer("d2", "D"),
        owned_players=owned, prices={"d2": 30.0, "c9": 40.0},
        plan=["c9"], owned=[], legality={}, rules=TINY, max_cap=100, beta=0.6,
    )
    assert cap is None


def test_opportunistic_walkaway_admits_an_open_role() -> None:
    owned = [ClassicPlayer("d1", "D")]  # A is still open
    cap = opportunistic_walkaway(
        ClassicPlayer("a1", "A"),
        owned_players=owned, prices={"a1": 30.0, "c9": 40.0},
        plan=["c9"], owned=[], legality={}, rules=TINY, max_cap=100, beta=0.6,
    )
    assert cap == 18  # min(int(0.6*30), share 40, max_cap 100)
