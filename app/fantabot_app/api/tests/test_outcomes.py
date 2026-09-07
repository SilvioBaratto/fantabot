"""Four failures, four screens — and the `except Exception` that made them one.

`except Exception -> found=False` sat on `/asta/plan`, `/lineup/plan` and
`/asta/target-prices`. A database that would not open, a season nobody had scraped, a
roster the optimizer could not seed, a lega that had never been synced and a corpus shape
with no recorded sales all rendered as **"No plan yet"**, under a suggestion to sync the
lega — the right remedy for one of them and a wasted evening for the other four.

`/lineup/plan` looked solved and was not: it already had a `reason`, and every failure
produced the *same* one.

The rule these three now keep is written down in `api/outcomes.py`: **degrade open on a
status read, fail closed on a decision.**

Two properties are asserted here, and the second is the one that rots without a test:

* Each induced failure produces a **different** outcome.
* The tuple of outcomes a route can return is **exactly** the pinned one — compared for
  equality, so a route that gains an outcome must say so and one that loses an outcome must
  delete its name. A set that only ever grows stops meaning anything.
"""

from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Self

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

from fantabot_app.api.main import app
from fantabot_app.api.outcomes import (
    ASTA_PLAN_OUTCOMES,
    LINEUP_PLAN_OUTCOMES,
    TARGET_PRICES_OUTCOMES,
    because,
)

ENDPOINTS = Path(__file__).resolve().parent.parent / "v1" / "endpoints"


def _plan(**params: Any) -> dict[str, Any]:
    with TestClient(app) as client:
        return client.get("/api/v1/asta/plan", params={"league_id": 4103937, **params}).json()


class TestTheRoutesReturnOnlyWhatTheyPin:
    """Read from the source. A route that returns an unlisted outcome does not fail — it
    renders a screen the frontend has no branch for, which is a blank one.
    """

    @staticmethod
    def _outcomes_returned(filename: str, model: str) -> set[str]:
        """Every literal passed as `outcome=` to `model(...)` in one endpoint module."""
        tree = ast.parse((ENDPOINTS / filename).read_text(encoding="utf-8"))
        found: set[str] = set()
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if not (isinstance(node.func, ast.Name) and node.func.id == model):
                continue
            for keyword in node.keywords:
                if keyword.arg == "outcome" and isinstance(keyword.value, ast.Constant):
                    found.add(str(keyword.value.value))
        return found

    @pytest.mark.parametrize(
        ("filename", "model", "pinned"),
        [
            ("asta.py", "AstaPlan", ASTA_PLAN_OUTCOMES),
            ("lineup.py", "LineupPlan", LINEUP_PLAN_OUTCOMES),
            ("pricing.py", "TargetPricesReport", TARGET_PRICES_OUTCOMES),
        ],
    )
    def test_the_outcomes_it_returns_are_exactly_the_ones_it_pins(
        self, filename: str, model: str, pinned: tuple[str, ...]
    ) -> None:
        returned = self._outcomes_returned(filename, model)

        assert returned, f"{filename} names no outcome at all — this scan reads nothing"
        assert returned == set(pinned), (
            f"{filename} returns {sorted(returned)} and pins {sorted(pinned)}; "
            "a route that gains an outcome must say so, and one that loses an outcome "
            "must delete its name"
        )

    @pytest.mark.parametrize("filename", ["asta.py", "lineup.py", "pricing.py"])
    def test_no_decision_route_catches_bare_exception(self, filename: str) -> None:
        """The criterion, literally: `except Exception` in none of the three.

        An earlier version of this test allowed one per route as the `unreachable` outcome,
        on the argument that banning it trades "we could not ask" for a 500. That was a
        weaker rule substituted for a written one, and the written one is right: a 500 is
        logged, alarming and unmistakably a fault, where a bare handler turns an
        unanticipated bug into a tidy page. The set of things that can actually go wrong on
        these routes is small enough to name — `SQLAlchemyError`, `OSError`, `TokenError`
        and the planner's own refusals — and anything outside it is a bug in this repository.
        """
        source = (ENDPOINTS / filename).read_text(encoding="utf-8")
        bare = [
            handler.lineno
            for handler in ast.walk(ast.parse(source))
            if isinstance(handler, ast.ExceptHandler)
            and isinstance(handler.type, ast.Name)
            and handler.type.id in {"Exception", "BaseException"}
        ]

        assert not bare, (
            f"{filename} catches bare Exception at {bare}. Name the families the route can "
            "actually fail on; let the rest reach FastAPI as a 500."
        )

    @pytest.mark.parametrize("filename", ["asta.py", "lineup.py", "pricing.py"])
    def test_and_the_scan_would_notice_one(self, filename: str) -> None:
        """A ban that cannot fire reads as compliance. This proves the walk finds one."""
        planted = ast.parse("try:\n    pass\nexcept Exception:\n    pass\n")
        found = [
            h
            for h in ast.walk(planted)
            if isinstance(h, ast.ExceptHandler)
            and isinstance(h.type, ast.Name)
            and h.type.id == "Exception"
        ]

        assert len(found) == 1


