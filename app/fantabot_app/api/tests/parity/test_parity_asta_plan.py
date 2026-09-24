"""`asta optimize` and `GET /asta/plan`, on one database, at one frozen date.

This is the divergence the whole phase is named for, and it is **closed** — the rest of
this paragraph is history, not the state of the two surfaces. The archived parity-phase
spec §11.1 counted **ten** inputs that differed, and the two that mattered most were a
pair: the endpoint passed `sentiment=None` and `tilt_k=1.0`. `sentiment=None` **is the
ablation control** — the experiment's other arm, plain `fvm`, which on the 2026-08-28 data
chases a player with a metatarsal fracture to 62 credits, and the page was showing it to
an operator as advice. `tilt_k` was 4x the CLI's 0.25 and inert only because there was
nothing to tilt.

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

from collections.abc import Callable
from datetime import date
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from typer.testing import Result

from .conftest import SeededWorld


def _cli_plan(
    world: SeededWorld,
    cli: Callable[..., Result],
) -> dict[str, object]:
    """What `asta optimize` decides — **by running `asta optimize`.**

    Since 2.1 the command is told *which lega* (`--lega`) and reads its band and format from
    the same snapshot the endpoint reads, so there is nothing left to reconstruct and no
    `--format` to keep in step.

    **This used to re-implement the command's body in this file**: `read_plan_inputs` +
    `optimize_roster` with the CLI's defaults, copied. The tier exists to stop one decision
    having two implementations, and it had two — the same failure it was built to catch, one
    level up. A drift in `asta_optimize` (a changed default, a new option, a different
    `RosterRules`) would have left this copy agreeing with the endpoint and both disagreeing
    with the command an operator actually types.

    The command runs for real, in-process, through `CliRunner`. What is *read back* is the
    `PlannedRoster` it built — captured by spying on `application.plan_request.build_plan`,
    which 1.4 made the single door to a plan. The spy calls through, so the command's own
    solve is the one measured; nothing is stubbed.

    Parsing the Rich table instead would break the tier's own rule — *compare decision
    content, never rendered text* — and a test that fails on a column width gets deleted.

    **`objective` is in the comparison and is the point of it.** Before 1.5 the two sides
    bought the same twelve players and disagreed about what the rosa was worth: 344.2 against
    369.0. Membership alone would have reported parity between a plan built on the sentiment
    model and one built on its ablation control.
    """
    from fantabot.application import plan_request as pr

    captured: list[object] = []
    asked: list[object] = []
    real = pr.build_plan

    def spy(session: object, request: object) -> object:
        # The request is recorded *before* the solve, the result after. `asta optimize` can
        # raise `InfeasibleRoster` out of `build_plan` (2.1), and a spy that only appended
        # on success reported "never reached build_plan" for a call that plainly did.
        asked.append(request)
        planned = real(session, request)
        captured.append(planned)
        return planned

    with patch.object(pr, "build_plan", spy):
        # **No `--lam`, deliberately.** It read `--lam 0` while the endpoint was called
        # without one, so the comparison held the CLI to a number the page was never told
        # and could not see the two *defaults* splitting — which is exactly how the route
        # kept `0.0` after the command moved to `0.3`. Both sides now take their own
        # default, so this test fails on that split too, and
        # `test_parity_asta_defaults.py` says which surface moved when it does.
        cli(
            "asta", "optimize",
            "--season", world.season,
            "--lega", str(world.league_id),
            "--budget", str(world.budget),
            "--fallbacks", "0",
        )

    assert asked, "`asta optimize` never reached build_plan — the spy did not take"
    assert captured, (
        "`asta optimize` reached build_plan and got no plan out of it. Today that is 2.1: "
        "the command plans on a bare RosterRules() — 30 men whatever the lega declares."
    )
    planned = captured[-1]
    result, inputs = planned.result, planned.world  # type: ignore[attr-defined]
    return {
        "players": {pid: float(inputs.prices.get(pid, 0.0)) for pid in result.optimal.player_ids},
        "total_cost": round(float(result.optimal.total_cost), 6),
        "objective": round(float(result.optimal.objective), 6),
    }


def test_the_seeded_world_is_plannable_at_all(
    seeded_db: SeededWorld, frozen_today: date, api: TestClient
) -> None:
    """The guard that makes every other assertion in this file mean something.

    Two empty results agree perfectly, so the endpoint has to actually plan before any
    comparison below is worth reading.
    """
    body = api.get(
        "/api/v1/asta/plan",
        params={"league_id": seeded_db.league_id, "season": seeded_db.season},
    ).json()

    assert body["found"] is True, body
    assert body["players"], "the endpoint planned over an empty pool"
    # The seed is a *choice*, not a forced buy: nineteen candidates for twelve slots. A
    # fixture where the rosa is the pool makes both sides agree about a decision neither of
    # them made, which is how the first version of this test passed.
    assert len(body["players"]) < len(seeded_db.player_ids)


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
    cli: Callable[..., Result],
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

    assert page == _cli_plan(seeded_db, cli)


def test_both_sides_build_the_same_request(
    seeded_db: SeededWorld,
    frozen_today: date,
    api: TestClient,
    cli: Callable[..., Result],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The golden dict — **both sides captured, neither reconstructed.**

    Comparing *outputs* only catches a divergence the seed is rich enough to express: the
    first version of this fixture agreed perfectly while one side planned on the sentiment
    model and the other on its ablation control. Comparing the request catches a changed
    field whatever the data.

    **An earlier version was one-sided.** It captured the endpoint's request and compared it
    against `_request_from`, a literal reconstruction written in this file — so a drift in
    `asta_optimize` left the reconstruction agreeing with the endpoint and both disagreeing
    with the command. That is 1.14's defect in the same file, one function along. Both
    requests are captured from the running code now.

    Since 2.1 the two requests are compared **whole**. `rules` used to be excluded and
    asserted-different, because the command built a bare `RosterRules()` while the page read
    the lega's band; both now read the same snapshot through
    `application.lega_reads.rules_for_league`.
    """
    from fantabot.application import plan_request as pr

    captured: list[object] = []
    real = pr.build_plan

    def spy(session: object, request: object) -> object:
        captured.append(request)
        return real(session, request)

    monkeypatch.setattr(pr, "build_plan", spy)

    api.get(
        "/api/v1/asta/plan",
        params={"league_id": seeded_db.league_id, "season": seeded_db.season},
    )
    assert captured, "the endpoint did not reach build_plan"
    from_page = captured[-1]

    captured.clear()
    # No `--lam` here either, and `PlanRequest.lam` is one of the fields compared whole —
    # so the two defaults are pinned by this assertion as well as by introspection.
    cli(
        "asta", "optimize",
        "--season", seeded_db.season,
        "--lega", str(seeded_db.league_id),
        "--budget", str(seeded_db.budget),
        "--fallbacks", "3",
    )
    assert captured, "`asta optimize` did not reach build_plan"
    from_cli = captured[-1]

    assert from_cli == from_page


