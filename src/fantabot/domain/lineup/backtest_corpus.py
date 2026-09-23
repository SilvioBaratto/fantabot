"""Which recorded auction rooms a backtest may replay, and why the others may not. Pure.

SPEC A16(1). The backtest needs a *rosa* — twenty-odd players one manager owned — and the
harvest recorded rooms, not rosters, so the rosters are folded back out of the sales and
the rooms that did not record one whole are dropped. Three rules, in this order:

1. the room states a shape (`num_teams`, `num_credits`);
2. the distinct buyers equal `num_teams`;
3. every buyer holds at least 23 identified players, and no buyer more than 32.

**`max_player` is read, carried and never consulted.** Measured 2026-09-23: 7 of the 17
admitted Mantra rooms declare `max_player = 25` and all 7 hold rosters above it. The
declaration is what the room's *organiser typed*; 32 is `xsltc`, what the platform allows,
and it is the number a rosa has to satisfy to be fieldable. Judging by the declaration
would throw away 7 of 17 rooms for disagreeing with their own settings.

**The rule order is pinned, and only the breakdown depends on it.** Measured over the live
corpus: in this order the Mantra rejections are `NO_SHAPE 3 / BUYER_COUNT 30 /
TOO_FEW_IDS 156 / ROSTER_TOO_LARGE 4`; swapping (3a) before (2) gives `3 / 1 / 185 / 4`.
The admitted set (17 rooms, 148 rosters) is the same either way — a room fails every rule
it fails — so what the order decides is what an operator reading the report is told, and a
report that names the wrong rule sends them to the wrong place.

**Sizes are counted over distinct ids, never rows.** There are no duplicate
`(room, buyer, id)` triples in the live data today, so a row count reads 17/148 as well;
a fold that counted rows would be wrong the first time one appeared and right until then.

Nothing here reads a database, a clock or a uuid: the ids arrive already recovered, which
is `BacktestCorpusRepository`'s job, and the rooms arrive in the order the replay will
take them.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Final, Literal

#: SPEC A16(1). Fewer identified players than this and there is no rosa to field an XI from
#: for 38 giornate — the room recorded a fragment of an auction, not an auction.
MIN_IDENTIFIED: Final[int] = 23

#: The lega's own `xsltc`, **not** a room's declared `max_player`. See the module docstring.
MAX_ROSTER: Final[int] = 32

RejectionReason = Literal["NO_SHAPE", "BUYER_COUNT", "TOO_FEW_IDS", "ROSTER_TOO_LARGE"]


@dataclass(frozen=True, slots=True)
class RoomRow:
    """One recorded room, as the read hands it over."""

    asta_id: str
    num_teams: int | None
    num_credits: int | None
    #: Read, carried, and never used in a decision.
    max_player: int | None


@dataclass(frozen=True, slots=True)
class SaleRow:
    """One player sold to one buyer. `player_id` is a fantacalcio id, already recovered."""

    asta_id: str
    buyer_team_id: str
    player_id: int


@dataclass(frozen=True, slots=True)
class Roster:
    """What one buyer walked out with."""

    buyer_team_id: str
    #: Distinct and ascending. T32 replays 38 giornate per rosa and an unordered set is a
    #: different replay every run.
    player_ids: tuple[int, ...]

    def __len__(self) -> int:
        return len(self.player_ids)


@dataclass(frozen=True, slots=True)
class BacktestRoom:
    """A room the backtest may replay, with its rosters in buyer order."""

    asta_id: str
    num_teams: int
    num_credits: int
    #: What the room declared. Recorded so the disagreement with `MAX_ROSTER` is visible,
    #: never consulted.
    declared_max_player: int | None
    rosters: tuple[Roster, ...]

    @property
    def shape(self) -> str:
        """`8x500` — the cell a recorded price is averaged within."""
        return f"{self.num_teams}x{self.num_credits}"


@dataclass(frozen=True, slots=True)
class Rejection:
    """A room the backtest may not replay, and the first rule it failed."""

    asta_id: str
    reason: RejectionReason
    detail: str


@dataclass(frozen=True, slots=True)
class ShapeCount:
    shape: str
    rooms: int
    rosters: int


@dataclass(frozen=True, slots=True)
class Corpus:
    """Everything the read found, split by whether it may be replayed."""

    rooms: tuple[BacktestRoom, ...]
    rejected: tuple[Rejection, ...]

    @property
    def roster_count(self) -> int:
        return sum(len(room.rosters) for room in self.rooms)

    def rejected_for(self, reason: RejectionReason) -> tuple[str, ...]:
        """The ids rejected for one rule, in the order they were read."""
        return tuple(r.asta_id for r in self.rejected if r.reason == reason)

    def by_shape(self) -> tuple[ShapeCount, ...]:
        """Rooms and rosters per shape, commonest first, then by shape so a tie is stable.

        The corpus is not one shape — 8x500 is 7 of the 17 Mantra rooms — and a replay that
        averaged across shapes would price a 10-team 300-credit room off an 8x500 cell.
        """
        rooms: dict[str, int] = defaultdict(int)
        rosters: dict[str, int] = defaultdict(int)
        for room in self.rooms:
            rooms[room.shape] += 1
            rosters[room.shape] += len(room.rosters)
        return tuple(
            ShapeCount(shape=shape, rooms=count, rosters=rosters[shape])
            for shape, count in sorted(rooms.items(), key=lambda kv: (-kv[1], kv[0]))
        )


def admit(
    rooms: Iterable[RoomRow],
    sales: Iterable[SaleRow],
    *,
    min_identified: int = MIN_IDENTIFIED,
    max_roster: int = MAX_ROSTER,
) -> Corpus:
    """Fold sales into rosters and judge each room. Input order is output order.

    The thresholds are parameters so the boundary can be driven without inventing 23
    synthetic players per buyer, and so T33's sweep never re-derives them.
    """
    by_room: dict[str, dict[str, set[int]]] = defaultdict(lambda: defaultdict(set))
    for sale in sales:
        by_room[sale.asta_id][sale.buyer_team_id].add(sale.player_id)

    admitted: list[BacktestRoom] = []
    rejected: list[Rejection] = []
    for room in rooms:
        buyers = by_room.get(room.asta_id, {})
        # A room that states nothing is not stating 0: FantaLab sends 0 for an unset field,
        # and taking it literally averages a recorded price inside a `0x0` cell. `num_teams`
        # is load-bearing for rule (2); `num_credits` is beyond A16(1)'s three rules and is
        # refused here because the shape is what a replay groups by. Measured 2026-09-23:
        # all 3 Mantra and all 8 Classic rooms rejected here fail on `num_teams`, so the
        # credits half costs the corpus nothing today and is a rule about the report.
        if not room.num_teams or not room.num_credits:
            rejected.append(
                Rejection(
                    room.asta_id,
                    "NO_SHAPE",
                    f"num_teams={room.num_teams} num_credits={room.num_credits}",
                )
            )
            continue
        if len(buyers) != room.num_teams:
            rejected.append(
                Rejection(
                    room.asta_id,
                    "BUYER_COUNT",
                    f"{len(buyers)} buyers, room declares {room.num_teams}",
                )
            )
            continue
        # Reachable only past rule (2), which has already established that there is one
        # buyer per declared team and that `num_teams` is not zero — so `sizes` is never
        # empty, and a `if not sizes` guard here would be a second gate on a question rule
        # (2) has already answered, making rule (2)'s own mutant survive.
        sizes = [len(ids) for ids in buyers.values()]
        if min(sizes) < min_identified:
            rejected.append(
                Rejection(
                    room.asta_id,
                    "TOO_FEW_IDS",
                    f"smallest roster {min(sizes)} < {min_identified}",
                )
            )
            continue
        if max(sizes) > max_roster:
            rejected.append(
                Rejection(
                    room.asta_id,
                    "ROSTER_TOO_LARGE",
                    f"largest roster {max(sizes)} > {max_roster}",
                )
            )
            continue
        admitted.append(
            BacktestRoom(
                asta_id=room.asta_id,
                num_teams=room.num_teams,
                num_credits=room.num_credits,
                declared_max_player=room.max_player,
                rosters=tuple(
                    Roster(buyer, tuple(sorted(ids))) for buyer, ids in sorted(buyers.items())
                ),
            )
        )
    return Corpus(tuple(admitted), tuple(rejected))


def report(corpus: Corpus) -> Sequence[str]:
    """The corpus as an operator reads it: what was admitted, per shape, and what was not."""
    lines = [
        f"admitted {len(corpus.rooms)} room(s), {corpus.roster_count} roster(s)",
        *(f"  {c.shape}: {c.rooms} room(s), {c.rosters} roster(s)" for c in corpus.by_shape()),
    ]
    reasons: dict[str, int] = defaultdict(int)
    for rejection in corpus.rejected:
        reasons[rejection.reason] += 1
    lines.extend(f"  rejected {reason}: {count}" for reason, count in sorted(reasons.items()))
    return lines
