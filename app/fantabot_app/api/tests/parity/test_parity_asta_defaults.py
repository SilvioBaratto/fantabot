"""The `--lam` *default*: every declaration of it pinned to one number, and every hand-off
that carries it followed from the query parameter down to the objective.

Ten sites declare it — six CLI commands, two routes, two application value objects — and
three hand-offs pass it along. This file reads the declarations out of the running code and
sends a number no default holds through each hand-off.

**This is the hole the rest of the tier could not see.** `test_parity_asta_plan.py` compares
what the CLI and the page decide — and it did it by handing both sides an explicit `--lam 0`
/ `lam=0`. Two surfaces told the same number agree about it whatever their defaults are, so
the file whose whole job is "the page and the command plan the same rosa" was structurally
blind to the one input neither of them is ever told: with no flag typed and no query
parameter sent, `asta optimize` solved `sum(mu) - 0.3*Var` and `GET /asta/plan` solved
`sum(mu)`. Two rosters, one lega, and `core/api/asta.service.ts:15` sends nothing but the
lega — so the page's default *is* what the operator sees, every time.

That is the pair `CLAUDE.md:141` names by name: "how `GET /asta/plan` came to build a plan
differing from `asta optimize`'s in ten inputs". It got there once by drifting apart and
again by being fixed on one side only.

**So this file passes nothing and asserts on the declarations themselves.** The CLI's side
is read off the real Click command — the object `fantabot asta optimize --help` prints, not
the Python function, because a `typer.Option` that never reached the command is a default
the operator does not have. The app's side is read off the OpenAPI schema — what a client is
actually told, which also catches a `Query(0.0)` wrapper that a signature read would report
as a `Query` object rather than as `0.0`.

**It sweeps rather than naming two sites**, so a seventh command or a third route cannot
join the set quietly, and it refuses to pass on an empty sweep: a discovery test that finds
nothing is a green test that checks nothing, which is how a one-entry `mantra_compat.json`
passed for a week.

**Three surfaces now, not two, and the third is why sweeping beats naming.** The version of
this file that shipped with the route fix pinned `PlanRequest.lam` by name and wrote the
*other* constructor down in a warning: "`AdvisoryRequest.lam` is the same field on the
advisory's value object and is **still `0.0`** — a real loose end and is named here rather
than left for a grep to find." A test that knows the defect and passes anyway is prose. The
application package is walked now, so a value object declaring `lam` is covered by existing
rather than by being remembered. It cost nothing: the walk finds exactly four sites and
`asta_bench.replay` / `asta_calibrate.sweep` — two literal `0.3`s nobody had noticed — come
with it.

**And a default is only half of it.** With every default agreeing, `lam=lam` can be *deleted*
from a route and nothing changes at the default — which is the mutation a reviewer measured
surviving the whole suite. So the last three tests follow the number rather than its
declaration: a non-default `lam` is sent to each route and asserted to arrive at the
objective, one hand-off at a time. `GET /asta/advisory` had no test of any kind on that path.
"""

from __future__ import annotations

import dataclasses
import importlib
import inspect
import pkgutil
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

#: Every CLI command that must carry the flag, so a rename or a lost registration fails here
#: rather than shrinking the sweep to nothing. `bench` and `calibrate` are in it: they name a
#: recorded *corpus* rather than a room, but they replay the live objective and an evening
#: swept at a different `lam` grades alphas against a plan nobody would have made.
EXPECTED_CLI = {
    "asta optimize", "asta live", "asta room", "asta bid", "asta calibrate", "asta bench",
}

#: Every route that must carry it. `/asta/plan` is the page's plan; `/asta/advisory` is the
#: rolling one over a live room's ledger, and it mirrors `asta live --league`.
EXPECTED_ROUTES = {"GET /api/v1/asta/plan", "GET /api/v1/asta/advisory"}

#: The application-layer declarations the walk must find, for the same reason the two sets
#: above exist: the walk is the assertion, and a walk that silently stopped finding things
#: would agree with everything. These two are the value objects a surface hands across the
#: boundary; the other two sites it finds are function defaults and are not named here,
#: because naming them is what this test stopped doing.
EXPECTED_APPLICATION = {
    "fantabot.application.plan_request.PlanRequest.lam",
    "fantabot.application.asta_advisory.AdvisoryRequest.lam",
}

