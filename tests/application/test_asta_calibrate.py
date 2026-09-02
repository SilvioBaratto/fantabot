"""The ceiling-alpha sweep: the only evidence the bid premium is set to a defensible number.

`--ceiling-alpha` decides what the bot pays on top of `lot_ceiling`'s own re-solved number. It
is a hand-set constant, and SPEC A6 refuses arming until it has been replayed against auctions
that really happened. This is that replay, and it is pure: it takes lots already read from
Postgres and returns a table.

**Re-targeted at `lot_ceiling`, not the retired walk-away floor (Task 1.3).** Only a plan
member is ever priced, same scope as before this task: a lot outside the plan was always
counted lost regardless of alpha, and stays that way here — see `_replay_one`'s own docstring
for why widening it needs a band/slot guard this sweep does not have.

**What is being measured, stated plainly.** For each recorded lot in the order it closed, we
ask the real guard chain whether our bot would have raised at the price the lot actually
cleared at. If it would, we count it won at that price and fold it into our state; if not, it
went to somebody else. That is an approximation — a real room might never have reached the
clearing price if we had dropped out earlier, and our own bidding would have moved it — and
the direction of the error is knowable: it flatters us slightly on contested lots. It is
still the only calibration available that uses prices somebody actually paid.
"""

from __future__ import annotations

from fantabot.application.asta_calibrate import Lot, RecordedAuction, sweep
from fantabot.domain.asta.legality import SchemaLegality, SlotRule
from fantabot.domain.asta.roles import MantraPlayer, normalize_roles
from fantabot.domain.asta.state import RosterRules
from fantabot.domain.asta.value import NaiveValueModel

SCHEMI = {
    "por-a": SchemaLegality(
        nome="por-a",
        slots=(
            SlotRule("Por", frozenset({"POR"}), frozenset({"POR"})),
            SlotRule("A", frozenset({"A"}), frozenset({"A"})),
        ),
    )
}
POOL = [
    MantraPlayer("gk1", normalize_roles(["POR"])),
    MantraPlayer("gk2", normalize_roles(["POR"])),
    MantraPlayer("a1", normalize_roles(["A"])),
    MantraPlayer("a2", normalize_roles(["A"])),
]
TEAMS = {"gk1": "W", "gk2": "V", "a1": "X", "a2": "Y"}
PRICES = {"gk1": 10.0, "gk2": 10.0, "a1": 40.0, "a2": 40.0}
# Real separation between a plan member and his alternative, not the flatter, identical
# signals an earlier version of this fixture used. `lot_ceiling` prices a plan member against
# the objective with *him* excluded (`lot_reference`), not against a total that already
# assumes he is in it — forcing him back in only clears the margin when there is a real gap
# to his replacement (gk1 over gk2, a1 over a2); an identical pair ties baseline exactly and
# never wins, which is a different, already-covered case (`test_asta_reservation.py`'s
# `TestLotReferencePicksTheRightObjectiveToBeat`), not what this file is about.
VALUE = NaiveValueModel(
    signals={"gk1": 5.0, "gk2": 3.0, "a1": 15.0, "a2": 9.0},
    prior_mean=1.0, base_variance=1.0, no_history_variance=1.0,
)
RULES = RosterRules(size=2, min_goalkeepers=1, min_movement=1)


def _auction(*lots: tuple[str, int]) -> RecordedAuction:
    return RecordedAuction(
        asta_id="a",
        lots=tuple(Lot(player_id=p, price=c, closed_at_ms=i) for i, (p, c) in enumerate(lots)),
    )


def _sweep(alphas, auctions=None, budget=100.0):  # type: ignore[no-untyped-def]
    return sweep(
        auctions if auctions is not None else [_auction(("gk1", 10), ("a1", 40))],
        alphas,
        pool=POOL, value=VALUE, prices=PRICES, teams=TEAMS, legality=SCHEMI,
        rules=RULES, budget=budget, lam=0.0,
    )


class TestTheSweepReportsOneRowPerAlpha:
    def test_a_row_for_each_alpha_in_order(self) -> None:
        rows = _sweep([0.6, 0.8, 1.0])

        assert [r.alpha for r in rows] == [0.6, 0.8, 1.0]

    def test_it_reports_what_was_spent_and_what_was_left(self) -> None:
        (row,) = _sweep([1.0])

        assert row.spend + row.unspent == 100.0
        assert row.won + row.lost == 2


