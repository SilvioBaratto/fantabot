"""Pure decision logic — no I/O, no Playwright. Fully unit-testable without
touching the real site. lineup.py / auction.py call into this and handle the
browser side separately.
"""

from fantabot.models import (
    VALID_FORMATIONS,
    AuctionListing,
    BidDecision,
    Lineup,
    Role,
    RosterSlot,
)


def _best_formation_for(available_by_role: dict[Role, int]) -> tuple[int, int, int]:
    """Pick the valid formation that starts the most fielded-eligible players,
    preferring more attackers when tied (attackers swing fantacalcio scores most).
    """
    d_avail = available_by_role.get(Role.DEFENDER, 0)
    c_avail = available_by_role.get(Role.MIDFIELDER, 0)
    a_avail = available_by_role.get(Role.ATTACKER, 0)

    feasible = [
        f for f in VALID_FORMATIONS if f[0] <= d_avail and f[1] <= c_avail and f[2] <= a_avail
    ]
    if not feasible:
        raise ValueError(
            f"No valid formation fits available roster (D={d_avail}, C={c_avail}, A={a_avail})"
        )
    return max(feasible, key=lambda f: (f[2], f[1], f[0]))


def pick_starting_lineup(roster: list[RosterSlot]) -> Lineup:
    fieldable = [r for r in roster if r.scored.is_available and r.scored.is_in_lineup_slot]

    goalkeepers = sorted(
        (r for r in fieldable if r.player.role == Role.GOALKEEPER),
        key=lambda r: r.scored.projected_score,
        reverse=True,
    )
    if not goalkeepers:
        raise ValueError("No available goalkeeper to start")
    goalkeeper = goalkeepers[0].player

    by_role: dict[Role, list[RosterSlot]] = {
        Role.DEFENDER: [],
        Role.MIDFIELDER: [],
        Role.ATTACKER: [],
    }
    for r in fieldable:
        if r.player.role in by_role:
            by_role[r.player.role].append(r)
    for role_list in by_role.values():
        role_list.sort(key=lambda r: r.scored.projected_score, reverse=True)

    formation = _best_formation_for({role: len(lst) for role, lst in by_role.items()})
    d_n, c_n, a_n = formation

    starters_slots = (
        by_role[Role.DEFENDER][:d_n] + by_role[Role.MIDFIELDER][:c_n] + by_role[Role.ATTACKER][:a_n]
    )
    starters = tuple(r.player for r in starters_slots)

    started_ids = {p.id for p in starters} | {goalkeeper.id}
    bench = tuple(r.player for r in roster if r.player.id not in started_ids)

    ranked_starters = sorted(starters_slots, key=lambda r: r.scored.projected_score, reverse=True)
    if len(ranked_starters) < 2:
        raise ValueError("Not enough starters to assign captain/vice-captain")
    captain = ranked_starters[0].player
    vice_captain = ranked_starters[1].player

    return Lineup(
        formation=formation,
        goalkeeper=goalkeeper,
        starters=starters,
        bench=bench,
        captain=captain,
        vice_captain=vice_captain,
    )


def allocate_auction_budget(
    total_budget: int,
    role_share: dict[Role, float] | None = None,
) -> dict[Role, int]:
    """Split total credits across roles. Default split is a common classic-mode
    heuristic (attackers/midfielders cost more than keepers/defenders at auction);
    tune role_share once real market prices are known.
    """
    share = role_share or {
        Role.GOALKEEPER: 0.05,
        Role.DEFENDER: 0.15,
        Role.MIDFIELDER: 0.35,
        Role.ATTACKER: 0.45,
    }
    if abs(sum(share.values()) - 1.0) > 1e-6:
        raise ValueError("role_share must sum to 1.0")
    return {role: round(total_budget * pct) for role, pct in share.items()}


def decide_bid(
    listing: AuctionListing,
    target_price: int,
    remaining_role_budget: int,
) -> BidDecision | None:
    """Bid one credit over the current bid, capped at whichever is lower:
    the player's target price or what's left in that role's budget slice.
    Returns None (pass) if already at/over the cap.
    """
    ceiling = min(target_price, remaining_role_budget)
    next_bid = listing.current_bid + 1
    if next_bid > ceiling:
        return None
    return BidDecision(
        player=listing.player,
        amount=next_bid,
        reasoning=f"next bid {next_bid} <= ceiling {ceiling} (target={target_price}, budget_left={remaining_role_budget})",
    )