#: A value no default anywhere holds, so "it arrived" cannot be confused with "it defaulted".
#: That confusion is the whole reason the deletion mutant survived: at `DEFAULT_LAM` a route
#: that forwards and a route that forgot are indistinguishable, byte for byte, in the answer.
NON_DEFAULT_LAM = 0.77


def _cli_lam_defaults() -> dict[str, object]:
    """`{"asta optimize": 0.3, ...}` — from the Click commands, not from the source text."""
    import typer.main
    from fantabot.interface.app import app as fantabot_cli

    def walk(
        command: object, path: tuple[str, ...] = ()
    ) -> Iterator[tuple[tuple[str, ...], object]]:
        children = getattr(command, "commands", None)
        if children:
            for name, child in children.items():
                yield from walk(child, (*path, name))
        else:
            yield path, command

    found: dict[str, object] = {}
    for path, command in walk(typer.main.get_command(fantabot_cli)):
        for param in command.params:
            if param.name == "lam":
                found[" ".join(path)] = param.default
    return found


def _route_lam_defaults() -> dict[str, object]:
    """`{"GET /api/v1/asta/plan": 0.3, ...}` — from the served schema, not the signature."""
    from fantabot_app.api.main import app

    found: dict[str, object] = {}
    for path, operations in app.openapi()["paths"].items():
        for verb, operation in operations.items():
            for param in operation.get("parameters", []):
                if param["name"] == "lam":
                    found[f"{verb.upper()} {path}"] = param["schema"].get("default")
    return found


def _application_lam_defaults() -> dict[str, object]:
    """`{"fantabot.application.plan_request.PlanRequest.lam": 0.3, ...}` — by walking.

    Every module of `fantabot.application`, every dataclass field and every function
    parameter called `lam` that carries a default. **Discovered rather than listed**, which
    is the point of the rewrite: the previous version named `PlanRequest` and described
    `AdvisoryRequest` in a docstring, so the one surface that had actually drifted was the
    one the test could not fail on.

    Scoped to `application/` on purpose, and that is not laziness about `domain/`.
    `domain/asta/reservation.py` and `domain/asta/optimizer.py` declare `lam: float = 0.0`
    four and one times over, and they **cannot** read `DEFAULT_LAM`: it lives in
    `application/`, `domain/` may not import it (`tests/test_layers.py`), and a pure kernel's
    neutral is 0.0 anyway — `sum(mu)` with no variance term. Those are the mathematical
    identity, not a policy; the policy is what a *surface* hands them, and every surface
    hands it explicitly. `interface/` is covered better one function up, by reading the real
    Click objects rather than the source.
    """
    import fantabot.application as pkg

    found: dict[str, object] = {}
    for info in pkgutil.walk_packages(pkg.__path__, prefix=f"{pkg.__name__}."):
        module = importlib.import_module(info.name)
        for obj in vars(module).values():
            if getattr(obj, "__module__", None) != module.__name__:
                continue  # re-exported; it is swept where it is declared
            if inspect.isclass(obj) and dataclasses.is_dataclass(obj):
                for declared in dataclasses.fields(obj):
                    if declared.name == "lam" and declared.default is not dataclasses.MISSING:
                        found[f"{module.__name__}.{obj.__name__}.lam"] = declared.default
            elif inspect.isfunction(obj):
                parameter = inspect.signature(obj).parameters.get("lam")
                if parameter is not None and parameter.default is not inspect.Parameter.empty:
                    found[f"{module.__name__}.{obj.__name__}(lam=)"] = parameter.default
    return found


def test_the_sweep_finds_every_surface() -> None:
    """The guard that makes every assertion below mean something.

    An empty sweep agrees with an empty sweep. If a command is renamed, a route moves or the
    application walk stops importing, this is the failure that says so — rather than the
    equality tests going quietly vacuous.
    """
    assert set(_cli_lam_defaults()) >= EXPECTED_CLI
    assert set(_route_lam_defaults()) >= EXPECTED_ROUTES
    assert set(_application_lam_defaults()) >= EXPECTED_APPLICATION


