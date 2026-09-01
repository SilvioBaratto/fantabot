"""The LISTONE pane's rows, as data rather than a pre-rendered string.

The requirement is "tutti i player" — the whole listone, on screen, sortable and filterable.
That means Rich needs columns, so this returns rows and the interface decides how they look.

**Primitives, not `PlanInputs`.** That type is defined in `application/`, and a domain module
importing it would give `domain/` a transitive path to `adapters.persistence` and break the
layer ratchet. Every existing call site already destructures at the boundary; this follows.
"""

from __future__ import annotations

from fantabot.domain.asta.report import listone_rows
from fantabot.domain.asta.roles import MantraPlayer, normalize_roles
from fantabot.domain.asta.state import AstaState
from fantabot.domain.asta.value import NaiveValueModel

POOL = [
    MantraPlayer("1", normalize_roles(["A"])),
    MantraPlayer("2", normalize_roles(["POR"])),
    MantraPlayer("3", normalize_roles(["DC", "DS"])),
]
NAMES = {"1": "Bomber", "2": "Portiere", "3": "Difensore"}
TEAMS = {"1": "MIL", "2": "INT", "3": "JUV"}
PRICES = {"1": 80.0, "2": 10.0}
VALUE = NaiveValueModel(
    signals={"1": 10.0, "2": 5.0, "3": 7.0},
    prior_mean=1.0, base_variance=1.0, no_history_variance=1.0,
)


def _rows(state=None, walkaways=None, limit=10):  # type: ignore[no-untyped-def]
    return listone_rows(
        POOL,
        state or AstaState(total_budget=500.0),
        names=NAMES, teams=TEAMS, prices=PRICES, value=VALUE,
        walkaways=walkaways or {},
        limit=limit,
    )


class TestWhatARowCarries:
    def test_a_name_a_club_and_the_mantra_roles(self) -> None:
        row = next(r for r in _rows() if r.player_id == "3")

        assert (row.name, row.team) == ("Difensore", "JUV")
        assert set(row.roles) == {"DC", "DS"}

    def test_the_price_is_the_planning_cost_not_the_raw_map(self) -> None:
        """A player with no observed sale costs 1, not 0 — the platform will not sell at 0,
        and the roster report already agrees on that convention."""
        assert next(r for r in _rows() if r.player_id == "3").price == 1

    def test_a_walkaway_is_shown_only_where_the_plan_priced_one(self) -> None:
        rows = {r.player_id: r for r in _rows(walkaways={"1": 42.7})}

        assert rows["1"].walk_away == 42
        assert rows["2"].walk_away is None


class TestStatus:
    def test_ours_taken_and_open_are_distinguished(self) -> None:
        state = AstaState(owned=("1",), taken=frozenset({"1", "2"}), total_budget=500.0)
        rows = {r.player_id: r.status for r in _rows(state=state)}

        assert rows["1"] == "ours"
        assert rows["2"] == "taken", "somebody else's, and no longer available"
        assert rows["3"] == "open"


class TestOrderAndLimit:
    def test_rows_are_ranked_by_value(self) -> None:
        assert [r.player_id for r in _rows()] == ["1", "3", "2"]

    def test_the_limit_keeps_the_top_of_the_list(self) -> None:
        assert [r.player_id for r in _rows(limit=2)] == ["1", "3"]

    def test_a_limit_of_zero_returns_nothing_rather_than_everything(self) -> None:
        """`[:0]` and "no limit" are different asks, and Python spells them the same way."""
        assert _rows(limit=0) == []
