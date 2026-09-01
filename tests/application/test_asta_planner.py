"""Assembling the plan world, and who is left out of it.

**Why an exclusion exists at all.** The listone is fantacalcio.it's, and it lags reality:
Rafael Leao's permanent transfer to Galatasaray was announced on 2026-08-30 and the site
still carried him at MIL the next day. Buying a player who has left Serie A is dead
money -- he cannot score -- and nothing else in the engine can prevent it:

* Re-scraping does not help. The scraper faithfully reproduces the site, and the site
  still lists him.
* The sentiment layer *cannot*, by design. Its gate is floored at `disp_floor=0.50` and
  `tit_floor=0.40` precisely so news can tilt a value and never veto it -- `sentiment.py`
  says so outright. Measured on the real pool: the worst reading the schema allows
  (`disponibilita=0`, `titolarita=0`, `confidenza=1`) takes Leao from 65.47 to 38.24,
  and at a 1-credit price he stays in the optimal roster regardless.

So it is a separate fact, held separately: a player is excluded because someone decided
he is not in the league, with a reason recorded beside it.
"""

from __future__ import annotations

from datetime import date

from fantabot.application.asta_planner import build_plan_inputs
from fantabot.domain.shared.values import QuotazioneRow


def _row(pid: str, nome: str, squadra: str = "MIL", fvm: int = 100) -> QuotazioneRow:
    return QuotazioneRow(
        player_id=pid, nome=nome, squadra=squadra,
        ruoli_codice=("A",), ruoli=("Attaccante",), fvm=fvm,
    )


QUOTAZIONI = {
    "1": _row("1", "Stays"),
    "2": _row("2", "Leao"),
    "3": _row("3", "Also stays", squadra="INT"),
}
PRICES = {"1": 10.0, "2": 1.0, "3": 5.0}


def _world(excluded: set[str] | None = None):  # type: ignore[no-untyped-def]
    return build_plan_inputs(
        QUOTAZIONI, PRICES, None, as_of=date(2026, 8, 31), tilt_k=0.25,
        excluded=excluded or set(),
    )


def _callable_world(callable_ids: set[str] | None):  # type: ignore[no-untyped-def]
    return build_plan_inputs(
        QUOTAZIONI, PRICES, None, as_of=date(2026, 8, 31), tilt_k=0.25,
        callable_ids=callable_ids,
    )


class TestAnExcludedPlayerIsAbsentFromEveryDerivedMap:
    """Not merely unpickable -- absent. A half-excluded player is worse than none.

    `format_roster` reads `names`, the opponent tracker reads `roles`, the optimizer's
    variance term reads `teams`. If he survived in any of them he would surface somewhere
    as a name with no row behind it.
    """

    def test_he_is_not_in_the_pool(self) -> None:
        assert [p.id for p in _world({"2"}).pool] == ["1", "3"]

    def test_he_is_not_in_the_name_map(self) -> None:
        assert "2" not in _world({"2"}).names

    def test_he_is_not_in_the_club_map(self) -> None:
        assert "2" not in _world({"2"}).teams

    def test_he_is_not_in_the_role_map(self) -> None:
        assert "2" not in _world({"2"}).roles

    def test_excluding_nobody_leaves_the_world_whole(self) -> None:
        assert len(_world().pool) == 3
        assert set(_world().names) == {"1", "2", "3"}


class TestExclusionIsIndependentOfTheOtherInputs:
    def test_an_id_that_is_not_in_the_listone_is_harmless(self) -> None:
        """The table outlives the listone: a player excluded last season may be gone."""
        assert len(_world({"nobody"}).pool) == 3

    def test_his_price_may_remain_and_is_never_reached(self) -> None:
        """Prices come from a different table and are not filtered -- the pool is what
        gates selection, so a stale price entry cannot put him back."""
        world = _world({"2"})

        assert "2" in world.prices
        assert "2" not in {p.id for p in world.pool}

    def test_the_value_model_carries_no_signal_for_him(self) -> None:
        """The sentiment normalization pins the *pool* mean at exactly 1.0, so a player
        left in the value model would shift every other player's multiplier.

        `value(pid)` still answers for him -- an unknown id shrinks to the prior with the
        widest band rather than raising -- so `signals` is what has to be checked. The
        pool is what gates selection either way.
        """
        world = _world({"2"})

        assert "2" not in world.value.signals
        assert "1" in world.value.signals


class TestOnlyCallablePlayersAreDerived:
    """A player FantaLab's listone does not carry can never come up for auction.

    Measured 2026-09-01: 41 of 570 pool players — Lukaku, Nkunku, Morata, Perin,
    Angelino among them — are absent from the platform's listone, and the optimizer
    put three of them in the plan. In a simulated mid-auction state Lukaku was the
    top walk-away of all twelve targets: the bot's headline target was a player who
    could not appear on the block.

    An allowlist rather than a difference. The obvious spelling —
    `pool_ids - set(bridge.values())` — subtracts `set[int]` from `set[str]`
    (`listone.parse` keeps ints, `resolve_ids` stringifies), removes nothing, and
    therefore excludes the entire pool.
    """

    def test_a_player_outside_the_listone_is_absent_from_every_map(self) -> None:
        world = _callable_world({"1", "3"})

        assert [p.id for p in world.pool] == ["1", "3"]
        assert "2" not in world.names
        assert "2" not in world.teams
        assert "2" not in world.roles

    def test_none_means_no_filtering_at_all(self) -> None:
        """Absent is not empty. The golden fixtures pass nothing and must be untouched."""
        assert [p.id for p in _callable_world(None).pool] == ["1", "2", "3"]

    def test_an_empty_allowlist_empties_the_pool_rather_than_ignoring_itself(self) -> None:
        """`callable_ids=set()` means the bridge resolved nothing, which is a real and
        loud failure — `asta bid` already refuses to start on an empty bridge. Treating
        it as "no filter" would silently plan on players none of which can be called."""
        assert list(_callable_world(set()).pool) == []

    def test_it_composes_with_exclusions_rather_than_replacing_them(self) -> None:
        world = build_plan_inputs(
            QUOTAZIONI, PRICES, None, as_of=date(2026, 8, 31), tilt_k=0.25,
            excluded={"1"}, callable_ids={"1", "2"},
        )

        assert [p.id for p in world.pool] == ["2"], "excluded wins, and uncallable is dropped"