def test_the_page_and_the_command_default_lam_to_the_same_number() -> None:
    """The refutation, as one assertion: no flag, no query parameter, one objective."""
    cli = _cli_lam_defaults()
    routes = _route_lam_defaults()

    assert cli["asta optimize"] == routes["GET /api/v1/asta/plan"], (
        "`asta optimize` and `GET /asta/plan` default `lam` differently, so the page and "
        "the command plan two rosters for one lega with nothing typed anywhere — the "
        "divergence this tier exists to prevent, and the one the frontend cannot work "
        "around because it sends no `lam` at all."
    )
    assert cli["asta live"] == routes["GET /api/v1/asta/advisory"], (
        "`asta live --league` and `GET /asta/advisory` default `lam` differently — the same "
        "split on the surface watched *during* the auction."
    )


def test_every_surface_reads_the_one_shared_constant() -> None:
    """One value across all three surfaces, and it is `DEFAULT_LAM`'s.

    Equality between them is the property; naming the constant is what says *where* the
    number comes from. A literal that happens to match today is the state this closes —
    and two of the sites the walk finds, `asta_bench.replay` and `asta_calibrate.sweep`,
    are exactly that: `lam: float = 0.3` typed out by hand, right today and right by
    coincidence. Moving `DEFAULT_LAM` with those two left behind is a silent split, so
    they are held to the constant here rather than to the number they currently spell.
    """
    from fantabot.application.plan_request import DEFAULT_LAM

    declared = {
        **_cli_lam_defaults(),
        **_route_lam_defaults(),
        **_application_lam_defaults(),
    }
    drifted = {site: value for site, value in declared.items() if value != DEFAULT_LAM}
    assert not drifted, (
        f"these declare `lam` as something other than DEFAULT_LAM ({DEFAULT_LAM}): "
        f"{drifted}. Every surface that plans a rosa has to agree, and a second value is "
        "a second objective nobody typed."
    )


def test_the_constructors_both_surfaces_build_default_the_same_way() -> None:
    """The third and fourth declarations of the same number, found rather than listed.

    `PlanRequest.lam` and `AdvisoryRequest.lam` are the value objects the CLI and the routes
    hand across the boundary. No surface *reaches* either default — all four callers state
    `lam` — so nothing else in the suite notices one drifting, and that is precisely what
    happened: `AdvisoryRequest.lam` sat at `0.0` through two rounds of unifying this number,
    with the previous version of this file describing it in a warning instead of failing on
    it.

    A wrong default here is not inert. It is what makes `lam=lam` *deletable* from
    `asta_advisory`'s `AdvisoryRequest(...)` — a mutation that compiles, runs, returns 200
    and prices the auction an operator is watching against a different objective from the
    plan they were briefed from. The three hand-off tests at the foot of this file close
    the deletion; this one closes the landing it falls onto.
    """
    from fantabot.application.plan_request import DEFAULT_LAM

    declared = _application_lam_defaults()
    assert {site: declared[site] for site in EXPECTED_APPLICATION} == dict.fromkeys(
        EXPECTED_APPLICATION, DEFAULT_LAM
    )


def test_the_response_models_echo_is_deliberately_not_the_shared_constant() -> None:
    """`AstaPlan.lam` stays `0.0`, and this is the test that says so out loud.

    It is the one `lam = 0.0` left in the app, and a sweep like the one above is exactly how
    somebody arrives at it next. It is not a surface's default: nothing is planned from it.
    It *echoes* what a plan was solved at, and the only responses that reach it are the six
    `found=False` screens, which carry no plan — so `DEFAULT_LAM` there would be a claim
    about an optimizer run that never happened, printed beside `total_cost`, `objective` and
    `budget` all saying `0.0` for the same reason. The page renders `Risk (lam)` inside the
    plan panel only (`pages/asta/asta.html:107`), so today it is unreachable either way; the
    value is chosen for the reading it would get if that stopped being true.

    It is also load-bearing, and that is measured rather than argued. Delete `lam=lam,` from
    the planned `AstaPlan(...)` return and `test_parity_asta_plan.py::
    test_the_page_says_what_it_planned_on` fails `assert 0.0 == 0.3` — because this default
    differs from the route's. Make the two agree and that mutant survives: the response
    would echo the number the route happened to default to whether or not the plan was ever
    told it. The one `lam = 0.0` left in the app is the reason a deletion beside it dies.
    """
    from fantabot_app.api.v1.endpoints.asta import AstaPlan

    assert AstaPlan.model_fields["lam"].default == 0.0
    assert AstaPlan(found=False).lam == 0.0


