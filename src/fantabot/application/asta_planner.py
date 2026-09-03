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

from collections.abc import Collection, Mapping
from datetime import date
from typing import TYPE_CHECKING

from fantabot.application.plan_inputs import PlanInputs, build_plan_inputs
from fantabot.domain.asta.prices import Sale, mean_prices

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from fantabot.domain.shared.values import SentimentRow


def read_plan_inputs(
    session: Session,
    *,
    season: str,
    sentiment: Mapping[str, SentimentRow] | None,
    as_of: date | None,
    tilt_k: float,
    callable_ids: Collection[str] | None = None,
    num_teams: int = 8,
    num_credits: int = 500,
    listone: str = "mantra",
) -> PlanInputs:
    """The I/O half: three reads on one session, then the pure derivation above.

    Sales are restricted to the league's shape so the prices are directly comparable and need
    no budget normalization. **The shape is a parameter, not a constant.** It was written in
    as 8x500, which is our room, so nothing looked wrong — and `docs/fantalab/00 §13` is
    explicit that a rule written into the code is a bug, because the next asta is the
    riparazione in January, or a friend's league. The corpus holds 68 recorded 10x500 rooms
    that would otherwise have been priced off somebody else's game.

    The defaults are our league, so no existing caller changes and the golden fixtures stand.

    `callable_ids` is forwarded untouched. It has to live here as well as on the pure half:
    this is the only door — `asta optimize`, `asta live` and `asta bid` all come through it —
    and it holds a `Session`, not a listone bridge. The bridge is fetched in the interface,
    where the network lives, so the caller supplies the set and this passes it along.
    """
    from fantabot.adapters.persistence.repositories.aste import AsteRepository
    from fantabot.adapters.persistence.repositories.reference import ReferenceRepository

    reference = ReferenceRepository(session)
    # The clearing-price corpus is Mantra-only today: no Classic asta has been recorded, so a
    # Classic run prices every player as no-history (same mean, a wider band) from fvm alone.
    # When a Classic corpus exists, generalise this read on asta_type.
    sales = (
        AsteRepository(session).mantra_clearing_sales(budget=num_credits, num_teams=num_teams)
        if listone == "mantra"
        else []
    )
    return build_plan_inputs(
        reference.quotazioni(season, listone),
        mean_prices(Sale(player_id, price) for player_id, price in sales),
        sentiment,
        as_of=as_of,
        tilt_k=tilt_k,
        # Read every run, not cached: the listone lags reality and this is the only
        # mechanism that can drop a player the site still lists. See
        # `adapters/persistence/models/exclusions.py`.
        excluded=reference.excluded_player_ids(),
        callable_ids=callable_ids,
        listone=listone,
    )
