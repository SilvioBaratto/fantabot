"""The headed connect-account login: job + prompt-gate + confirm.

No browser is launched and no socket opened: the use cases are faked, so the wiring —
start the job, block on the prompt, confirm releases it, job completes — is exercised
deterministically.

Auto-detection replaced this gate briefly and was reverted: polling the browser's
storage opened a burst of tabs over the login form every two seconds. What survived the
revert is the gate's timeout behaviour, which now raises instead of falling through to
the read.
"""

from __future__ import annotations

import contextlib
import time
from types import SimpleNamespace

from fastapi.testclient import TestClient

from fantabot_app.api.main import app


def _job(client: TestClient, job_id: str) -> dict:
    return client.get(f"/api/v1/jobs/{job_id}").json()


def _wait(predicate, timeout: float = 3.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(0.02)
    return False


def test_login_starts_a_job_that_reports_through_the_registry(monkeypatch) -> None:
    from fantabot.application import auth_login

    def fake_run(**kwargs):
        kwargs["report"].print("Opening login")
        kwargs["report"].print("captured")
        return SimpleNamespace(ok=True)

    monkeypatch.setattr(auth_login, "run", fake_run)

    client = TestClient(app)
    job_id = client.post("/api/v1/auth/login").json()["job_id"]

    assert _wait(lambda: _job(client, job_id)["status"] == "done")
    assert _job(client, job_id)["ok"] is True
    assert "Opening login" in " ".join(_job(client, job_id)["lines"])


def test_confirm_unknown_job_is_404() -> None:
    assert TestClient(app).post("/api/v1/auth/login/nope/confirm").status_code == 404


def test_an_unconfirmed_login_stores_nothing(monkeypatch) -> None:
    """The gate's timeout raises instead of falling through.

    It used to discard `wait`'s result, so a login nobody confirmed woke after ten
    minutes and read the browser anyway — storing whatever happened to be in it.
    """
    import fantabot_app.api.v1.endpoints.auth as auth_module

    monkeypatch.setattr(auth_module, "_LOGIN_TIMEOUT_S", 0.05)
    read = {"happened": False}

    def fake_run(**kwargs):
        kwargs["prompt"]("confirm please")
        read["happened"] = True  # must never be reached
        return SimpleNamespace(ok=True)

    from fantabot.application import auth_login

    monkeypatch.setattr(auth_login, "run", fake_run)

    client = TestClient(app)
    job_id = client.post("/api/v1/auth/login").json()["job_id"]

    assert _wait(lambda: _job(client, job_id)["status"] == "error")
    assert read["happened"] is False
    assert "Nothing was written" in (_job(client, job_id)["error"] or "")


def test_login_is_given_a_state_reader(monkeypatch) -> None:
    """The poll needs a way to read the browser, injected by the endpoint.

    Without it the use case would have to import playwright itself, which the layer
    rules forbid — so its absence is a wiring bug the type checker cannot see.
    """
    from fantabot.application import auth_login

    seen: dict[str, object] = {}

    def fake_run(**kwargs):
        seen.update(kwargs)
        return SimpleNamespace(ok=True)

    monkeypatch.setattr(auth_login, "run", fake_run)

    client = TestClient(app)
    job_id = client.post("/api/v1/auth/login").json()["job_id"]
    assert _wait(lambda: _job(client, job_id)["status"] == "done")
    assert callable(seen["read_state"])
    assert callable(seen["prompt"])


def test_fantalab_login_starts_a_job(monkeypatch) -> None:
    from fantabot.application import fantalab_login

    def fake_run(**kwargs):
        kwargs["report"].print("Opening FantaLab")
        kwargs["report"].print("session captured")
        return SimpleNamespace(ok=True)

    monkeypatch.setattr(fantalab_login, "run", fake_run)

    client = TestClient(app)
    job_id = client.post("/api/v1/auth/fantalab-login").json()["job_id"]

    assert _wait(lambda: _job(client, job_id)["status"] == "done")
    assert _job(client, job_id)["ok"] is True


def test_fantalab_login_is_given_a_state_reader(monkeypatch) -> None:
    from fantabot.application import fantalab_login

    seen: dict[str, object] = {}

    def fake_run(**kwargs):
        seen.update(kwargs)
        return SimpleNamespace(ok=True)

    monkeypatch.setattr(fantalab_login, "run", fake_run)

    client = TestClient(app)
    job_id = client.post("/api/v1/auth/fantalab-login").json()["job_id"]
    assert _wait(lambda: _job(client, job_id)["status"] == "done")
    assert callable(seen["read_state"])
    assert callable(seen["prompt"])


def test_login_forwards_force_to_the_use_case(monkeypatch) -> None:
    """Without this the UI cannot recover a disconnected lega.

    `auth_login.run` opens no browser when every *remaining* stored token is valid, so
    after disconnecting one of two leghe the survivor makes the re-auth a silent no-op.
    """
    from fantabot.application import auth_login

    seen: dict[str, object] = {}

    def fake_run(**kwargs):
        seen.update(kwargs)
        return SimpleNamespace(ok=True)

    monkeypatch.setattr(auth_login, "run", fake_run)

    client = TestClient(app)
    job_id = client.post("/api/v1/auth/login?force=true").json()["job_id"]
    assert _wait(lambda: _job(client, job_id)["status"] == "done")
    assert seen["force"] is True


def test_login_does_not_force_by_default(monkeypatch) -> None:
    from fantabot.application import auth_login

    seen: dict[str, object] = {}

    def fake_run(**kwargs):
        seen.update(kwargs)
        return SimpleNamespace(ok=True)

    monkeypatch.setattr(auth_login, "run", fake_run)

    client = TestClient(app)
    job_id = client.post("/api/v1/auth/login").json()["job_id"]
    assert _wait(lambda: _job(client, job_id)["status"] == "done")
    assert seen["force"] is False


def test_fantalab_login_forwards_force(monkeypatch) -> None:
    """`fantalab_login.run` refuses to open a browser while any session is stored."""
    from fantabot.application import fantalab_login

    seen: dict[str, object] = {}

    def fake_run(**kwargs):
        seen.update(kwargs)
        return SimpleNamespace(ok=True)

    monkeypatch.setattr(fantalab_login, "run", fake_run)

    client = TestClient(app)
    job_id = client.post("/api/v1/auth/fantalab-login?force=true").json()["job_id"]
    assert _wait(lambda: _job(client, job_id)["status"] == "done")
    assert seen["force"] is True


# --- disconnect -------------------------------------------------------------------


class _FakeSession:
    """Stands in for the real session context manager: no engine, no socket."""


@contextlib.contextmanager
def _fake_get_session():
    yield _FakeSession()


def _patch_session(monkeypatch, purged: dict[str, int] | None = None) -> None:
    """Fake the session, and the lega purge that now runs alongside the token delete."""
    from fantabot.adapters.persistence import database_manager
    from fantabot.adapters.persistence.repositories import league as league_repo

    class FakeLeagueRepository:
        def __init__(self, session) -> None:
            pass

        def purge(self, league_id: int) -> dict[str, int]:
            return dict(purged or {})

    monkeypatch.setattr(database_manager, "get_session", _fake_get_session)
    monkeypatch.setattr(league_repo, "LeagueRepository", FakeLeagueRepository)


def test_forget_league_removes_the_row(monkeypatch) -> None:
    from fantabot.adapters.tokens import store as store_module

    forgotten: list[int] = []

    class FakeStore:
        def __init__(self, session, cipher=None) -> None:
            # Keyless: an operator who lost the key must still be able to clear rows.
            assert cipher is None

        def forget(self, league_id: int) -> bool:
            forgotten.append(league_id)
            return True

    _patch_session(monkeypatch, purged={"league_snapshot": 3, "league_player_pool": 1776})
    monkeypatch.setattr(store_module, "TokenStore", FakeStore)

    response = TestClient(app).delete("/api/v1/auth/league/4103937")
    assert response.status_code == 200
    # The lega goes with the token: leaving its rows behind kept the lega on every
    # screen except the one that said it was disconnected.
    assert response.json() == {"ok": True, "removed": True, "rows_removed": 1779}
    assert forgotten == [4103937]


def test_forget_league_reports_when_there_was_nothing(monkeypatch) -> None:
    from fantabot.adapters.tokens import store as store_module

    class FakeStore:
        def __init__(self, session, cipher=None) -> None:
            pass

        def forget(self, league_id: int) -> bool:
            return False

    _patch_session(monkeypatch)
    monkeypatch.setattr(store_module, "TokenStore", FakeStore)

    assert TestClient(app).delete("/api/v1/auth/league/999").json() == {
        "ok": True,
        "removed": False,
        "rows_removed": 0,
    }


def test_forget_league_does_not_degrade_open(monkeypatch) -> None:
    """A failed delete must NOT look like a success.

    The read endpoints swallow everything and return an empty state. Here the commit
    happens as the session context manager exits — inside any such wrapper — so the
    same pattern would report a rolled-back delete as removed. It has to surface.
    """
    from fantabot.adapters.tokens import store as store_module

    class ExplodingStore:
        def __init__(self, session, cipher=None) -> None:
            pass

        def forget(self, league_id: int) -> bool:
            raise RuntimeError("database is down")

    _patch_session(monkeypatch)
    monkeypatch.setattr(store_module, "TokenStore", ExplodingStore)

    client = TestClient(app, raise_server_exceptions=False)
    assert client.delete("/api/v1/auth/league/4103937").status_code == 500


def test_forget_league_rejects_a_non_numeric_id() -> None:
    """The id is a required path parameter, so there is no silent "lega 0" default."""
    assert TestClient(app).delete("/api/v1/auth/league/all").status_code == 422


def test_forget_fantalab_removes_the_session(monkeypatch) -> None:
    from fantabot.adapters.persistence.repositories import tokens as repo_module

    removed: list[str] = []

    class FakeRepo:
        def __init__(self, session) -> None:
            pass

        def delete(self, user_id: str) -> bool:
            removed.append(user_id)
            return True

    _patch_session(monkeypatch)
    monkeypatch.setattr(repo_module, "FantalabSessionRepository", FakeRepo)

    response = TestClient(app).delete("/api/v1/auth/fantalab/user9")
    assert response.status_code == 200
    assert response.json() == {"ok": True, "removed": True}
    assert removed == ["user9"]


def test_forget_never_echoes_the_credential(monkeypatch) -> None:
    """The response carries a boolean and nothing else — no ciphertext, no fingerprint."""
    from fantabot.adapters.tokens import store as store_module

    class FakeStore:
        def __init__(self, session, cipher=None) -> None:
            pass

        def forget(self, league_id: int) -> bool:
            return True

    _patch_session(monkeypatch)
    monkeypatch.setattr(store_module, "TokenStore", FakeStore)

    body = TestClient(app).delete("/api/v1/auth/league/4103937").json()
    # Widened for the row count, deliberately: the invariant is that nothing about the
    # credential itself — ciphertext, fingerprint, expiry — reaches the response.
    assert set(body) == {"ok", "removed", "rows_removed"}
    assert isinstance(body["rows_removed"], int)


def test_the_job_log_names_the_gesture_the_web_user_actually_has(monkeypatch) -> None:
    """The use case states the condition; the interface supplies the gesture.

    The panel showed "Press Enter once you are logged in..." to someone looking at a
    browser with a Continue button and no terminal.
    """
    from fantabot.application import auth_login

    def fake_run(**kwargs):
        kwargs["prompt"]("once you are logged in and can see your leghe")
        return SimpleNamespace(ok=True)

    monkeypatch.setattr(auth_login, "run", fake_run)

    client = TestClient(app)
    job_id = client.post("/api/v1/auth/login").json()["job_id"]
    assert _wait(lambda: "Click Continue" in " ".join(_job(client, job_id)["lines"]))

    client.post(f"/api/v1/auth/login/{job_id}/confirm")
    assert _wait(lambda: _job(client, job_id)["status"] == "done")

    log = " ".join(_job(client, job_id)["lines"])
    assert "Press Enter" not in log
    assert "Click Continue once you are logged in and can see your leghe." in log


def test_forget_league_purges_the_lega_before_the_token(monkeypatch) -> None:
    """Order matters on failure: the token is the operator's only recovery handle.

    Purge first, token last — so a purge that raises leaves the row that lets them
    try again, rather than a lega with no data and no way back to it.
    """
    from fantabot.adapters.persistence.repositories import league as league_repo
    from fantabot.adapters.tokens import store as store_module

    order: list[str] = []

    class FakeLeagueRepository:
        def __init__(self, session) -> None:
            pass

        def purge(self, league_id: int) -> dict[str, int]:
            order.append("purge")
            return {"league_snapshot": 1}

    class FakeStore:
        def __init__(self, session, cipher=None) -> None:
            pass

        def forget(self, league_id: int) -> bool:
            order.append("forget")
            return True

    from fantabot.adapters.persistence import database_manager

    monkeypatch.setattr(database_manager, "get_session", _fake_get_session)
    monkeypatch.setattr(league_repo, "LeagueRepository", FakeLeagueRepository)
    monkeypatch.setattr(store_module, "TokenStore", FakeStore)

    TestClient(app).delete("/api/v1/auth/league/4103937")
    assert order == ["purge", "forget"]


def test_a_failed_purge_does_not_report_success(monkeypatch) -> None:
    """Both deletes share one commit, so a partial disconnect must surface as a 500."""
    from fantabot.adapters.persistence import database_manager
    from fantabot.adapters.persistence.repositories import league as league_repo

    class ExplodingLeagueRepository:
        def __init__(self, session) -> None:
            pass

        def purge(self, league_id: int) -> dict[str, int]:
            raise RuntimeError("database is down")

    monkeypatch.setattr(database_manager, "get_session", _fake_get_session)
    monkeypatch.setattr(league_repo, "LeagueRepository", ExplodingLeagueRepository)

    client = TestClient(app, raise_server_exceptions=False)
    assert client.delete("/api/v1/auth/league/4103937").status_code == 500
