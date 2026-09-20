"""T25 — `POST /db/snapshot-team` and `POST /db/backfill-teams`, for Synchronize.

One implementation, two printers: `application/team_maintenance.py` chooses the
endpoint, the parser and the repository method, and this module maps its result onto
the wire. Both are writes, so both **fail closed** — a failure is a named outcome and
never a 200 that reads like success, which for `snapshot-team` matters because
`league_team_snapshot` is append-only and a capture that did not happen leaves a
permanent gap.

`backfill-teams` is here rather than in `endpoints/db.py` for `exclusions.py`'s reason:
that module's subject is the health probe and its "must never 500" rule is the opposite
of the one a write needs.

Zero sockets: `database_manager.get_session` is patched and the use case is replaced at
the module the route imports it from.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any

import pytest
from fantabot.domain.shared.league import TeamSnapshot
from fantabot.domain.tokens.errors import ApiTimeout, TokenMissing, TokenRejected
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

from fantabot_app.api.main import app
from fantabot_app.api.v1.endpoints.teams import (
    BACKFILL_OUTCOMES,
    SNAPSHOT_OUTCOMES,
    to_wire,
)

MINE = TeamSnapshot(
    league_id=4103937,
    team_id=1,
    user_id=9,
    nome="Legamiallerotaie",
    owner="silvio",
    credits_initial=500,
    credits_spent=474,
    credits_remaining=26,
)


@contextmanager
def _session() -> Any:
    yield object()


@pytest.fixture
def db_answers(monkeypatch: pytest.MonkeyPatch) -> None:
    from fantabot.adapters.persistence import database_manager

    monkeypatch.setattr(database_manager, "get_session", _session)


def _a_key() -> str:
    """A real Fernet key, generated rather than pasted. `TokenCipher` validates the
    length, so a 32-character placeholder answers `no_credential` for the wrong reason
    and every outcome below then reads as passing."""
    from cryptography.fernet import Fernet

    return str(Fernet.generate_key().decode())


KEY = _a_key()


@pytest.fixture
def has_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """A key that decrypts nothing — the route only needs one to exist before it asks."""
    from fantabot.config import settings

    monkeypatch.setattr(settings, "fantabot_encryption_key", KEY, raising=False)


def _raise(exc: Exception) -> Any:
    def call(*_a: Any, **_k: Any) -> Any:
        raise exc

    return call


class TestTheMapping:
    """Pure, and the place a field silently stops being sent."""

    def test_every_credit_crosses(self) -> None:
        wire = to_wire(MINE)

        assert (wire.league_id, wire.team_id, wire.nome, wire.owner) == (
            4103937, 1, "Legamiallerotaie", "silvio",
        )
        assert (wire.credits_initial, wire.credits_spent, wire.credits_remaining) == (
            500, 474, 26,
        )

    def test_an_absent_credit_crosses_as_null_not_zero(self) -> None:
        """`cri`/`crs`/`cr` are optional on the wire the platform sends. The CLI prints
        `or 0` because a terminal line needs a number; a JSON field does not, and a 0
        there is a claim that the team has spent nothing."""
        blank = TeamSnapshot(
            league_id=4103937, team_id=1, user_id=None, nome="T", owner="",
            credits_initial=None, credits_spent=None, credits_remaining=None,
        )

        wire = to_wire(blank)

        assert wire.credits_initial is None
        assert wire.credits_spent is None
        assert wire.credits_remaining is None


class TestSnapshotTeam:
    def test_a_capture_answers_saved_and_carries_the_row(
        self, monkeypatch: pytest.MonkeyPatch, db_answers: None, has_key: None
    ) -> None:
        from fantabot_app.api.v1.endpoints import teams

        monkeypatch.setattr(teams, "snapshot_team", lambda *_a, **_k: MINE)
        body = TestClient(app).post(
            "/api/v1/db/snapshot-team", json={"league_id": 4103937}
        ).json()

        assert body["outcome"] == "saved"
        assert body["team_id"] == 1
        assert body["credits_remaining"] == 26

    def test_the_lega_asked_for_is_the_lega_in_the_request(
        self, monkeypatch: pytest.MonkeyPatch, db_answers: None, has_key: None
    ) -> None:
        """`teams/my` carries no league id of its own, so the one passed in is the only
        thing that decides which lega the row lands under. Asking one and recording
        another is a mistake nothing downstream can detect, in an append-only table."""
        from fantabot_app.api.v1.endpoints import teams

        seen: list[int] = []

        def capture(_session: Any, league_id: int, **_k: Any) -> Any:
            seen.append(league_id)
            return MINE

        monkeypatch.setattr(teams, "snapshot_team", capture)
        TestClient(app).post("/api/v1/db/snapshot-team", json={"league_id": 3584692})

        assert seen == [3584692]

    def test_no_key_is_answered_before_anything_is_asked(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """No encryption key means no token can be read at all. Asking anyway produces a
        `KeyMissing` whose wording is about a key file, which is a worse sentence than
        the one the operator needs: connect an account."""
        from fantabot.config import settings

        monkeypatch.setattr(settings, "fantabot_encryption_key", "", raising=False)
        body = TestClient(app).post(
            "/api/v1/db/snapshot-team", json={"league_id": 4103937}
        ).json()

        assert body["outcome"] == "no_credential"
        assert body["team_id"] is None
        # The wording is the assertion. Falling through, `TokenCipher("")` also answers
        # `no_credential` — with `KeyMalformed`'s sentence about a 44-character Fernet
        # key, which is true and is not the operator's next move.
        assert body["reason"] == "No encryption key set — connect an account first."

    def test_a_missing_token_is_no_credential_not_unreachable(
        self, monkeypatch: pytest.MonkeyPatch, db_answers: None, has_key: None
    ) -> None:
        """`auth login` is the remedy. "The platform is down" sends the operator to wait."""
        from fantabot_app.api.v1.endpoints import teams

        monkeypatch.setattr(teams, "snapshot_team", _raise(TokenMissing("no token for 4103937")))
        body = TestClient(app).post(
            "/api/v1/db/snapshot-team", json={"league_id": 4103937}
        ).json()

        assert body["outcome"] == "no_credential"
        assert "4103937" in body["reason"]

    def test_a_rejected_token_is_refused(
        self, monkeypatch: pytest.MonkeyPatch, db_answers: None, has_key: None
    ) -> None:
        """The platform answered, and said no. Distinct from never having asked."""
        from fantabot_app.api.v1.endpoints import teams

        monkeypatch.setattr(teams, "snapshot_team", _raise(TokenRejected("401")))
        body = TestClient(app).post(
            "/api/v1/db/snapshot-team", json={"league_id": 4103937}
        ).json()

        assert body["outcome"] == "refused"

    def test_a_timeout_is_unreachable(
        self, monkeypatch: pytest.MonkeyPatch, db_answers: None, has_key: None
    ) -> None:
        from fantabot_app.api.v1.endpoints import teams

        monkeypatch.setattr(teams, "snapshot_team", _raise(ApiTimeout(8.0)))
        body = TestClient(app).post(
            "/api/v1/db/snapshot-team", json={"league_id": 4103937}
        ).json()

        assert body["outcome"] == "unreachable"

    def test_a_database_that_will_not_open_is_unreachable(
        self, monkeypatch: pytest.MonkeyPatch, has_key: None
    ) -> None:
        from fantabot.adapters.persistence import database_manager

        monkeypatch.setattr(
            database_manager,
            "get_session",
            _raise(OperationalError("SELECT 1", {}, Exception("refused"))),
        )
        body = TestClient(app).post(
            "/api/v1/db/snapshot-team", json={"league_id": 4103937}
        ).json()

        assert body["outcome"] == "unreachable"
        assert "OperationalError" in body["reason"]


class TestBackfillTeams:
    def test_it_reports_how_many_names_were_resolved(
        self, monkeypatch: pytest.MonkeyPatch, db_answers: None
    ) -> None:
        from fantabot_app.api.v1.endpoints import teams

        monkeypatch.setattr(teams, "backfill_teams", lambda _s: 17)
        body = TestClient(app).post("/api/v1/db/backfill-teams").json()

        assert body["outcome"] == "resolved"
        assert body["changed"] == 17

    def test_zero_resolved_is_still_resolved(
        self, monkeypatch: pytest.MonkeyPatch, db_answers: None
    ) -> None:
        """A fresh database has no `match_grain` names to resolve from. That is the
        backfill working, not the backfill failing — the repository returns 0 on purpose
        so a July listone-first scrape still succeeds."""
        from fantabot_app.api.v1.endpoints import teams

        monkeypatch.setattr(teams, "backfill_teams", lambda _s: 0)
        body = TestClient(app).post("/api/v1/db/backfill-teams").json()

        assert body["outcome"] == "resolved"
        assert body["changed"] == 0

    def test_an_untrustworthy_mapping_carries_the_refusals_own_words(
        self, monkeypatch: pytest.MonkeyPatch, db_answers: None
    ) -> None:
        """The sentence the operator reads on a page and the one they read in a terminal
        are the same sentence, because they are the same person."""
        from fantabot.application.team_maintenance import NamesUnresolved

        from fantabot_app.api.v1.endpoints import teams

        monkeypatch.setattr(
            teams, "backfill_teams", _raise(NamesUnresolved("no name for code 'PIS' — scrape voti"))
        )
        body = TestClient(app).post("/api/v1/db/backfill-teams").json()

        assert body["outcome"] == "unresolved"
        assert body["changed"] == 0
        assert "PIS" in body["reason"]

    def test_a_database_that_will_not_open_is_unreachable(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Kept apart from `unresolved`: one remedy is a scrape, the other is a database."""
        from fantabot.adapters.persistence import database_manager

        monkeypatch.setattr(
            database_manager,
            "get_session",
            _raise(OperationalError("SELECT 1", {}, Exception("refused"))),
        )
        body = TestClient(app).post("/api/v1/db/backfill-teams").json()

        assert body["outcome"] == "unreachable"
        assert body["changed"] == 0


