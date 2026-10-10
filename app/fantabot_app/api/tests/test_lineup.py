"""S12 — /lineup/plan (preview only; degrades open, never submits)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import ClassVar

from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

from fantabot_app.api.main import app
from fantabot_app.api.v1.endpoints.lineup import build_lineup_plan


def test_the_plan_names_the_competition_it_was_built_for() -> None:
    """`build_plans` resolves it and used to discard it — `_comp`, unused.

    `GET /lineup/current` needs a competition and the page has no other way to learn one;
    more to the point, "what we should field" and "what is saved" are only comparable when
    both name the same competition, and on a matchday where a submit was refused they are
    exactly what an operator wants side by side.
    """
    from fantabot_app.api.v1.endpoints.lineup import build_lineup_plan

    class _Planned:
        module = "343"
        mday = 5
        starts: ClassVar[list[int]] = []
        bench: ClassVar[list[int]] = []

    assert build_lineup_plan(_Planned(), {}, 77).competition == 77
    assert build_lineup_plan(_Planned(), {}).competition is None


def test_build_lineup_plan_maps_starters_and_bench() -> None:
    planned = SimpleNamespace(
        module="4-3-3",
        starts=(1, 2, 3),
        bench=(4, 5),
        mday=3,
    )
    names = {1: "Svilar", 2: "Dimarco", 3: "Dybala", 4: "Reserve A", 5: "Reserve B"}
    plan = build_lineup_plan(planned, names)
    assert plan.found is True
    assert plan.module == "4-3-3"
    assert plan.matchday == 3
    assert [p.nome for p in plan.starters] == ["Svilar", "Dimarco", "Dybala"]
    assert [p.nome for p in plan.bench] == ["Reserve A", "Reserve B"]


def test_build_lineup_plan_falls_back_to_pid_when_name_missing() -> None:
    # A pid absent from the names dict must render as its string id, never crash.
    planned = SimpleNamespace(module="3-4-3", starts=(7,), bench=(), mday=1)
    plan = build_lineup_plan(planned, names={})  # no names at all
    assert plan.found is True
    assert [p.nome for p in plan.starters] == ["7"]


def test_lineup_plan_no_key_reason_is_distinct_from_not_connected(monkeypatch) -> None:
    # A3: "no encryption key" and "network/other failure" must be two DIFFERENT clean
    # bodies, not one catch-all. Force each branch and assert the reasons differ.
    from fantabot.adapters.persistence import database_manager
    from fantabot.config import settings
    from fantabot.domain.tokens import crypto

    # Branch 1 — no key configured: the early-return reason.
    monkeypatch.setattr(settings, "fantabot_encryption_key", "")
    no_key = TestClient(app).get("/api/v1/lineup/plan?league_id=4103937").json()
    assert no_key["found"] is False
    assert "encryption key" in no_key["reason"].lower()

    # Branch 2 — key present, but the DB/platform read fails: the not-connected reason.
    monkeypatch.setattr(settings, "fantabot_encryption_key", "present")
    monkeypatch.setattr(crypto, "TokenCipher", lambda _key: object())  # accept the fake key

    def boom():
        raise OperationalError("SELECT 1", {}, OSError("db unreachable"))

    monkeypatch.setattr(database_manager, "get_session", boom)
    connected = TestClient(app).get("/api/v1/lineup/plan?league_id=4103937").json()
    assert connected["found"] is False
    assert connected["reason"] != no_key["reason"]  # discriminated, not one catch-all
    assert "encryption key" not in connected["reason"].lower()


def test_lineup_plan_degrades_open_without_a_platform_call(monkeypatch) -> None:
    # Force the DB layer to fail so no apileague/network call is reached: the test makes no
    # live request to the platform and exercises the degrade-open path. (With no key
    # configured the endpoint returns early anyway — either way, found is false.)
    from fantabot.adapters.persistence import database_manager

    def boom():
        raise OperationalError("SELECT 1", {}, OSError("db unreachable"))

    monkeypatch.setattr(database_manager, "get_session", boom)

    response = TestClient(app).get("/api/v1/lineup/plan?league_id=4103937")
    assert response.status_code == 200
    body = response.json()
    assert body["found"] is False
    assert body["starters"] == []
    assert body["reason"]


def test_the_route_forwards_the_competition_build_plans_resolved(monkeypatch) -> None:
    """The wiring, not the mapper. Survivor 5 of this slice's battery: dropping `comp` at
    the one call site left `build_lineup_plan`'s own test green, because that test calls the
    mapper directly and the mapper still accepted one.

    A plan that reports no competition is a `Saved on the platform` panel that never asks —
    and a panel that never asks is indistinguishable, on screen, from a competition with
    nothing saved.
    """
    import contextlib

    from fantabot.adapters.persistence import database_manager
    from fantabot.adapters.tokens import store as token_store
    from fantabot.application import lineup_submit
    from fantabot.config import settings
    from fantabot.domain.tokens import crypto

    # Patched where the route looks each name up, which for a lazily-imported name is the
    # defining module. `build_plans` in particular: the route imports it inside its own body,
    # so a patch on the endpoint module is inert — and inert is how a harness passes while
    # measuring the real code path it meant to replace.
    planned = SimpleNamespace(module="343", mday=3, starts=[1], bench=[9])
    monkeypatch.setattr(settings, "fantabot_encryption_key", "k" * 44, raising=False)
    monkeypatch.setattr(crypto, "TokenCipher", lambda _k: object())
    monkeypatch.setattr(
        database_manager, "get_session", lambda: contextlib.nullcontext(object())
    )
    monkeypatch.setattr(token_store, "TokenStore", lambda *_a: object())
    monkeypatch.setattr(
        lineup_submit, "build_plans", lambda *_a: ([planned], {1: "Mandas", 9: "Rovella"}, 77)
    )

    body = TestClient(app).get("/api/v1/lineup/plan?league_id=4103937").json()

    assert body["outcome"] == "planned"
    assert body["competition"] == 77



def _plan_route_with_build_plans(monkeypatch, build_plans) -> dict:
    import contextlib

    from fantabot.adapters.persistence import database_manager
    from fantabot.adapters.tokens import store as token_store
    from fantabot.application import lineup_submit
    from fantabot.config import settings
    from fantabot.domain.tokens import crypto

    monkeypatch.setattr(settings, "fantabot_encryption_key", "k" * 44, raising=False)
    monkeypatch.setattr(crypto, "TokenCipher", lambda _k: object())
    monkeypatch.setattr(
        database_manager, "get_session", lambda: contextlib.nullcontext(object())
    )
    monkeypatch.setattr(token_store, "TokenStore", lambda *_a: object())
    monkeypatch.setattr(lineup_submit, "build_plans", build_plans)
    response = TestClient(app).get("/api/v1/lineup/plan?league_id=4103937")
    assert response.status_code == 200
    return response.json()


def test_a_lineup_error_while_planning_is_a_no_lineup_outcome_not_a_500(monkeypatch) -> None:
    """`NoCompetition`, `CompetitionAmbiguous`, `RosterIncomplete` and `NoFieldableModule`
    are all `LineupError`s that `build_plans` raises, and none is a `TokenError`: they
    escaped as a 500 and the page got neither an outcome nor a reason."""
    from fantabot.domain.lineup.errors import LineupError

    def refuse(*_a):
        raise LineupError("no competition for this lega")

    body = _plan_route_with_build_plans(monkeypatch, refuse)

    assert body["found"] is False
    assert body["outcome"] == "no_lineup"
    assert "no competition for this lega" in body["reason"]


def test_the_concrete_lineup_errors_each_map_to_no_lineup(monkeypatch) -> None:
    from fantabot.domain.lineup import errors

    for cls in (errors.NoCompetition, errors.CompetitionAmbiguous, errors.NoFieldableModule):
        exc = cls.__new__(cls)
        Exception.__init__(exc, f"{cls.__name__} said so")

        def refuse(*_a, exc=exc):
            raise exc

        body = _plan_route_with_build_plans(monkeypatch, refuse)
        assert body["outcome"] == "no_lineup", cls.__name__
        assert cls.__name__ in body["reason"]