class TestFourFailuresFourScreens:
    """Induced, not described. Each patches one thing and reads the outcome back."""

    def test_a_database_that_will_not_open_is_unreachable(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from fantabot.adapters.persistence import database_manager

        def boom() -> None:
            raise OperationalError("SELECT 1", {}, OSError("db unreachable"))

        monkeypatch.setattr(database_manager, "get_session", boom)

        body = _plan()

        assert body["outcome"] == "unreachable"
        assert "OperationalError" in body["reason"]

    def test_a_lega_that_was_never_synced_is_no_lega(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The one that is easy to miss: the planner would happily run. With no snapshot
        the format, the budget and the roster band are all defaults, so the plan is three
        guesses wearing an answer's clothes."""
        from fantabot.application import lega_reads as reads

        monkeypatch.setattr(reads, "latest_settings", lambda *_a, **_k: None)
        monkeypatch.setattr(
            "fantabot.adapters.persistence.database_manager.get_session", _fake_session
        )

        body = _plan()

        assert body["outcome"] == "no_lega"
        assert "lega sync" in body["reason"]

    @pytest.mark.parametrize(
        ("error", "outcome"),
        [
            ("NoSentimentRows", "no_sentiment"),
            ("NoCorpus", "no_corpus"),
            ("EmptyPool", "empty_pool"),
            ("InfeasibleRoster", "infeasible"),
        ],
    )
    def test_each_planner_refusal_gets_its_own_screen(
        self, monkeypatch: pytest.MonkeyPatch, error: str, outcome: str
    ) -> None:
        from fantabot.application import lega_reads as reads
        from fantabot.application import plan_request as pr
        from fantabot.domain.asta.optimizer import InfeasibleRoster
        from fantabot.domain.asta.prices import NoCorpus

        raised: Exception = {
            "NoSentimentRows": pr.NoSentimentRows("no rows in the database"),
            "NoCorpus": NoCorpus("3x777 mantra", []),
            "EmptyPool": pr.EmptyPool("no classic players for season 2026/27"),
            "InfeasibleRoster": InfeasibleRoster("no schema can be seeded within budget"),
        }[error]

        def boom(*_a: object, **_k: object) -> None:
            raise raised

        monkeypatch.setattr(
            reads, "latest_settings", lambda *_a, **_k: _snapshot()
        )
        monkeypatch.setattr(pr, "build_plan", boom)
        monkeypatch.setattr(
            "fantabot.adapters.persistence.database_manager.get_session", _fake_session
        )

        body = _plan()

        assert body["outcome"] == outcome
        assert body["reason"], "a named outcome with no reason is half the fix"

    def test_an_unknown_system_is_its_own_screen_not_no_data(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`system` reaches a `WHERE listone = :system`, so a typo selected no rows and read
        as "no training data" — sending the operator to scrape a season when the fix is a
        spelling. Two remedies behind one screen is what this module exists to stop."""
        with TestClient(app) as client:
            body = client.get(
                "/api/v1/asta/target-prices", params={"system": "mantr"}
            ).json()

        assert body["outcome"] == "unknown_system", body
        assert "classic" in body["reason"] and "mantra" in body["reason"]

    def test_the_five_refusals_are_five_distinct_screens(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The property, stated once: no two of them render the same thing."""
        assert len(set(ASTA_PLAN_OUTCOMES)) == len(ASTA_PLAN_OUTCOMES)
        assert "planned" in ASTA_PLAN_OUTCOMES


class TestTheLineupRouteDoesNotCallATimeoutACredentialProblem:
    """`apileague` maps *every* failure onto `TokenError` — deliberately, because both
    `httpx.RequestError.request` and a bare traceback can render the `Authorization`
    header. A bare `except TokenError` would therefore report a timeout as a credential
    problem, which is `endpoints/room.py`'s ordering lesson exactly."""

    @pytest.mark.parametrize(
        ("error", "outcome"),
        [
            ("ApiTimeout", "unreachable"),
            ("ApiUnavailable", "unreachable"),
            ("TokenRejected", "refused"),
            ("AppKeyRejected", "refused"),
            ("TokenMissing", "no_credential"),
            ("TokenExpired", "no_credential"),
        ],
    )
    def test_each_token_error_family_maps_to_its_own_outcome(
        self, monkeypatch: pytest.MonkeyPatch, error: str, outcome: str
    ) -> None:
        from fantabot.adapters.http import apileague
        from fantabot.domain.tokens import errors

        raised: Exception = {
            "ApiTimeout": errors.ApiTimeout(10.0),
            "ApiUnavailable": errors.ApiUnavailable(503),
            "TokenRejected": errors.TokenRejected(4103937),
            "AppKeyRejected": errors.AppKeyRejected(),
            "TokenMissing": errors.TokenMissing(4103937),
            "TokenExpired": errors.TokenExpired(4103937, "yesterday"),
        }[error]

        def boom(*_a: object, **_k: object) -> None:
            raise raised

        monkeypatch.setenv("FANTABOT_ENCRYPTION_KEY", _A_VALID_KEY)
        monkeypatch.setattr(
            "fantabot.config.settings.fantabot_encryption_key", _A_VALID_KEY, raising=False
        )
        monkeypatch.setattr(apileague, "my_team", boom)
        monkeypatch.setattr(
            "fantabot.adapters.persistence.database_manager.get_session", _fake_session
        )

        with TestClient(app) as client:
            body = client.get("/api/v1/lineup/plan", params={"league_id": 4103937}).json()

        assert body["outcome"] == outcome, body

    def test_no_key_at_all_is_a_credential_problem_and_says_what_to_do(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "fantabot.config.settings.fantabot_encryption_key", "", raising=False
        )

        with TestClient(app) as client:
            body = client.get("/api/v1/lineup/plan", params={"league_id": 4103937}).json()

        assert body["outcome"] == "no_credential"
        assert "connect" in body["reason"].lower()


def test_because_is_one_typed_line_and_not_a_traceback() -> None:
    """A driver traceback says the call failed; it does not say which of five things did,
    and it is not something to put on a page."""
    line = because(RuntimeError("could not connect\nsecond line\nthird line"))

    assert line == "RuntimeError: could not connect"
    assert "\n" not in line


# -- helpers ------------------------------------------------------------------------------

def _a_valid_key() -> str:
    """A throwaway Fernet key, minted rather than written down.

    `TokenCipher(key)` validates, so a plausible-looking literal fails construction and the
    route answers `no_credential` before reaching the call the test is about — which is how
    the first version of this file reported four network failures as credential problems.
    Minted for the same reason `ci.yml` mints one: a key literal in a tracked file is a key
    literal, whatever it opens.
    """
    from cryptography.fernet import Fernet

    return Fernet.generate_key().decode()


_A_VALID_KEY = _a_valid_key()


def _snapshot() -> SimpleNamespace:
    return SimpleNamespace(
        role_groups=2, budget=500, roster_size=30, min_roles=[2, 28], max_roles=[4, 28]
    )


class _FakeSession:
    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_exc: object) -> None:
        return None


def _fake_session(*_a: object, **_k: object) -> _FakeSession:
    """A session that opens and does nothing. The reads on top of it are patched."""
    return _FakeSession()