@pytest.mark.parametrize("surface", sorted(EXPECTED_ROUTES))
def test_the_routes_lam_is_optional(surface: str) -> None:
    """A default only decides anything while the parameter can be omitted — and the page
    omits it. Required would make the assertions above true and irrelevant."""
    from fantabot_app.api.main import app

    verb, path = surface.split(" ", 1)
    (param,) = [
        p
        for p in app.openapi()["paths"][path][verb.lower()].get("parameters", [])
        if p["name"] == "lam"
    ]
    assert param.get("required") is not True


# -- the hand-offs: a default that agrees is only half of the number ----------------------
#
# With every declaration above equal, `lam=lam` can be deleted from a route and the answer
# is byte-identical at the default. That is the mutation a reviewer applied and measured
# green against the whole suite, and no assertion anywhere could have seen it: the advisory
# route's `lam` was not read by a single test, and the plan route's was only ever read back
# *at* its default. These follow a number no default holds, one hand-off at a time.


def _minimal_advisory() -> Any:
    """An `Advisory` with nothing in it. This test exercises the wiring, not the fold."""
    from fantabot.application.asta_advisory import Advisory
    from fantabot.application.plan_inputs import PlanInputs

    return Advisory(
        targets=(),
        opponents=(),
        sales=0,
        dropped_sales=0,
        total_cost=0,
        objective=0.0,
        world=PlanInputs(
            pool=[], value=lambda _p: 1.0, prices={}, teams={}, names={}, roles={},
            legality={}, sentiment=None,
        ),
    )


def test_the_advisory_route_hands_its_lam_to_the_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`GET /asta/advisory?lam=0.77` builds an `AdvisoryRequest` carrying 0.77.

    **The mutant this kills**, applied and measured surviving by a reviewer: delete
    `lam=lam,` from the `AdvisoryRequest(...)` in `asta_advisory()`. It compiles, the route
    answers 200 with a complete advisory, and the dataclass default catches the omission —
    so at the default nothing observable changes and every other test in this repository
    still passes. Only a `lam` the default cannot supply separates "forwarded" from
    "forgotten".

    This is the surface watched *during* the auction. A `lam` the operator set and the route
    dropped means the advisory prices every lot against `sum(mu)` while the plan it was
    briefed from and the bidder spending the credits solve `sum(mu) - lam*Var`.
    """
    from fantabot_app.api.v1.endpoints import asta as endpoint

    seen: dict[str, Any] = {}

    def fake_build(_session: Any, request: Any, **_kwargs: Any) -> Any:
        seen["request"] = request
        return _minimal_advisory()

    monkeypatch.setattr(endpoint, "ledger_events", lambda _db, _league: [])
    monkeypatch.setattr(endpoint, "listone_fetch", lambda: {"uuid-1": 7})
    monkeypatch.setattr(endpoint, "build_advisory", fake_build)

    from fantabot_app.api.main import app

    with TestClient(app) as client:
        response = client.get(
            "/api/v1/asta/advisory",
            params={
                "league": "a-room",
                "db": 1,
                "team": "US",
                "listone": "mantra",
                "lam": NON_DEFAULT_LAM,
            },
        )

    assert response.status_code == 200, response.text
    assert seen["request"].lam == NON_DEFAULT_LAM, (
        "`GET /asta/advisory` did not hand its `lam` to the `AdvisoryRequest` — the "
        "dataclass default answered for it, and the advisory an operator watches during "
        "the auction is priced against a different objective from the plan and the bidder."
    )


def test_the_plan_route_hands_its_lam_to_the_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`GET /asta/plan?lam=0.77` builds a `PlanRequest` carrying 0.77, and echoes it back.

    The same deletion, one route along and with the same landing under it since round two:
    drop `lam=lam,` from the `PlanRequest(...)` and `PlanRequest.lam`'s `DEFAULT_LAM`
    answers silently. `test_parity_asta_plan.py` compares the two requests whole, which
    catches a *divergence* between the surfaces — but both surfaces defaulting is not a
    divergence, so a route that stopped forwarding would still match a command that never
    was told either.

    Only the hand-off *into* the request is asserted here — the fake `build_plan` raises
    before a plan exists to echo. The way back out is already covered, and covered by the
    one test in this repository that could be: `test_parity_asta_plan.py`'s
    `assert body["lam"] == DEFAULT_LAM` reads the response at the route's default, and
    `AstaPlan.lam` deliberately defaults to `0.0` (see the test above), so dropping
    `lam=lam` from the `AstaPlan(...)` return makes that assertion read 0.0 against 0.3.
    That asymmetry is the whole reason the response model keeps a different default.
    """
    from fantabot.application import lega_reads, plan_request
    from fantabot.domain.asta.state import SNAPSHOT_DECLARED, RosterRules

    seen: dict[str, Any] = {}

    class _Snapshot:
        budget = 500

    def fake_build_plan(_session: Any, request: Any) -> Any:
        seen["request"] = request
        # `EmptyPool` is one of the route's named outcomes, so this stops the flow before
        # the walk-aways without pretending to be a plan. What is under test is the
        # request that reached here, not what came back.
        raise plan_request.EmptyPool("nothing to plan — this test never seeds a pool")

    monkeypatch.setattr(lega_reads, "latest_settings", lambda _s, _l: _Snapshot())
    monkeypatch.setattr(
        lega_reads,
        "rules_for_league",
        lambda _s, _l: (RosterRules(), SNAPSHOT_DECLARED, "mantra"),
    )
    monkeypatch.setattr(plan_request, "build_plan", fake_build_plan)

    from fantabot_app.api.main import app

    with TestClient(app) as client:
        response = client.get(
            "/api/v1/asta/plan", params={"league_id": 1, "lam": NON_DEFAULT_LAM}
        )

    assert response.status_code == 200, response.text
    assert seen["request"].lam == NON_DEFAULT_LAM, (
        "`GET /asta/plan` did not hand its `lam` to the `PlanRequest` — `DEFAULT_LAM` "
        "answered for it, and the page plans a rosa the operator did not ask for."
    )


