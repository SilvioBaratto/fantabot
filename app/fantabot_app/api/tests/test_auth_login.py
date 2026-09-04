"""S3 — the headed connect-account login: job + prompt-gate + confirm.

No browser is launched and no socket opened: auth_login.run is faked with a stub that
uses the injected prompt (which blocks on the per-job gate) so the wiring — start job,
block on prompt, confirm releases it, job completes — is exercised deterministically.
"""

from __future__ import annotations

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


def test_login_blocks_on_prompt_then_confirm_completes(monkeypatch) -> None:
    from fantabot.application import auth_login

    def fake_run(**kwargs):
        kwargs["report"].print("Opening login")
        kwargs["prompt"]("Press Enter once logged in")  # blocks on the gate
        kwargs["report"].print("captured")
        return SimpleNamespace(ok=True)

    monkeypatch.setattr(auth_login, "run", fake_run)

    client = TestClient(app)
    job_id = client.post("/api/v1/auth/login").json()["job_id"]

    assert _wait(lambda: "Opening login" in " ".join(_job(client, job_id)["lines"]))
    assert _job(client, job_id)["status"] == "running"  # blocked on the prompt

    assert client.post(f"/api/v1/auth/login/{job_id}/confirm").status_code == 200

    assert _wait(lambda: _job(client, job_id)["status"] == "done")
    assert _job(client, job_id)["ok"] is True


def test_confirm_unknown_job_is_404() -> None:
    assert TestClient(app).post("/api/v1/auth/login/nope/confirm").status_code == 404


def test_fantalab_login_blocks_on_prompt_then_confirm_completes(monkeypatch) -> None:
    # The FantaLab connect route had no test at all: prove its job + gate + confirm wiring
    # deterministically, with fantalab_login.run faked so no headed browser / socket opens.
    from fantabot.application import fantalab_login

    def fake_run(**kwargs):
        kwargs["report"].print("Opening FantaLab")
        kwargs["prompt"]("Press Enter once logged in")  # blocks on the per-job gate
        kwargs["report"].print("session captured")
        return SimpleNamespace(ok=True)

    monkeypatch.setattr(fantalab_login, "run", fake_run)

    client = TestClient(app)
    job_id = client.post("/api/v1/auth/fantalab-login").json()["job_id"]

    assert _wait(lambda: "Opening FantaLab" in " ".join(_job(client, job_id)["lines"]))
    assert _job(client, job_id)["status"] == "running"  # blocked on the prompt

    # The confirm endpoint is shared with the lega login (same _login_gates dict).
    assert client.post(f"/api/v1/auth/login/{job_id}/confirm").status_code == 200

    assert _wait(lambda: _job(client, job_id)["status"] == "done")
    assert _job(client, job_id)["ok"] is True
