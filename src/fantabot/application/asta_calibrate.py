"""Replay recorded aste at several walk-away floors, and report what each would have spent.

`--floor-alpha` is the number that decides what the bot pays, and it is hand-set. SPEC A6
refuses arming until it has been replayed against auctions that really happened. This is that
replay: 48 Mantra auctions in our exact 8x500 shape sit in Postgres with 6,554 sales and a
full bid ladder each, collected on 2026-08-26/27.

**Pure on purpose.** It takes lots already read and returns a table, so the default test tier
can drive it — `pytest -m db` is exempt from the socket guard (`tests/conftest.py`), so a
db-marked test could not prove this opens nothing.

**What the number means, stated plainly.** For each lot in the order it closed, the real guard
chain is asked whether our bot would have raised at the price the lot actually cleared at. If
it would, we count it won at that price and fold it into our state. That is an approximation
in a knowable direction: a real room might never have reached that price had we dropped out
earlier, and our own bidding would have pushed it higher. It flatters us slightly on contested
lots. It is still the only calibration available that uses prices somebody actually paid, and
a floor chosen against it is a floor chosen against evidence rather than taste.

**Auctions too short to fill a roster are dropped, and the count is printed.** Many recorded
rooms stop after a handful of lots; a sweep that counted them would report the corpus's shape
rather than the floor's effect. A silent filter reads as full coverage, so it is a column.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace

from fantabot.domain.asta.bid import Seat, decide_bid
from fantabot.domain.asta.legality import SchemaLegality, fieldable_schemi
from fantabot.domain.asta.optimizer import InfeasibleRoster
from fantabot.domain.asta.reservation import price_floor, reservations
from fantabot.domain.asta.roles import MantraPlayer
from fantabot.domain.asta.state import AstaState, RosterRules
from fantabot.domain.asta.value import ValueModel

#: Our seat in a replay. The ids are arbitrary — nothing is sent — but they have to differ
#: from the recorded buyer's, or `decide_bid`'s "don't bid against yourself" guard fires.
REPLAY_SEAT = Seat(fantateam_id="__us__", user_id="__us__")

#: Far past any recorded `last_bid_time`, so the 500 ms client floor never refuses a rung.
#: Without this the sweep would be measuring the debounce rather than the floor.
REPLAY_NOW_MS = 1 << 62


@dataclass(frozen=True)
class Lot:
    """One recorded sale: who it was, what it cleared at, and when it closed."""

    player_id: str
    price: int
    closed_at_ms: int


@dataclass(frozen=True)
class RecordedAuction:
    """One evening, its lots in the order they closed."""

    asta_id: str
    lots: tuple[Lot, ...]


@dataclass(frozen=True)
class CalibrationRow:
    """What one alpha would have done across the admitted corpus."""

    alpha: float
    auctions: int
    dropped: int
    spend: float
    unspent: float
    slots: float
    schemi: float
    won: int
    lost: int

    def line(self) -> str:
        return (
            f"{self.alpha:>5.2f} {self.spend:>8.0f} {self.unspent:>9.0f} "
            f"{self.slots:>7.1f} {self.schemi:>8.1f} {self.won:>6} {self.lost:>7}"
        )


HEADER = f"{'alpha':>5} {'spend':>8} {'unspent':>9} {'slots':>7} {'schemi':>8} {'won':>6} {'lost':>7}"


def admits(auction: RecordedAuction, rules: RosterRules) -> bool:
    """Could this auction have filled a roster at all? Pure.

    The test is the corpus's, not the bot's: an evening with fewer lots than the band needs
    cannot spend a budget at any alpha, so including it would drag every column toward the
    same floor regardless of what the floor does.
    """
    return len(auction.lots) >= rules.size


def _replay_one(
    auction: RecordedAuction,
    *,
    floor: object,
    pool: Sequence[MantraPlayer],
    value: ValueModel,
    prices: Mapping[str, float],
    teams: Mapping[str, str],
    legality: dict[str, SchemaLegality],
    rules: RosterRules,
    budget: float,
    lam: float,
) -> tuple[AstaState, int, int]:
    """One evening at one alpha: the state we end with, and how many lots we won and lost."""
    state = AstaState(total_budget=budget)
    won = lost = 0

    # The plan is re-solved only when it could have changed: after a lot we won, and after a
    # lot somebody else won that the plan was counting on. A naive re-solve per lot costs one
    # full optimisation each — 45 auctions x ~140 lots x 5 alphas is 31,500 of them, about
    # forty minutes — and almost every one returns the same plan as the last, because most
    # lots are players we were never going to buy.
    cached: dict[str, float] | None = None
    planned: frozenset[str] = frozenset()

    for lot in sorted(auction.lots, key=lambda item: item.closed_at_ms):
        if lot.player_id in state.taken:
            continue
        if cached is None:
            try:
                plan, cached = reservations(
                    state, pool, value=value, prices=prices, teams=teams, legality=legality,
                    rules=rules, lam=lam, n_targets=None, floor=floor,  # type: ignore[arg-type]
                )
            except InfeasibleRoster:
                # The rosa is full or unfinishable; the rest of the evening is somebody else's.
                break
            planned = frozenset(plan.optimal.player_ids)

        walkaways = cached
        if lot.player_id in planned:
            # A player the plan wanted is leaving the board either way, so the next lot needs
            # a fresh plan whether we win him or not.
            cached = None

        walk_away = walkaways.get(lot.player_id)
        if walk_away is None:
            lost += 1
            state = replace(state, taken=state.taken | {lot.player_id})
            continue

        # The recorded clearing price, offered to the real guard chain as a lot standing one
        # rung below it: winning means being willing to go one higher than the room did.
        snapshot = {
            "player_id": lot.player_id,
            "price": lot.price - 1,
            "user_id": "__them__",
            "last_bid_time": 0,
        }
        payload = decide_bid(
            snapshot,
            REPLAY_SEAT,
            auction.asta_id,
            target=lot.player_id,
            walk_away=int(walk_away),
            remaining_budget=int(state.remaining_budget),
            now_ms=REPLAY_NOW_MS,
            max_cap=None,
        )
        if payload is None:
            lost += 1
            state = replace(state, taken=state.taken | {lot.player_id})
            continue

        won += 1
        cached = None  # our own state moved; every walk-away below it is stale
        state = replace(
            state,
            owned=(*state.owned, lot.player_id),
            spent=state.spent + lot.price,
            taken=state.taken | {lot.player_id},
        )

    return state, won, lost


def sweep(
    auctions: Sequence[RecordedAuction],
    alphas: Sequence[float],
    *,
    pool: Sequence[MantraPlayer],
    value: ValueModel,
    prices: Mapping[str, float],
    teams: Mapping[str, str],
    legality: dict[str, SchemaLegality],
    rules: RosterRules = RosterRules(),
    budget: float = 500.0,
    lam: float = 0.3,
) -> list[CalibrationRow]:
    """One row per alpha, averaged over the admitted auctions. Pure, and opens nothing."""
    admitted = [a for a in auctions if admits(a, rules)]
    dropped = len(auctions) - len(admitted)

    rows: list[CalibrationRow] = []
    for alpha in alphas:
        floor = price_floor(alpha, prices)
        spends: list[float] = []
        slots: list[int] = []
        schemi: list[int] = []
        won = lost = 0

        for auction in admitted:
            state, auction_won, auction_lost = _replay_one(
                auction, floor=floor, pool=pool, value=value, prices=prices, teams=teams,
                legality=legality, rules=rules, budget=budget, lam=lam,
            )
            spends.append(state.spent)
            slots.append(len(state.owned))
            owned_players = [player for player in pool if player.id in set(state.owned)]
            schemi.append(len(fieldable_schemi(owned_players, legality)))
            won += auction_won
            lost += auction_lost

        n = len(admitted) or 1
        mean_spend = sum(spends) / n
        rows.append(
            CalibrationRow(
                alpha=alpha,
                auctions=len(admitted),
                dropped=dropped,
                spend=mean_spend,
                unspent=budget - mean_spend,
                slots=sum(slots) / n,
                schemi=sum(schemi) / n,
                won=won,
                lost=lost,
            )
        )
    return rows
