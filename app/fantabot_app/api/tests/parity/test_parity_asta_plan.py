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

**Green since 1.5**, and it was `xfail(strict=True)` before it: the marker is what made
the fix fail this file until somebody deleted it. The last input to close was one 1.5
*created* — the app gained its own `_today()` seam, the tier did not freeze it, and the two
sides then read the calendar six days apart and disagreed about what the same twelve
players were worth while agreeing on which twelve they were.

`test_both_sides_build_the_same_request` is the guard that keeps it closed: comparing
outputs catches a divergence only when the seed is rich enough to express it, and comparing
the request catches it whatever the data.
"""

from __future__ import annotations

from datetime import date

import pytest
from fastapi.testclient import TestClient

from .conftest import SeededWorld, cli_session


def _cli_plan(
    world: SeededWorld, today: date, narrowed: frozenset[str] | None
) -> dict[str, object]:
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
            callable_ids=narrowed,
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
    seeded_db: SeededWorld,
    frozen_today: date,
    api: TestClient,
    seeded_callable_ids: frozenset[str],
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

    cli = _cli_plan(seeded_db, frozen_today, seeded_callable_ids)
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


def test_the_plan_the_page_shows_is_the_plan_the_cli_prints(
    seeded_db: SeededWorld,
    frozen_today: date,
    api: TestClient,
    seeded_callable_ids: frozenset[str],
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

    assert page == _cli_plan(seeded_db, frozen_today, seeded_callable_ids)


def _request_from(world: SeededWorld, today: date, narrowed: frozenset[str] | None) -> object:
    """The request `asta optimize` builds for this world, with the CLI's own defaults."""
    from fantabot.application.plan_request import PlanRequest
    from fantabot.domain.asta.sentiment import SentimentWeights
    from fantabot.domain.asta.state import RosterRules

    return PlanRequest(
        season=world.season,
        listone=world.listone,
        as_of=today,
        budget=float(world.budget),
        rules=RosterRules(
            size=world.roster_size,
            min_goalkeepers=world.min_roles[0],
            min_movement=world.min_roles[1],
        ),
        owned=frozenset(),
        lam=0.0,
        n_fallbacks=3,
        tilt_k=SentimentWeights().k,
        sentiment=True,
        sentiment_run=None,
        callable_ids=narrowed,
    )


