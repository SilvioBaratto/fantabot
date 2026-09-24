"""The Classic branches of the live-room helpers: roster shrink, the drop, and the bargain gate."""

from __future__ import annotations

import subprocess
import sys
import textwrap

import pytest

from fantabot.domain.asta.bid import max_bid
from fantabot.domain.asta.reservation import PoolFormatMismatch, opportunistic_walkaway
from fantabot.domain.asta.roles import MantraPlayer
from fantabot.domain.asta.state import AstaState, RosterRules, drop_unvaluable
from fantabot.domain.classic.roles import ClassicPlayer
from fantabot.domain.classic.state import ClassicRosterRules

TINY = ClassicRosterRules(size=4, bands=(("P", 1, 1), ("D", 1, 1), ("C", 1, 1), ("A", 1, 1)))


def test_the_max_cap_sizes_off_the_classic_roster_not_the_mantra_one() -> None:
    # asta bid's _cap reserves one credit per remaining obligatory slot: 25 for Classic,
    # 30 for the Mantra default. A wrong --format would cap against the wrong band.
    assert ClassicRosterRules().size == 25
    assert max_bid(500, ClassicRosterRules().size) != max_bid(500, RosterRules().size)


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


# -- The dispatch pairs a format's rules with that format's pool, and says so ------------
#
# `Candidate` and `CompositionRules` are independent unions, so nothing in the type system
# stops a caller handing over one of each. The two sites below were bare
# `assert isinstance(...)`: stripped entirely under `python -O`, and an `AssertionError`
# with an empty message when they did fire — out of a function inside the room's poll loop,
# whose every other refusal is a `None` and whose caller now re-raises `AssertionError` on
# purpose (`application/containment.py`: an assertion is a bug).


def test_a_mantra_player_under_classic_rules_is_a_named_domain_error() -> None:
    with pytest.raises(PoolFormatMismatch) as caught:
        opportunistic_walkaway(
            MantraPlayer("a1", frozenset({"A"})),
            owned_players=[], prices={"a1": 30.0, "c9": 40.0},
            plan=["c9"], owned=[], legality={}, rules=TINY, max_cap=100, beta=0.6,
        )

    assert "Classic roster rules were handed MantraPlayer 'a1'" in str(caught.value)


def test_a_classic_player_under_mantra_rules_is_a_named_domain_error() -> None:
    with pytest.raises(PoolFormatMismatch) as caught:
        opportunistic_walkaway(
            ClassicPlayer("a1", "A"),
            owned_players=[], prices={"a1": 30.0, "c9": 40.0},
            plan=["c9"], owned=[], legality={}, rules=RosterRules(), max_cap=100, beta=0.6,
        )

    assert "Mantra roster rules were handed ClassicPlayer 'a1'" in str(caught.value)


def test_the_mismatch_is_a_domain_error_and_not_an_assertion() -> None:
    """`asta_room._bargain_for` re-raises `AssertionError` and holds on everything else, so
    which base this picks decides whether a mismatch costs the poll or the lot."""
    assert issubclass(PoolFormatMismatch, TypeError)
    assert not issubclass(PoolFormatMismatch, AssertionError)


def test_the_guard_still_holds_under_python_O_where_an_assert_would_not() -> None:
    """A subprocess because `-O` is decided at compile time and cannot be turned on here.

    This is the half of the defect no in-process test can reach: with the asserts stripped
    the mismatch fell through to the next line and surfaced one frame later as
    `AttributeError: 'MantraPlayer' object has no attribute 'role'` — a message that names
    neither the rules nor the dispatch that paired them wrong.
    """
    script = textwrap.dedent(
        """
        from fantabot.domain.asta.reservation import (
            PoolFormatMismatch, opportunistic_walkaway,
        )
        from fantabot.domain.asta.roles import MantraPlayer
        from fantabot.domain.classic.state import ClassicRosterRules

        if __debug__:
            raise SystemExit(4)  # not an `assert`: -O would strip the guard itself
        try:
            opportunistic_walkaway(
                MantraPlayer("a1", frozenset({"A"})),
                owned_players=[], prices={"a1": 30.0, "c9": 40.0},
                plan=["c9"], owned=[], legality={}, rules=ClassicRosterRules(),
                max_cap=100, beta=0.6,
            )
        except PoolFormatMismatch:
            raise SystemExit(0)
        except BaseException as exc:
            print(f"{type(exc).__name__}: {exc}")
            raise SystemExit(2)
        raise SystemExit(3)
        """
    )

    result = subprocess.run(
        [sys.executable, "-O", "-c", script], capture_output=True, text=True
    )

    assert result.returncode == 0, f"{result.stdout}{result.stderr}"
