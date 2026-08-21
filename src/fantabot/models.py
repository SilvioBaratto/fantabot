from dataclasses import dataclass
from enum import StrEnum


class Role(StrEnum):
    GOALKEEPER = "P"
    DEFENDER = "D"
    MIDFIELDER = "C"
    ATTACKER = "A"


# valid classic-mode outfield splits (defenders, midfielders, attackers), always summing to 10
VALID_FORMATIONS: set[tuple[int, int, int]] = {
    (3, 4, 3),
    (3, 5, 2),
    (4, 3, 3),
    (4, 4, 2),
    (4, 5, 1),
    (5, 3, 2),
    (5, 4, 1),
}


@dataclass(frozen=True)
class Player:
    id: str
    name: str
    role: Role
    team: str


@dataclass(frozen=True)
class ScoredPlayer:
    player: Player
    projected_score: float
    is_available: bool  # false if injured/suspended/benched per data source
    is_in_lineup_slot: bool  # can be started today per the site's roster page


@dataclass(frozen=True)
class RosterSlot:
    """One player owned on the fantateam, whether or not started this matchday."""

    player: Player
    scored: ScoredPlayer


@dataclass(frozen=True)
class Lineup:
    formation: tuple[int, int, int]  # (D, C, A)
    goalkeeper: Player
    starters: tuple[Player, ...]  # 10 outfield players
    bench: tuple[Player, ...]
    captain: Player
    vice_captain: Player


@dataclass(frozen=True)
class MatchdayInfo:
    matchday: int
    deadline_utc: str  # ISO 8601


@dataclass(frozen=True)
class AuctionListing:
    player: Player
    base_price: int
    current_bid: int
    current_bidder: str | None
    closes_utc: str | None  # None if still live/no countdown visible


@dataclass(frozen=True)
class BidDecision:
    player: Player
    amount: int
    reasoning: str
