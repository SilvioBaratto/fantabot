"""The rolling advisory, composed once: what `asta live` folds and what the app renders.

`asta live` was one of the last Typer bodies holding a decision the app also needs. Its body
resolved the ledger's uuids against the listone, read the world, folded `rolling_advisory`
over every sale and printed the result — and every one of those steps is a place a second
caller diverges. `CLAUDE.md` records the shape twice: three commands each grew their own
value model, and `GET /asta/plan` built a plan differing from `asta optimize`'s in ten
inputs. The two arguments a second caller forgets are exactly the two this module keeps —
`callable_ids`, without which the advisory can head its list with a player who cannot be
called, and the corpus shape, without which a 10x650 room is priced against somebody else's
8x500 game.

**`events` is a parameter, not a read.** The parity phase's spec, T20 (archived, not in
this checkout) keeps `--replay` as developer machinery and CLI-only; taking events rather
than fetching them is what makes that one fold over two sources instead of two folds. The
terminal supplies either a recorded file or the live `purchases/<fl>` ledger; the app
supplies the ledger.

**This layer decides; it does not present.** `format_advisory` and `format_opponents` stay in
`domain/asta` where the terminal reaches them, and the values here are what a screen renders:
the distinction they already draw between *chase* and *freely replaceable* is carried as a
boolean rather than a sentence, because a surface that had to re-derive it would eventually
say "chase, walk-away 0" — an instruction to do the one thing the bidder will never do.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import TYPE_CHECKING, cast

from fantabot.application.plan_inputs import PlanInputs
from fantabot.application.plan_request import (
    DEFAULT_LAM,
    DEFAULT_NUM_CREDITS,
    DEFAULT_NUM_TEAMS,
    EmptyPool,
    resolve_sentiment,
)
from fantabot.domain.asta.live import AssignmentEvent, resolve_ids
from fantabot.domain.asta.opponents import MIN_BID, OpponentState, track_opponents
from fantabot.domain.asta.state import AstaState

if TYPE_CHECKING:  # pragma: no cover - typing only
    from sqlalchemy.orm import Session

    from fantabot.domain.asta.roles import MantraPlayer
    from fantabot.domain.asta.state import OptimizationResult


@dataclass(frozen=True, slots=True)
class AdvisoryRequest:
    """Everything an advisory is built from. Frozen and comparable, like `PlanRequest`.

    Comparable is the point there and here: it is what lets a test pin the request the CLI
    builds against the one the endpoint builds, so the two cannot drift again without a test
    naming the field that moved.
    """

    our_team_id: str
    season: str
    #: `"mantra"` or `"classic"`. **Not defaulted**, and that is the whole point of it:
    #: `read_plan_inputs`' own `listone` defaults to Mantra and selects *both* the pool and
    #: the corpus, so a Classic room advised without it is headed by players who are not in
    #: the Classic listone at all, priced off another game — and nothing raises. It is the
    #: 2026-09-05 defect, where the format was in a query's name *and* pinned in its filter
    #: and a Classic plan bought its 25-man roster for 25 credits of 500.
    listone: str
    #: The calendar, as a value. The asta feature reads the clock in exactly one place per
    #: surface, and it is not this layer.
    as_of: date
    budget: float
    #: Risk aversion: the `lam` in the optimizer's `sum(mu) - lam*Var`. **From the shared
    #: constant, never a literal.** This was the *third* declaration of that number and it
    #: read `0.0` until 2026-09-24, after both the six CLI commands and both routes had
    #: been unified on `DEFAULT_LAM` — the same split the constant was lifted to close,
    #: one layer down and invisible because both of this object's callers state `lam`.
    #:
    #: What a wrong default here buys is a mutation that compiles, runs and says nothing:
    #: drop `lam=lam` from `GET /asta/advisory`'s `AdvisoryRequest(...)` and the surface an
    #: operator watches *during* the auction prices every lot against `sum(mu)` while the
    #: plan it was briefed from and the bidder spending the credits solve
    #: `sum(mu) - 0.3*Var`. `api/tests/parity/test_parity_asta_defaults.py` sweeps the
    #: application package for this field rather than naming it, because the version that
    #: named two sites is the version that wrote *this* one down in a comment instead of
    #: failing on it.
    lam: float = DEFAULT_LAM
    tilt_k: float = 0.25
    sentiment: bool = True
    sentiment_run: date | None = None
    #: The room's own shape, never a default carried down from a different lega.
    num_teams: int = DEFAULT_NUM_TEAMS
    num_credits: int = DEFAULT_NUM_CREDITS


@dataclass(frozen=True, slots=True)
class AdvisoryTarget:
    """One member of the target roster, and the most we would pay for him."""

    player_id: str
    nome: str
    walk_away: int
    #: False when the walk-away is under one credit. `reservations` clamps a negative
    #: marginal to zero — which means only that he is freely replaceable — and the bidder
    #: refuses at every price, because its smallest raise is `current + step`. He stays on
    #: the list: he is in the target roster and the operator should see him.
    chase: bool


@dataclass(frozen=True, slots=True)
class AdvisoryOpponent:
    """A rival as the sale feed reconstructs him."""

    team_id: str
    players: int
    spent: int
    remaining: int


@dataclass(frozen=True, slots=True)
class Advisory:
    """The rolling advisory after the last sale, and the world it was computed against.

    No `request` field, deliberately: `PlannedRoster` carries one so a parity test can pin
    the CLI's request against the endpoint's, and no such test exists here. A field nobody
    reads is a field that stops being true without anything saying so.
    """

    targets: tuple[AdvisoryTarget, ...]
    opponents: tuple[AdvisoryOpponent, ...]
    #: Sales actually folded — every one the listone could name.
    sales: int
    #: Sales the listone could not name. Each is a purchase nobody subtracted, so a rival's
    #: budget and that player's availability are both wrong until it is explained.
    dropped_sales: int
    total_cost: int
    objective: float
    world: PlanInputs
    #: The raw plan and the raw walk-aways, for the renderers that already print them.
    #: `PlannedRoster`'s reason, verbatim: the world travels with the result because every
    #: renderer needs it, and returning only the derived shape is what made each caller
    #: re-read the world to display it — which is how they came to read it differently.
    #: `domain/asta/opponents.py`'s `format_advisory` and `format_opponents` take exactly
    #: these three, and the terminal's output is a recorded golden.
    result: OptimizationResult | None = None
    walkaways: Mapping[str, float] = field(default_factory=dict)
    rivals: Mapping[str, OpponentState] = field(default_factory=dict)


def build_advisory(
    session: Session,
    request: AdvisoryRequest,
    *,
    events: Iterable[AssignmentEvent],
    bridge: Mapping[str, int],
) -> Advisory:
    """Fold the sale ledger into a target roster and a walk-away each. The one door.

    `InfeasibleRoster` is deliberately not caught, for `build_plan`'s reason: it says the
    rosa cannot be built at all, which is a different screen from every other failure.
    """
    from fantabot.adapters.persistence.news_sentiment import NewsSentimentSource
    from fantabot.application import asta_planner
    from fantabot.domain.asta import reservation

    if request.listone not in ("mantra", "classic"):
        # Refused here rather than defaulted, because the default is the defect. Both
        # surfaces validate their own option — a Typer body raises `BadParameter`, a route
        # answers an outcome — and this is the layer they both pass through.
        raise ValueError(
            f"{request.listone!r} is not a listone. Use 'mantra' or 'classic'."
        )

    sales, unknown = resolve_ids(list(events), bridge)

    rows = resolve_sentiment(
        NewsSentimentSource(session), enabled=request.sentiment, run=request.sentiment_run
    )
    world = asta_planner.read_plan_inputs(
        session,
        season=request.season,
        sentiment=rows,
        as_of=request.as_of,
        tilt_k=request.tilt_k,
        # The same narrowing the bidder applies. Without it the advisory an operator bids
        # by hand from can head its list with a player who can never come up for auction —
        # 41 of 570 are absent from the listone (defect B3). `None`, never an empty set:
        # `read_plan_inputs` reads an empty collection as a real, total exclusion.
        callable_ids=frozenset(str(fid) for fid in bridge.values()) or None,
        listone=request.listone,
        num_teams=request.num_teams,
        num_credits=request.num_credits,
    )
    if not world.pool:
        raise EmptyPool(
            f"no {request.listone} players for season {request.season} — nothing to advise on."
        )

    last = None
    for step in reservation.rolling_advisory(
        AstaState(total_budget=request.budget),
        cast("Sequence[MantraPlayer]", world.pool),
        sales,
        our_team_id=request.our_team_id,
        value_of=world.value_of,
        prices=world.prices,
        teams=world.teams,
        legality=world.legality,
        lam=request.lam,
    ):
        last = step

    targets: tuple[AdvisoryTarget, ...] = ()
    total_cost = 0
    objective = 0.0
    result = None
    walkaways: Mapping[str, float] = {}
    if last is not None:
        _, _, result, walkaways = last
        total_cost = int(getattr(result.optimal, "total_cost", 0))
        objective = float(result.optimal.objective)
        targets = tuple(
            AdvisoryTarget(
                player_id=player_id,
                nome=world.names.get(player_id, player_id),
                walk_away=int(walkaway),
                chase=walkaway >= MIN_BID,
            )
            # Highest first, the order `format_advisory` prints and the order an operator
            # reads under time pressure: the lot on the block is usually near the top.
            for player_id, walkaway in sorted(
                walkaways.items(), key=lambda kv: kv[1], reverse=True
            )
        )

    rivals = track_opponents(sales, our_team_id=request.our_team_id, roles_by_id=world.roles)
    opponents = tuple(
        AdvisoryOpponent(
            team_id=team_id,
            players=len(rival.players),
            spent=rival.spent,
            # Against the **room's** declared credits, not ours. The two are the same number
            # only where every seat started equal — the ordinary case, and exactly why
            # taking it from the wrong place would never be noticed.
            remaining=rival.remaining(int(request.num_credits)),
        )
        for team_id, rival in sorted(rivals.items(), key=lambda kv: kv[1].spent, reverse=True)
    )

    return Advisory(
        targets=targets,
        opponents=opponents,
        sales=len(sales),
        dropped_sales=len(unknown),
        total_cost=total_cost,
        objective=objective,
        world=world,
        result=result,
        walkaways=walkaways,
        rivals=rivals,
    )


__all__ = [
    "Advisory",
    "AdvisoryOpponent",
    "AdvisoryRequest",
    "AdvisoryTarget",
    "build_advisory",
]

