"""`asta optimize` and `GET /asta/plan`, on one database, at one frozen date.

This is the divergence the whole phase is named for. `SPEC.md` §11.1 counts **ten**
inputs that differ, and the two that matter most are a pair: the endpoint passes
`sentiment=None` and `tilt_k=1.0`. `sentiment=None` **is the ablation control** — the
experiment's other arm, plain `fvm`, which on the 2026-08-28 data chases a player with a
metatarsal fracture to 62 credits. The page shows it to an operator as advice. `tilt_k`
is 4x the CLI's 0.25 and inert only because there is nothing to tilt.

**The comparison is on decision content, never rendered text.** A test that diffed a Rich
table against JSON would fail on a column width, and a test that fails for a reason nobody
believes gets deleted. So the CLI's own `read_plan_inputs` + `optimize_roster` are driven
here through the same seam the command uses, and the endpoint is called over HTTP; what is
compared is which players were bought and for how much.

1.5 replaces the `xfail` below with a golden-dict test pinning the `PlanRequest` both
sides build. The marker is `strict`, so the day the two agree this file fails until
somebody deletes the marker — the ratchet discipline `test_layers.py` uses, and the reason
it is a marker here rather than a comment.
"""

from __future__ import annotations

from datetime import date

import pytest
from fastapi.testclient import TestClient

from .conftest import SeededWorld, cli_session


def _cli_plan(world: SeededWorld, today: date) -> dict[str, object]:
    """What `asta optimize` decides: who, at what price, at what cost, and worth what.

    **`objective` is in the comparison and is the point of it.** On this seed the two
    sides buy the same twelve players — the budget is not binding, so both take the top of
    the pool — and disagree about what that rosa is *worth*: 344.2 against 369.0. A test
    comparing membership alone would report parity between a plan built on the sentiment
    model and one built on its ablation control. The number an operator reads off the page
    is the second one.

    Driven through the command's own inputs rather than by parsing its table. The seam is
    `read_plan_inputs` + `optimize_roster`, which is exactly what `asta_optimize` calls —
    and what 1.4 lifts into `build_plan(session, request)`, at which point this helper
    becomes one call.
    """
    from fantabot.adapters.persistence.news_sentiment import NewsSentimentSource
    from fantabot.application.asta_planner import read_plan_inputs
    from fantabot.domain.asta.optimizer import optimize_roster
    from fantabot.domain.asta.sentiment import SentimentWeights
    from fantabot.domain.asta.state import AstaState, RosterRules
    from fantabot.interface.asta import sentiment_rows

    with cli_session() as session:
        rows = sentiment_rows(NewsSentimentSource(session), enabled=True, run="")
        inputs = read_plan_inputs(
            session,
            season=world.season,
            sentiment=rows,
            as_of=today,
            tilt_k=SentimentWeights().k,
            callable_ids=None,
            listone=world.listone,
            num_teams=world.num_teams,
            num_credits=world.budget,
        )
    result = optimize_roster(
        AstaState(total_budget=float(world.budget)),
        inputs.pool,
        value=inputs.value,
        prices=inputs.prices,
        teams=inputs.teams,
        legality=inputs.legality,
        rules=RosterRules(size=world.roster_size, min_goalkeepers=world.min_roles[0],
                          min_movement=world.min_roles[1]),
        lam=0.0,
        n_fallbacks=0,
    )
    return {
        "players": {pid: float(inputs.prices.get(pid, 0.0)) for pid in result.optimal.player_ids},
        "total_cost": round(float(result.optimal.total_cost), 6),
        "objective": round(float(result.optimal.objective), 6),
    }


def test_the_seeded_world_is_plannable_at_all(
    seeded_db: SeededWorld, frozen_today: date, api: TestClient
) -> None:
    """The guard that makes every other assertion in this file mean something.

    Two empty results agree perfectly. `test_a_torn_seed_is_not_a_pass` is what this is:
    if the endpoint says `found=false` the parity assertions below are comparing nothing.
    """
    body = api.get(
        "/api/v1/asta/plan",
        params={"league_id": seeded_db.league_id, "season": seeded_db.season},
    ).json()

    assert body["found"] is True, body
    assert body["players"], "the endpoint planned over an empty pool"

    cli = _cli_plan(seeded_db, frozen_today)
    assert cli["players"], "the CLI planned over an empty pool"
    # The seed is a *choice*, not a forced buy: eighteen candidates for twelve slots. A
    # fixture where the rosa is the pool makes both sides agree about a decision neither
    # of them made, which is how the first version of this test passed.
    assert len(cli["players"]) < len(seeded_db.player_ids)


def test_both_sides_read_the_same_format_and_budget(
    seeded_db: SeededWorld, frozen_today: date, api: TestClient
) -> None:
    """The inputs that already agree, pinned so a later change cannot quietly split them.

    The format is *detected*, never configured — `role_groups` 1 is Classic and 2 is
    Mantra — and the budget comes from the lega's own snapshot on both sides.
    """
    body = api.get(
        "/api/v1/asta/plan",
        params={"league_id": seeded_db.league_id, "season": seeded_db.season},
    ).json()

    assert body["listone"] == seeded_db.listone
    assert body["budget"] == float(seeded_db.budget)
    assert body["roster_size"] == seeded_db.roster_size


@pytest.mark.xfail(
    strict=True,
    reason=(
        "the ten inputs of SPEC.md §11.1, and `sentiment=None`/`tilt_k=1.0` above all: "
        "the page is showing the sentiment model's ablation control as advice. 1.5 fixes "
        "both in one commit and deletes this marker."
    ),
)
def test_the_plan_the_page_shows_is_the_plan_the_cli_prints(
    seeded_db: SeededWorld, frozen_today: date, api: TestClient
) -> None:
    body = api.get(
        "/api/v1/asta/plan",
        params={"league_id": seeded_db.league_id, "season": seeded_db.season},
    ).json()
    page = {
        "players": {row["player_id"]: row["price"] for row in body["players"]},
        "total_cost": round(float(body["total_cost"]), 6),
        "objective": round(float(body["objective"]), 6),
    }

    assert page == _cli_plan(seeded_db, frozen_today)
