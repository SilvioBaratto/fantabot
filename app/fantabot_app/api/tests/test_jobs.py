"""F5 — the in-process job runner and its poll endpoint.

Jobs run synchronously in the tests via an injected thread factory, so state is
deterministic without threading races.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from fantabot_app.api.infrastructure.jobs import BufferingReporter, JobRegistry, registry
from fantabot_app.api.main import app


def _inline(run) -> None:
    """A thread factory that runs the job synchronously."""
    run()


def test_buffering_reporter_satisfies_the_reporter_protocol() -> None:
    reporter = BufferingReporter()
    reporter.print("[red]warn[/red]", "extra")
    assert reporter.lines == ["[red]warn[/red] extra"]


def test_registry_runs_a_job_and_captures_its_lines() -> None:
    reg = JobRegistry()

    def job(reporter: BufferingReporter):
        reporter.print("hello")
        reporter.print("world")
        return True

    job_id = reg.start(job, thread_factory=_inline)
    state = reg.get(job_id)
    assert state is not None
    assert state.status == "done"
    assert state.lines == ["hello", "world"]
    assert state.ok is True


def test_registry_marks_error_on_exception() -> None:
    reg = JobRegistry()

    def job(reporter: BufferingReporter):
        reporter.print("starting")
        raise ValueError("boom")

    job_id = reg.start(job, thread_factory=_inline)
    state = reg.get(job_id)
    assert state is not None
    assert state.status == "error"
    assert "boom" in (state.error or "")
    assert state.lines == ["starting"]  # partial progress is preserved


def test_registry_uses_a_result_ok_flag() -> None:
    reg = JobRegistry()

    class Result:
        ok = False

    job_id = reg.start(lambda reporter: Result(), thread_factory=_inline)
    assert reg.get(job_id).ok is False


def test_registry_runs_an_async_job() -> None:
    reg = JobRegistry()

    async def job(reporter: BufferingReporter):
        reporter.print("async line")
        return True

    job_id = reg.start(job, thread_factory=_inline)
    state = reg.get(job_id)
    assert state is not None
    assert state.status == "done"
    assert state.lines == ["async line"]


def test_registry_runs_a_sync_fn_that_returns_a_coroutine() -> None:
    # The distinct branch from the async-def case: a plain `def` job whose *return value*
    # is a coroutine (e.g. it forwards asyncio work). `iscoroutinefunction` is False, so
    # `_invoke` must detect the coroutine result and `asyncio.run` it to completion.
    reg = JobRegistry()

    async def _coro(reporter: BufferingReporter):
        reporter.print("from coroutine")
        return True

    def job(reporter: BufferingReporter):
        return _coro(reporter)  # returns a coroutine object, not an `async def` itself

    job_id = reg.start(job, thread_factory=_inline)
    state = reg.get(job_id)
    assert state is not None
    assert state.status == "done"
    assert state.lines == ["from coroutine"]
    assert state.ok is True


def test_jobs_endpoint_reports_status_and_404() -> None:
    job_id = registry.start(lambda reporter: reporter.print("done"), thread_factory=_inline)
    client = TestClient(app)

    response = client.get(f"/api/v1/jobs/{job_id}")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "done"
    assert "done" in body["lines"]

    assert client.get("/api/v1/jobs/unknown").status_code == 404