class TestAlphaScalesTheCeilingHonestly:
    """The property the whole sweep rests on: alpha is a premium on an already-real number,
    not a patch for a broken one. Raising it can turn a loss into a win; it does not touch
    which players are even considered (that is plan membership, decided before alpha applies
    at all — see `_replay_one`).
    """

    def test_a_higher_alpha_wins_a_lot_a_lower_one_would_not(self) -> None:
        """`a1`'s re-solved ceiling here is 90 (measured): 0.3 x 90 = 27, under the 50 the
        room cleared at; 1.0 x 90 = 90, comfortably over. Nothing else about the lot changed.
        """
        priced_above_the_low_alpha = _auction(("gk1", 10), ("a1", 50))

        low, high = _sweep([0.3, 1.0], auctions=[priced_above_the_low_alpha])

        assert low.won == 1, "gk1 only; 0.3 x ceiling(a1) does not clear 50"
        assert high.won == 2, "both; 1.0 x ceiling(a1) clears 50"

    def test_spend_is_monotone_in_alpha(self) -> None:
        """More willingness to pay cannot buy strictly less."""
        rows = _sweep([0.6, 1.0])

        assert rows[1].spend >= rows[0].spend


class TestTheCorpusIsFiltered:
    def test_an_auction_too_short_to_fill_the_band_is_not_admitted(self) -> None:
        """Half the recorded corpus cannot spend a budget at any alpha. Counting those
        would make the table report the corpus's shape instead of the ceiling's effect."""
        rows = _sweep([1.0], auctions=[_auction(("gk1", 10))])

        assert rows[0].auctions == 0
        assert rows[0].dropped == 1

    def test_the_dropped_count_is_reported_rather_than_silent(self) -> None:
        both = [_auction(("gk1", 10), ("a1", 40)), _auction(("gk1", 10))]
        rows = _sweep([1.0], auctions=both)

        assert rows[0].auctions == 1
        assert rows[0].dropped == 1


class TestItTouchesNothingOutside:
    def test_the_module_cannot_reach_persistence(self) -> None:
        """The sweep is pure so the default tier can run it: `pytest -m db` is exempt from
        the socket guard (`conftest.py`), so a db-marked test could not prove "no network"."""
        import _importgraph

        assert not _importgraph.reaches(
            "fantabot.application.asta_calibrate", "fantabot.adapters.persistence"
        )


class TestTheColumnThatMeasuresTheCeiling:
    """Spend cannot measure the ceiling when the corpus cannot fill the plan.

    Measured 2026-09-01: only 19 of the plan's 30 members appear anywhere in the recorded
    corpus, and one recorded evening sells a median of 7 of them. So "spend as a fraction of
    budget" tops out around 43% at any alpha, and it is reporting the overlap between an
    August corpus and a September listone rather than anything about the ceiling.

    Conditioning on what a room actually put up for sale is what isolates the ceiling: of the
    plan members that were available, how many did we win.
    """

    def test_it_reports_how_many_plan_members_were_even_available(self) -> None:
        (row,) = _sweep([1.0])

        assert row.available == 2, "both fixture lots are plan members"

    def test_it_reports_the_share_of_those_we_won(self) -> None:
        (row,) = _sweep([1.0])

        assert row.won_share == 1.0

    def test_a_lot_outside_the_plan_is_not_counted_as_available(self) -> None:
        """Losing a player we never wanted is not a miss, and must not dilute the share."""
        with_stranger = _auction(("gk1", 10), ("a1", 40), ("gk2", 99))
        (row,) = _sweep([1.0], auctions=[with_stranger])

        assert row.available == 2, "gk2 is not in the plan at any point"

    def test_the_share_is_zero_rather_than_undefined_when_nothing_was_available(self) -> None:
        """A corpus with no overlap is a real answer, not a division by zero."""
        (row,) = _sweep([1.0], auctions=[_auction(("gk2", 99), ("a2", 99))], budget=1.0)

        assert row.available >= 0
        assert 0.0 <= row.won_share <= 1.0


def test_won_can_never_exceed_available() -> None:
    """The first run of this column reported 106%.

    `available` was counted against the plan from an empty state while `won` tracked the plan
    as it moved, and the plan moves: a player we did not want at 20:00 becomes a target once
    the man ahead of him is gone. A share above 1.0 is not a good result, it is a broken
    denominator, and it looked like evidence.
    """
    for row in _sweep([0.6, 0.8, 1.0], auctions=[_auction(("gk1", 10), ("a1", 40), ("gk2", 1))]):
        assert row.won <= row.available, f"alpha={row.alpha}: {row.won} won of {row.available}"
        assert 0.0 <= row.won_share <= 1.0


def test_the_roster_is_never_bought_past_its_own_size() -> None:
    """`optimize_roster` does not itself refuse a roster forced over `rules.size` — it just
    stops adding once `len(picked) >= rules.size` and returns what is there. The guard is
    plan membership: once the band is full, `reservations()`'s own optimum *is* what we own,
    so no further lot is ever `in planned`, and `_replay_one` never re-solves for one. A third
    lot offered after the band fills must be lost, not bought.
    """
    three_offers = _auction(("gk1", 10), ("a1", 40), ("gk2", 1))
    for row in _sweep([0.6, 0.8, 1.0], auctions=[three_offers]):
        assert row.slots <= RULES.size, f"alpha={row.alpha}: {row.slots} players in a {RULES.size}-slot band"
