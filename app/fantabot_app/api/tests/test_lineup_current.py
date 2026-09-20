"""`GET /lineup/current` — the lineup the platform currently has saved.

`fantabot lineup show`'s screen, and the last of the thirty-two commands to get one. It is a
different question from `GET /lineup/plan`: that one asks *what should we field*, this one
asks *what is actually saved right now*, and on a matchday where a submit was refused the two
answers differ — which is the case an operator most needs to see.

**It returns ids, exactly as the command prints them.** Naming them would mean running
`build_plans` — seven live reads and a solve — to annotate a read that has already answered,
and it would fail for reasons that have nothing to do with the saved lineup. The page already
holds the plan for the same lega and joins the names client-side, falling back to the id.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from fantabot_app.api.main import app


@pytest.fixture
def wired(monkeypatch):
    """The one live read, faked. `store` is never opened: the route hands it over."""
    from fantabot_app.api.v1.endpoints import lineup as endpoint

    seen: dict[str, Any] = {}

    def fake_read(league_id: int, competition: int, *, store: Any) -> dict[str, Any]:
        seen["args"] = (league_id, competition)
        return seen.get("body", {"teamLineupDto": {"mdl": "343", "starts": [1, 2], "bench": [9]}})

    monkeypatch.setattr(endpoint, "teamLineup_read", fake_read, raising=False)
    monkeypatch.setattr(endpoint, "_open_store", lambda: _NullStore(), raising=False)
    return seen


class _NullStore:
    def __enter__(self) -> Any:
        return object()

    def __exit__(self, *_a: object) -> None:
        return None


def _get(client: TestClient, **params: Any):
    return client.get(
        "/api/v1/lineup/current", params={"league_id": 4103937, "competition": 12, **params}
    )


def test_it_reads_the_competition_it_was_given(wired) -> None:
    body = _get(TestClient(app)).json()

    assert wired["args"] == (4103937, 12)
    assert body["outcome"] == "read"
    assert body["module"] == "343"
    assert body["starters"] == [1, 2] and body["bench"] == [9]


def test_a_competition_with_nothing_saved_is_its_own_outcome(wired) -> None:
    """Not an error and not an empty XI: a competition whose lineup has never been set is
    the ordinary state before the first submit of a matchday, and reading it as "no
    starters" is a claim about the roster rather than about the save."""
    wired["body"] = {"teamLineupDto": {}}
    body = _get(TestClient(app)).json()

    assert body["outcome"] == "no_lineup"
    assert body["reason"]
    assert body["starters"] == [] and body["bench"] == []


def test_a_competition_id_is_required_rather_than_guessed(wired) -> None:
    """`lineup show` refuses without one — a lineup belongs to a competition, and picking
    one would answer a question nobody asked."""
    assert TestClient(app).get(
        "/api/v1/lineup/current", params={"league_id": 4103937}
    ).status_code == 422


@pytest.mark.parametrize(
    ("exc", "outcome"),
    [("TokenRejected", "refused"), ("TokenError", "no_credential"),
     ("ApiUnavailable", "unreachable"), ("SQLAlchemyError", "unreachable")],
)
def test_each_failure_is_its_own_name(wired, monkeypatch, exc: str, outcome: str) -> None:
    """Five outcomes, one per cause — `outcomes.py`'s rule. A read that cannot reach the
    platform and a token the platform rejected need different things from the operator."""
    from fantabot.domain.tokens.errors import ApiUnavailable, TokenError, TokenRejected
    from sqlalchemy.exc import SQLAlchemyError

    from fantabot_app.api.v1.endpoints import lineup as endpoint

    raises = {
        "TokenRejected": TokenRejected("the platform rejected it"),
        "TokenError": TokenError("nothing stored"),
        "ApiUnavailable": ApiUnavailable("connection reset"),
        "SQLAlchemyError": SQLAlchemyError("the database would not open"),
    }[exc]

    def boom(*_a: Any, **_k: Any) -> Any:
        raise raises

    monkeypatch.setattr(endpoint, "teamLineup_read", boom, raising=False)
    body = _get(TestClient(app)).json()

    assert body["outcome"] == outcome and body["reason"]


def test_the_outcomes_are_pinned() -> None:
    from fantabot_app.api.outcomes import LINEUP_CURRENT_OUTCOMES

    assert LINEUP_CURRENT_OUTCOMES == (
        "read", "no_lineup", "no_credential", "refused", "unreachable",
    )