def test_both_sides_build_the_same_request(
    seeded_db: SeededWorld,
    frozen_today: date,
    api: TestClient,
    seeded_callable_ids: frozenset[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The golden dict. Without it this reopens the first time either side gains an option.

    Comparing *outputs* only catches a divergence the seed is rich enough to express — the
    first version of this fixture agreed perfectly while one side planned on the sentiment
    model and the other on its ablation control, because the rosa was the whole pool.
    Comparing the request catches a changed field whatever the data.

    The endpoint's request is captured rather than reconstructed: reconstructing it is
    writing the assertion twice and calling the second copy evidence.
    """
    from fantabot.application import plan_request as pr

    captured: list[object] = []
    real = pr.build_plan

    def spy(session: object, request: object) -> object:
        captured.append(request)
        return real(session, request)

    # The endpoint imports `build_plan` inside its own body, so patching the module
    # attribute is what a call actually resolves. Patching the endpoint's namespace would
    # do nothing and the `captured` assertion below is what would say so.
    monkeypatch.setattr(pr, "build_plan", spy)

    api.get(
        "/api/v1/asta/plan",
        params={"league_id": seeded_db.league_id, "season": seeded_db.season},
    )

    assert captured, "the endpoint did not reach build_plan"
    assert captured[0] == _request_from(seeded_db, frozen_today, seeded_callable_ids)


def test_the_page_says_what_it_planned_on(
    seeded_db: SeededWorld, frozen_today: date, api: TestClient
) -> None:
    """`lam`, `owned`, `callable_pool` and the fallbacks, none of which the page had.

    `callable_pool` is `None` when the listone was unreachable and the plan degraded open —
    never `0`, because an empty exclusion set and an unknown one are different facts and
    the second is the one that widens the pool.
    """
    body = api.get(
        "/api/v1/asta/plan",
        params={"league_id": seeded_db.league_id, "season": seeded_db.season},
    ).json()

    assert body["lam"] == 0.0
    assert body["owned"] == []
    assert body["callable_pool"] == len(seeded_db.player_ids)
    assert body["fallbacks"], "the CLI prints three next-best plans; the page printed none"


def test_owned_reaches_the_plan(
    seeded_db: SeededWorld, frozen_today: date, api: TestClient
) -> None:
    """The page had only ever shown the plan for an empty roster — the right answer on the
    morning of the asta and the wrong one on every evening after it."""
    keeper = seeded_db.player_ids[0]

    body = api.get(
        "/api/v1/asta/plan",
        params={
            "league_id": seeded_db.league_id,
            "season": seeded_db.season,
            "owned": keeper,
        },
    ).json()

    assert body["found"] is True, body
    assert body["owned"] == [keeper]
    assert keeper in [row["player_id"] for row in body["players"]]


def test_sentiment_on_with_no_feed_says_so_rather_than_showing_a_blank(
    seeded_db: SeededWorld, frozen_today: date, api: TestClient
) -> None:
    """`found=false` with no reason is how "run `news fetch` first" and "the database is
    down" became the same screen. 1.7 pins the full tuple; these two are named now because
    turning sentiment on is what made the first of them reachable."""
    from fantabot.adapters.persistence import database_manager
    from sqlalchemy import text

    from .conftest import SYNTHETIC_BASE

    with database_manager.get_session() as session:
        session.execute(
            text("DELETE FROM player_sentiment WHERE player_id >= :b"),
            {"b": SYNTHETIC_BASE},
        )

    body = api.get(
        "/api/v1/asta/plan",
        params={"league_id": seeded_db.league_id, "season": seeded_db.season},
    ).json()

    assert body["found"] is False
    assert body["reason"] and "news fetch" in body["reason"], body


def test_the_page_carries_a_walk_away_beside_the_corpus_price(
    seeded_db: SeededWorld, frozen_today: date, api: TestClient
) -> None:
    """Two numbers, two labels, **both in credits**.

    The page had one — the observed mean clearing price — under the heading "Price", which
    reads as advice about what to pay. Then it had two, and the second was `reservations`'
    marginal: an *objective* difference clamped by the budget, never converted to credits
    (`SPEC.md` §2.A's unit error). It is `lot_reference` + `lot_ceiling` now, the pair the
    live room prices a lot with.
    """
    from fantabot.application.plan_request import (
        WALK_AWAY_AT_BUDGET,
        WALK_AWAY_HOLD,
        WALK_AWAY_RESOLVED,
    )

    body = api.get(
        "/api/v1/asta/plan",
        params={"league_id": seeded_db.league_id, "season": seeded_db.season},
    ).json()

    assert body["found"] is True, body
    priced = [row for row in body["players"] if row["walk_away"] is not None]
    assert priced, "no row carried a walk-away"

    for row in priced:
        assert row["walk_away_provenance"] in {
            WALK_AWAY_RESOLVED,
            WALK_AWAY_HOLD,
            WALK_AWAY_AT_BUDGET,
        }
        # Credits, and bounded by what the band can still spend on one lot. The marginal
        # this replaced was unbounded above by anything meaningful — 140.5 against a corpus
        # price of 71.8 on the live pool.
        assert isinstance(row["walk_away"], int)
        assert 0 <= row["walk_away"] <= seeded_db.budget


def test_a_walk_away_of_zero_survives_serialisation_as_zero(
    seeded_db: SeededWorld, frozen_today: date, api: TestClient
) -> None:
    """Defect B2's shape: 0 and `null` must stay different values all the way to the JSON.

    **The first version of this test could not fail.** It built `zeros` and `absent` from the
    same rows by mutually exclusive predicates and asserted the intersection was empty — true
    by construction. Simulated with the exact defect it named (`walk_away or None`), it still
    passed.

    This one names a specific row and asserts what came back for it, so a collapse anywhere
    in the chain — endpoint, Pydantic, JSON — turns it red.
    """
    from fantabot.application.plan_request import WALK_AWAY_HOLD

    body = api.get(
        "/api/v1/asta/plan",
        params={"league_id": seeded_db.league_id, "season": seeded_db.season},
    ).json()
    zeros = [row for row in body["players"] if row["walk_away_provenance"] == WALK_AWAY_HOLD]

    assert zeros, (
        "the seed no longer produces a held (zero-ceiling) plan member, so this test guards "
        "nothing — re-measure and re-seed before editing it"
    )
    for row in zeros:
        assert row["walk_away"] == 0
        assert row["walk_away"] is not None, "a zero collapsed to null between here and JSON"


def test_an_owned_player_needs_no_walk_away(
    seeded_db: SeededWorld, frozen_today: date, api: TestClient
) -> None:
    """`reservations` prices only unowned plan members, and that is right: a player already
    bought is not a purchase to decide. It is now the *only* reason a row carries none."""
    keeper = seeded_db.player_ids[0]

    body = api.get(
        "/api/v1/asta/plan",
        params={
            "league_id": seeded_db.league_id,
            "season": seeded_db.season,
            "owned": keeper,
        },
    ).json()

    from fantabot.application.plan_request import WALK_AWAY_UNPRICED

    (row,) = [r for r in body["players"] if r["player_id"] == keeper]
    assert row["walk_away"] is None
    assert row["walk_away_provenance"] == WALK_AWAY_UNPRICED
