"""The plan world, derived from rows. Pure — this module reaches no database.

Split out of `asta_planner` so the import graph says what the docstring always claimed. The
two queries stay there; only the derivation lives here.

**Why the split is load-bearing rather than tidy.** `read_plan_inputs` imports `AsteRepository`
inside its body, and `tests/_importgraph` counts function-body imports on purpose — that is
where three real violations were hiding. So anything sharing a module with it reaches
`adapters.persistence`, and so does anything that takes one of its types. The live-room tracker's
one structural guarantee is that it cannot reach Postgres; taking a `PlanInputs` defined next to
a repository import would have made that guarantee unprovable.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING

from fantabot.domain.asta.legality import SchemaLegality, build_legality, load_compat
from fantabot.domain.asta.report import build_pool, build_value
from fantabot.domain.asta.roles import MantraPlayer
from fantabot.domain.asta.sentiment import SentimentWeights
from fantabot.domain.asta.value import ValueModel

if TYPE_CHECKING:
    from fantabot.domain.shared.values import QuotazioneRow, SentimentRow


@dataclass(frozen=True)
class PlanInputs:
    """The world a plan is computed against, read once."""

    pool: Sequence[MantraPlayer]
    value: ValueModel
    prices: Mapping[str, float]
    teams: Mapping[str, str]
    names: Mapping[str, str]
    roles: Mapping[str, Sequence[str]]
    legality: dict[str, SchemaLegality]
    #: The readings the value model was built from, or `None` under `--no-sentiment`.
    #: Carried because `report.format_roster` annotates drifted players from it.
    sentiment: Mapping[str, SentimentRow] | None

    def value_of(self) -> ValueModel:
        """The factory `rolling_advisory` asks for. See this module's docstring."""
        return self.value


def build_plan_inputs(
    quotazioni: Mapping[str, QuotazioneRow],
    prices: Mapping[str, float],
    sentiment: Mapping[str, SentimentRow] | None,
    *,
    as_of: date | None,
    tilt_k: float,
    excluded: Collection[str] = (),
    callable_ids: Collection[str] | None = None,
) -> PlanInputs:
    """Derive the plan world from two already-read tables. Pure.

    Takes rows rather than a `Session` so the derivation is testable without a database,
    which is what lets the golden harness drive it from fixtures.

    **`excluded` is dropped before anything is derived**, not filtered out afterwards.
    Half of it would be worse than none: `format_roster` reads `names`, the opponent
    tracker reads `roles`, the optimizer's variance term reads `teams`, and a player left
    in any of them surfaces as a name with no row behind it. It also has to happen before
    `build_value`, because the sentiment normalization pins the *pool* mean at exactly
    1.0 -- a player who cannot be bought must not move everyone else's multiplier.

    `prices` is deliberately not filtered. It comes from a different table and cannot put
    anyone back: the pool is what gates selection.

    **`callable_ids` is the same drop for a different reason.** `excluded` answers "someone
    decided not to buy him"; this answers "he cannot come up for auction at all", because
    FantaLab's listone does not carry him. Measured 2026-09-01: 41 of 570 pool players are
    absent from it — Lukaku, Nkunku, Morata, Perin, Angelino among them — and the optimizer
    put three in the plan. In a simulated mid-auction state Lukaku was the *top* walk-away of
    all twelve targets, so the headline target was a player who could never appear.

    An allowlist, not a difference. `pool_ids - set(bridge.values())` is the obvious spelling
    and it is wrong: `listone.parse` keeps fantacalcio ids as `int` while pool ids are `str`,
    so the subtraction removes nothing and the whole pool would be excluded. `None` means no
    filtering; an empty collection means the bridge resolved nothing, which is a real failure
    and empties the pool rather than quietly passing everyone through.
    """
    if callable_ids is not None:
        allowed = set(callable_ids)
        quotazioni = {pid: row for pid, row in quotazioni.items() if pid in allowed}
    if excluded:
        quotazioni = {pid: row for pid, row in quotazioni.items() if pid not in excluded}
    roles = {pid: row.ruoli_codice for pid, row in quotazioni.items()}
    return PlanInputs(
        pool=build_pool(roles),
        value=build_value(
            {pid: row.fvm for pid, row in quotazioni.items()},
            priced_ids=set(prices),
            sentiment=sentiment,
            as_of=as_of if sentiment else None,
            weights=SentimentWeights(k=tilt_k),
        ),
        prices=prices,
        teams={pid: row.squadra for pid, row in quotazioni.items()},
        names={pid: row.nome for pid, row in quotazioni.items()},
        roles=roles,
        legality=build_legality(load_compat()),
        sentiment=sentiment,
    )
