"""S10 — /asta/target-prices."""

from __future__ import annotations

from types import SimpleNamespace

from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

from fantabot_app.api.main import app
from fantabot_app.api.v1.endpoints.pricing import build_report


def _row(nome: str, qi: int, target: int):
    return SimpleNamespace(
        id="1",
        nome=nome,
        squadra="ROM",
        role="A",
        macro_role="A",
        qi=qi,
        prior_media_fantavoto=6.5,
        predicted_pct_delta=0.1,
        team_factor=1.0,
        target_price=target,
        flags="",
    )


def test_build_report_maps_bumps_cuts_and_fades() -> None:
    report = SimpleNamespace(
        system="classic",
        stored=42,
        fades=[SimpleNamespace(role="A", observations=120, fade=None)],
        team_factors={"ROM": 1.0},
        biggest_bumps=[_row("Dybala", 20, 28)],
        biggest_cuts=[_row("Someone", 15, 8)],
        flag_counts={"floor_qi": 3},
    )
    out = build_report(report)
    assert out.found is True
    assert out.system == "classic"
    assert out.stored == 42
    assert out.biggest_bumps[0].nome == "Dybala"
    assert out.fades[0].observations == 120
    assert out.flag_counts["floor_qi"] == 3


def test_target_prices_degrades_open_on_error(monkeypatch) -> None:
    """The GET fits and does not store, so `fit` is what it calls. See T38."""
    from fantabot.application import pricing

    def boom(**_kwargs):
        # A driver failure. Since 1.7 this route catches `SQLAlchemyError`/`OSError` and
        # `LookupError`, and nothing else — a bare `RuntimeError` is now a 500, on purpose.
        raise OperationalError("SELECT 1", {}, OSError("no data"))

    monkeypatch.setattr(pricing, "fit", boom)

    response = TestClient(app).get("/api/v1/asta/target-prices")
    assert response.status_code == 200
    body = response.json()
    assert body["found"] is False
    assert body["outcome"] == "unreachable"
    assert body["biggest_bumps"] == []


def test_the_get_never_reaches_the_call_that_writes(monkeypatch) -> None:
    """`pricing.run` upserts `target_price`, and the GET called it — so opening the Prices
    page mutated the database. Proven by making the writing call fatal rather than by
    reading the code: a later refactor that reroutes the GET back through `run` fails here.
    """
    from fantabot.application import pricing

    def refuse(**_kwargs):
        raise AssertionError("the GET called pricing.run, which upserts target_price")

    monkeypatch.setattr(pricing, "run", refuse)
    monkeypatch.setattr(pricing, "fit", lambda **_k: _EMPTY_REPORT)

    response = TestClient(app).get("/api/v1/asta/target-prices")

    assert response.status_code == 200


def test_the_post_is_the_one_that_writes(monkeypatch) -> None:
    from fantabot.application import pricing

    called: list[str] = []
    monkeypatch.setattr(
        pricing, "run", lambda **_k: (called.append("run"), _EMPTY_REPORT)[1]
    )
    monkeypatch.setattr(
        pricing, "fit", lambda **_k: (called.append("fit"), _EMPTY_REPORT)[1]
    )

    TestClient(app).post("/api/v1/asta/target-prices")

    assert called == ["run"]


#: The shape `_report` reads before deciding on `no_data`. A namespace rather than a class,
#: so the empty mappings are instance state and not shared class attributes.
_EMPTY_REPORT = SimpleNamespace(
    system="classic",
    stored=0,
    fades=(),
    team_factors={},
    biggest_bumps=(),
    biggest_cuts=(),
    flag_counts={},
)