def test_the_page_says_what_it_planned_on(
    seeded_db: SeededWorld, frozen_today: date, api: TestClient
) -> None:
    """`lam`, `owned`, `callable_pool` and the fallbacks, none of which the page had.

    `callable_pool` is `None` when the listone was unreachable and the plan degraded open —
    never `0`, because an empty exclusion set and an unknown one are different facts and
    the second is the one that widens the pool.

    `lam` is asserted against the shared constant rather than a literal. It read `0.0` here,
    which was the route's own default and therefore true of any number the route happened to
    hold — including the `0.0` that was three tenths away from the command's.
    """
    from fantabot.application.plan_request import DEFAULT_LAM

    body = api.get(
        "/api/v1/asta/plan",
        params={"league_id": seeded_db.league_id, "season": seeded_db.season},
    ).json()

    assert body["lam"] == DEFAULT_LAM
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

    **`lam=0` is stated, and it is not the route's default.** The held member is a measured
    property of this seed at `lam=0`; at the route's `DEFAULT_LAM` the same nineteen players
    produce no zero-ceiling row at all and the guard above fires. This test is about `0` and
    `null` surviving Pydantic and JSON as different values, not about which objective built
    the plan — so it names the condition its fixture was measured under instead of riding a
    default that is free to move.
    """
    from fantabot.application.plan_request import WALK_AWAY_HOLD

    body = api.get(
        "/api/v1/asta/plan",
        params={"league_id": seeded_db.league_id, "season": seeded_db.season, "lam": 0},
    ).json()
    zeros = [row for row in body["players"] if row["walk_away_provenance"] == WALK_AWAY_HOLD]

    assert zeros, (
        "the seed no longer produces a held (zero-ceiling) plan member, so this test guards "
        "nothing — re-measure and re-seed before editing it"
    )
    for row in zeros:
        # `== 0` is the whole assertion, and deliberately not paired with an `is not None`
        # beside it: `None == 0` is False, so a collapse fails *here*. Measured 2026-09-24
        # by simulating the defect (`priced[pid].credits or None` in `endpoints/asta.py`) —
        # this line is what went red, and the `is not None` that used to follow it was
        # never reached. An assertion its predecessor cannot let fail reads as a second
        # guard and is a comment.
        assert row["walk_away"] == 0, "a zero collapsed to null between here and JSON"


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
