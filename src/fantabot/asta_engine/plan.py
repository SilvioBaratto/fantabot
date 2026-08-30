"""Everything a plan needs, assembled once. The one place the value model is built.

Three commands — `asta optimize`, `asta live` and `asta bid` — each repeated the same
fifteen lines: read `quotazioni`, read the observed clearing prices, read the sentiment
feed, then derive the pool, the club map, the name map, the value model and the legality
matrix. A fourth copy lived in `tests/integration/test_asta_engine_db.py`'s `_world()`
helper, which nobody had counted.

**Why that mattered rather than being untidy.** They drifted. `asta bid` was still
planning on plain `fvm` after `asta optimize` had moved to the sentiment-adjusted model,
which meant the command that spends real credits and the command that says what to spend
disagreed about what a player was worth. On plain `fvm` that loop would chase Yildiz to 62
credits with a metatarsal fracture reported by three sources. A walk-away is "what is he
worth to us", so this is the last place in the repo where the planner and the bidder may
differ.

**The factory seam survives, and is not an accident.** `reservation.rolling_advisory`
takes `value_of: Callable[[], ValueModel]` while `reservation.reservations` takes a
`ValueModel` directly. `asta live` supplies the callable; today it returns one snapshot,
because `feed.ledger_events` materialises the whole ledger before the loop starts and
there is no "later" during which a fresher reading could arrive. The seam is what a
genuinely live `asta live` will need, so `PlanInputs` exposes both the model and a factory
over it rather than collapsing the difference.

**Split in two on purpose.** `build_plan_inputs` is pure and takes rows; `read_plan_inputs`
is the two-query shell over a `Session`. Validating `--sentiment-run` stays in the CLI,
because it raises `typer.BadParameter` and nothing in this package may import typer.

Both queries sit in the shell rather than behind helpers in the modules that reduce their
rows. `expected_prices` used to live in `prices.py`, with its repository import inside the
function body — so `prices.py` read as pure to a reader and to a grep while reaching
Postgres on every call. Keeping the reads here means the layer a module belongs to is the
one its imports say it belongs to.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING

from .legality import SchemaLegality, build_legality, load_compat
from .prices import Sale, mean_prices
from .report import build_pool, build_value
from .roles import MantraPlayer
from .sentiment import SentimentWeights
from .value import ValueModel

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from fantabot.data_sources.models import SentimentRow
    from fantabot.db.repositories.reference import QuotazioneRow


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
) -> PlanInputs:
    """Derive the plan world from two already-read tables. Pure.

    Takes rows rather than a `Session` so the derivation is testable without a database,
    which is what lets the golden harness drive it from fixtures.
    """
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


def read_plan_inputs(
    session: Session,
    *,
    season: str,
    sentiment: Mapping[str, SentimentRow] | None,
    as_of: date | None,
    tilt_k: float,
) -> PlanInputs:
    """The I/O half: two reads on one session, then the pure derivation above.

    Sales are restricted to our exact league shape — 8 teams, 500 credits — so the prices
    are directly comparable and need no budget normalization.
    """
    from fantabot.db.repositories.aste import AsteRepository
    from fantabot.db.repositories.reference import ReferenceRepository

    sales = AsteRepository(session).mantra_clearing_sales(budget=500, num_teams=8)
    return build_plan_inputs(
        ReferenceRepository(session).quotazioni(season, "mantra"),
        mean_prices(Sale(player_id, price) for player_id, price in sales),
        sentiment,
        as_of=as_of,
        tilt_k=tilt_k,
    )
