import pytest

from fantabot.models import AuctionListing, Player, Role, RosterSlot, ScoredPlayer
from fantabot.strategy import allocate_auction_budget, decide_bid, pick_starting_lineup


def _slot(
    id_: str, role: Role, score: float, available: bool = True, fieldable: bool = True
) -> RosterSlot:
    player = Player(id=id_, name=id_, role=role, team="X")
    scored = ScoredPlayer(
        player=player, projected_score=score, is_available=available, is_in_lineup_slot=fieldable
    )
    return RosterSlot(player=player, scored=scored)


def _full_roster() -> list[RosterSlot]:
    roster = [_slot("gk1", Role.GOALKEEPER, 6.0), _slot("gk2", Role.GOALKEEPER, 5.5)]
    roster += [_slot(f"d{i}", Role.DEFENDER, 6.0 + i * 0.1) for i in range(6)]
    roster += [_slot(f"c{i}", Role.MIDFIELDER, 6.0 + i * 0.1) for i in range(6)]
    roster += [_slot(f"a{i}", Role.ATTACKER, 6.0 + i * 0.1) for i in range(4)]
    return roster


def test_pick_starting_lineup_valid_formation() -> None:
    lineup = pick_starting_lineup(_full_roster())
    d, c, a = lineup.formation
    assert d + c + a == 10
    assert (d, c, a) in {
        (3, 4, 3),
        (3, 5, 2),
        (4, 3, 3),
        (4, 4, 2),
        (4, 5, 1),
        (5, 3, 2),
        (5, 4, 1),
    }
    assert len(lineup.starters) == 10
    assert lineup.goalkeeper.id == "gk1"  # higher score


def test_pick_starting_lineup_picks_highest_scorers() -> None:
    lineup = pick_starting_lineup(_full_roster())
    started_defenders = [p for p in lineup.starters if p.id.startswith("d")]
    # d5 has the highest score (6.0 + 5*0.1), must start over d0
    assert "d5" in {p.id for p in started_defenders}


def test_pick_starting_lineup_skips_unavailable_players() -> None:
    roster = _full_roster()
    roster[2] = _slot("d0", Role.DEFENDER, 9.9, available=False)  # best defender injured
    lineup = pick_starting_lineup(roster)
    assert "d0" not in {p.id for p in lineup.starters}


def test_pick_starting_lineup_raises_when_infeasible() -> None:
    roster = [_slot("gk1", Role.GOALKEEPER, 6.0)]
    roster += [_slot("d0", Role.DEFENDER, 6.0)]  # only 1 defender, no formation needs <3
    with pytest.raises(ValueError):
        pick_starting_lineup(roster)


def test_allocate_auction_budget_sums_to_total() -> None:
    budget = allocate_auction_budget(500)
    assert sum(budget.values()) == 500


def test_allocate_auction_budget_rejects_bad_shares() -> None:
    with pytest.raises(ValueError):
        allocate_auction_budget(500, {Role.GOALKEEPER: 0.5})


def test_decide_bid_within_budget() -> None:
    player = Player(id="a1", name="Striker", role=Role.ATTACKER, team="X")
    listing = AuctionListing(
        player=player, base_price=10, current_bid=50, current_bidder="rival", closes_utc=None
    )
    decision = decide_bid(listing, target_price=80, remaining_role_budget=100)
    assert decision is not None
    assert decision.amount == 51


def test_decide_bid_passes_when_over_ceiling() -> None:
    player = Player(id="a1", name="Striker", role=Role.ATTACKER, team="X")
    listing = AuctionListing(
        player=player, base_price=10, current_bid=80, current_bidder="rival", closes_utc=None
    )
    assert decide_bid(listing, target_price=80, remaining_role_budget=100) is None
    assert decide_bid(listing, target_price=100, remaining_role_budget=5) is None
