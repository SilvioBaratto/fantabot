"""Regressions for the job registry's age bound and the job endpoint's read ordering."""

from __future__ import annotations

from fastapi.testclient import TestClient

from fantabot_app.api.infrastructure.jobs import JobRegistry, registry
from fantabot_app.api.main import app


def _never(run) -> None:
    """A thread factory that does not run the job."""


def test_a_long_job_survives_its_own_finish_sweep() -> None:
    clock = [1000.0]
    reg = JobRegistry(max_finished=100, max_age_s=60.0, clock=lambda: clock[0])
    captured: list = []
    job_id = reg.start(lambda r: None, thread_factory=captured.append)

    clock[0] += 3600.0
    captured[0]()

    assert reg.get(job_id) is not None, "age runs from the finish, not the start"

    clock[0] += 61.0
    reg.start(lambda r: None, thread_factory=_never)
    assert reg.get(job_id) is None


def test_status_is_read_before_lines_so_a_terminal_status_never_hides_the_tail(
    monkeypatch,
) -> None:
    job_id = registry.start(lambda r: None, thread_factory=_never)
    state = registry.get(job_id)
    original = registry.lines_since

    def lines_then_finish(jid: str, since: int):
        result = original(jid, since)
        state.lines.append("tail")
        state.status = "done"
        return result

    monkeypatch.setattr(registry, "lines_since", lines_then_finish)

    body = TestClient(app).get(f"/api/v1/jobs/{job_id}").json()

    assert not (body["status"] != "running" and "tail" not in body["lines"])


def test_a_job_evicted_mid_read_is_a_404_not_a_500(monkeypatch) -> None:
    job_id = registry.start(lambda r: None, thread_factory=_never)

    def gone(jid: str, since: int):
        raise KeyError(jid)

    monkeypatch.setattr(registry, "lines_since", gone)

    response = TestClient(app, raise_server_exceptions=False).get(f"/api/v1/jobs/{job_id}")

    assert response.status_code == 404
