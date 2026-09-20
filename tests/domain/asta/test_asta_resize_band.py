"""Resizing a roster band without making it incoherent.

`asta bid` is unauthenticated: it cannot read the room's own shape, so the band it plans and
caps against comes from `--lega` — which defaults to `settings.fantabot_league_id`, a
*leghe.fantacalcio* league id with no relation to the FantaLab room being bid in. `--size`
is how the room's own total reaches it, and this is the rule that applies it.

**A bare `size=` override is the trap.** `RosterRules.max_goalkeepers()` is
`size - min_movement`, so `replace(rules, size=25)` over the built-in `30/2/28` yields
**-3 keepers**. Measured on the live database, the band is not even self-consistent in the
obvious way: lega 4103937's last sync reads `size=32, min_goalkeepers=2, min_movement=23`,
where `min_goalkeepers + min_movement = 25 ≠ 32` — `rules_for_lega` builds the size from
`roster_size` and the floors from `minrl`, and they are a maximum and two minimums. So a rule
deriving `min_movement` from `size - min_goalkeepers` would *invent* a floor the lega never
declared.

The rule is the one `drop_unvaluable` already uses, generalised to both directions: keep the
floors, and clamp the movement floor only as far as the new size forces. On a shrink it
reproduces `drop_unvaluable` exactly (30 → 25 gives `min_movement=23`, which is `28 - 5`);
on a growth it leaves the declared floor alone rather than inflating it.
"""

from __future__ import annotations

import pytest

from fantabot.domain.asta.state import RosterRules, resize_band
from fantabot.domain.classic.state import ClassicRosterRules

#: Every band shape this repository can actually produce, by where it comes from.
BANDS = [
    ("the built-in default", RosterRules()),
    ("lega 4103937's last sync", RosterRules(size=32, min_goalkeepers=2, min_movement=23)),
    ("a room's own declaration", RosterRules(size=25, min_goalkeepers=2, min_movement=23)),
]


class TestTheResultIsAlwaysCoherent:
    @pytest.mark.parametrize(("where", "rules"), BANDS, ids=[b[0] for b in BANDS])
    @pytest.mark.parametrize("size", [19, 25, 30, 32, 40])
    def test_a_band_can_hold_its_own_floors(self, where: str, rules: RosterRules, size: int) -> None:
        """`max_goalkeepers()` and `max_movement()` must leave room for the floors they
        sit above. Below that the optimizer is being asked for a roster that cannot exist."""
        out = resize_band(rules, size)

        assert out.size == size
        assert out.max_goalkeepers() >= out.min_goalkeepers, (
            f"{where} at --size {size}: {out.max_goalkeepers()} keepers allowed, "
            f"{out.min_goalkeepers} required"
        )
        assert out.max_movement() >= out.min_movement
        assert out.min_goalkeepers + out.min_movement <= out.size

    def test_the_naive_override_is_what_this_exists_to_prevent(self) -> None:
        """Stated as a test rather than as a comment, because it is the whole reason the
        function is not one `replace` call."""
        from dataclasses import replace

        naive = replace(RosterRules(), size=25)

        assert naive.max_goalkeepers() == -3
        assert resize_band(RosterRules(), 25).max_goalkeepers() == 2


class TestItAgreesWithTheRuleAlreadyInTheTree:
    def test_a_shrink_matches_drop_unvaluable(self) -> None:
        """`drop_unvaluable` shrinks `size` and `min_movement` by the same amount when an
        owned player is not in the pool. A second rule for the same arithmetic is a second
        answer, so this one has to give the same number."""
        from fantabot.domain.asta.state import AstaState, drop_unvaluable

        rules = RosterRules()
        _state, shrunk, _dropped = drop_unvaluable(
            AstaState(owned=("ghost-1", "ghost-2", "ghost-3", "ghost-4", "ghost-5")), [], rules
        )

        assert (shrunk.size, shrunk.min_movement) == (25, 23)
        assert resize_band(rules, 25) == shrunk

    def test_a_growth_does_not_invent_a_floor(self) -> None:
        """The declared floor is what the lega said. Scaling it up with the size would
        report a minimum nobody stated — and on the band measured live
        (`32/2/23`) that would be a floor seven players above the truth."""
        grown = resize_band(RosterRules(size=32, min_goalkeepers=2, min_movement=23), 40)

        assert grown.min_movement == 23
        assert grown.min_goalkeepers == 2

    def test_the_same_size_is_the_identity(self) -> None:
        for _where, rules in BANDS:
            assert resize_band(rules, rules.size) == rules


class TestItRefusesRatherThanReturningNonsense:
    def test_a_size_below_the_keeper_floor_is_refused(self) -> None:
        """A one-man rosa in a league that requires two goalkeepers is not a small band, it
        is an impossible one — and `optimize_roster` would say so much later, from inside a
        live loop, as an exception per cycle."""
        with pytest.raises(ValueError, match="2 goalkeepers"):
            resize_band(RosterRules(), 1)

    @pytest.mark.parametrize("size", [0, -1])
    def test_a_size_that_is_not_a_roster_is_refused(self, size: int) -> None:
        with pytest.raises(ValueError):
            resize_band(RosterRules(), size)


class TestClassic:
    """Four per-role bands over P/D/C/A, and `min == max` under the `static` selection."""

    def test_a_shrink_goes_through_the_rules_own_method(self) -> None:
        """`ClassicRosterRules.shrunk` already trims floors largest-first to keep
        `sum(min) <= size`. Re-deriving that here would be the second copy."""
        rules = ClassicRosterRules()

        out = resize_band(rules, rules.size - 4)

        assert out == rules.shrunk(4)
        assert sum(out.min_of(role) for role in out.roles()) <= out.size

    def test_a_growth_past_the_declared_band_is_refused(self) -> None:
        """Under `static` every band is pinned (`min == max`), so the roles can supply
        exactly `sum(max)` players and no more. A size above that is a roster the room can
        never fill — the optimizer would look for a player no role is allowed to add."""
        rules = ClassicRosterRules()
        ceiling = sum(rules.max_of(role) for role in rules.roles())

        assert resize_band(rules, ceiling).size == ceiling
        with pytest.raises(ValueError, match=r"cannot be filled"):
            resize_band(rules, ceiling + 1)

    @pytest.mark.parametrize("size", [0, -1])
    def test_a_size_that_is_not_a_roster_is_refused_here_too(self, size: int) -> None:
        """Classic reaches the refusal by a different route and has to be asked separately.

        The Mantra branch refuses a zero twice over — `size < 1`, and again below the keeper
        floor — so dropping the first guard left every Mantra case still red and this one
        green: `0 <= ceiling` passes the band check and `shrunk(25)` floors the size at 0.
        A survivor of this file's own battery.
        """
        with pytest.raises(ValueError):
            resize_band(ClassicRosterRules(), size)

    def test_the_result_keeps_every_role(self) -> None:
        out = resize_band(ClassicRosterRules(), 21)

        assert out.roles() == ("P", "D", "C", "A")