class TestTheOutcomesAreExactlyWhatIsPinned:
    """`api/outcomes.py`'s ratchet, applied locally as `lineup.py::SUBMIT_OUTCOMES` is.
    A route that gains an outcome must say so; one that loses an outcome must delete
    its name, or the frontend renders a branch it does not have — a blank screen."""

    @staticmethod
    def _returned(model: str) -> set[str]:
        import ast
        from pathlib import Path

        import fantabot_app.api.v1.endpoints.teams as module

        tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
        return {
            str(keyword.value.value)
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == model
            for keyword in node.keywords
            if keyword.arg == "outcome" and isinstance(keyword.value, ast.Constant)
        }

    @pytest.mark.parametrize(
        ("model", "pinned"),
        [("TeamSnapshotResult", SNAPSHOT_OUTCOMES), ("BackfillResult", BACKFILL_OUTCOMES)],
    )
    def test_the_outcomes_it_returns_are_exactly_the_ones_it_pins(
        self, model: str, pinned: tuple[str, ...]
    ) -> None:
        returned = self._returned(model)

        assert returned, f"{model} names no outcome at all — this scan reads nothing"
        assert returned == set(pinned)

    def test_neither_route_catches_bare_exception(self) -> None:
        """A bare handler turns an unanticipated bug into a tidy page saying we could
        not ask. The families these two can fail on are small enough to name."""
        import ast
        from pathlib import Path

        import fantabot_app.api.v1.endpoints.teams as module

        bare = [
            handler.lineno
            for handler in ast.walk(ast.parse(Path(module.__file__).read_text(encoding="utf-8")))
            if isinstance(handler, ast.ExceptHandler)
            and isinstance(handler.type, ast.Name)
            and handler.type.id in {"Exception", "BaseException"}
        ]

        assert not bare, f"teams.py catches bare Exception at {bare}"