def test_the_advisory_hands_the_requests_lam_to_the_fold(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`build_advisory` hands `request.lam` to `rolling_advisory`. The last hand-off.

    `domain/asta/reservation.rolling_advisory` declares `lam: float = 0.0` — correct for a
    pure kernel, which cannot read `DEFAULT_LAM` and whose neutral is no variance term at
    all — so dropping `lam=request.lam` from the fold is the third silent deletion on this
    one path, and the one furthest from anything a screen would show. The plan side has had
    this assertion since it was written (`tests/application/test_plan_request.py`'s
    `test_lam_and_fallbacks_reach_the_optimizer`); the advisory side captured the fold's
    kwargs and never looked at them.
    """
    from datetime import date

    from fantabot.adapters.persistence import news_sentiment
    from fantabot.application import asta_planner
    from fantabot.application.asta_advisory import AdvisoryRequest, build_advisory
    from fantabot.application.plan_inputs import PlanInputs
    from fantabot.domain.asta import reservation

    seen: dict[str, Any] = {}

    def fake_read(_session: Any, **_kwargs: Any) -> Any:
        return PlanInputs(
            pool=[object()], value=lambda _p: 1.0, prices={}, teams={}, names={},
            roles={}, legality={}, sentiment=None,
        )

    def fake_rolling(_state: Any, _pool: Any, _events: Any, **kwargs: Any) -> Any:
        seen["lam"] = kwargs.get("lam")
        return iter(())

    class _Source:
        def all_latest(self, *, data_run: date | None = None) -> dict[str, Any]:
            return {"1": object()}

    monkeypatch.setattr(asta_planner, "read_plan_inputs", fake_read)
    monkeypatch.setattr(reservation, "rolling_advisory", fake_rolling)
    monkeypatch.setattr(news_sentiment, "NewsSentimentSource", lambda _s: _Source())

    build_advisory(
        object(),
        AdvisoryRequest(
            our_team_id="US",
            season="2026/27",
            listone="mantra",
            as_of=date(2026, 9, 20),
            budget=500.0,
            lam=NON_DEFAULT_LAM,
        ),
        events=[],
        bridge={"uuid-1": 7},
    )

    assert seen["lam"] == NON_DEFAULT_LAM, (
        "`build_advisory` did not hand `request.lam` to `rolling_advisory`, so the fold "
        "ran on the kernel's 0.0 — `sum(mu)`, with the variance term gone."
    )
