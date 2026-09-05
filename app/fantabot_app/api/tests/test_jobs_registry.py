"""§3.1 — the registry learns to list, to page, to stop honestly, and to forget.

Four defects, one module. `_jobs` grew for ever and so did `_login_gates` beside it, which
holds a `threading.Event` per login. `GET /jobs/{id}` returned the whole log every 1.5 s,
so polling a job cost O(n) in its own output — a lega sync's 40 lines re-sent 27 times. A
running job could not be listed, so a page refresh orphaned it invisibly. And nothing
could be stopped.

The stop is the interesting one, because the honest answer today is *no*. Every job here
is a daemon thread and a thread cannot be interrupted from outside; a control that
pretended otherwise would be worse than no control. So `JobState` carries an optional
`stop` callable, and the endpoint answers 409 with a reason when a job has none. That is
what lets this land before the supervisor that will set one exists.
"""

from __future__ import annotations

import threading

import pytest
from fastapi.testclient import TestClient

from fantabot_app.api.infrastructure.jobs import JobRegistry


def _inline(run) -> None:
    run()


def _never(run) -> None:
    """A thread factory that does not run the job — it stays `running` for ever."""


class TestListing:
    def test_it_lists_what_a_job_is_without_its_lines(self) -> None:
        """The list is polled; the log is not. Sending 2,000 lines per job to render a
        table of three rows is the same defect `?since=` fixes one job at a time."""
        reg = JobRegistry()
        reg.start(lambda r: r.print("a line"), kind="lega-sync", thread_factory=_inline)

        (entry,) = reg.list()

        assert entry.kind == "lega-sync"
        assert entry.status == "done"
        assert entry.line_count == 1
        assert entry.started_at is not None
        assert not hasattr(entry, "lines")

    def test_a_job_started_without_a_kind_still_lists(self) -> None:
        reg = JobRegistry()
        reg.start(lambda r: None, thread_factory=_inline)

        assert reg.list()[0].kind == "job"

    def test_the_newest_job_is_first(self) -> None:
        reg = JobRegistry()
        first = reg.start(lambda r: None, kind="one", thread_factory=_inline)
        second = reg.start(lambda r: None, kind="two", thread_factory=_inline)

        assert [e.id for e in reg.list()] == [second, first]


class TestSince:
    def test_it_returns_only_what_is_new_and_where_to_resume(self) -> None:
        reg = JobRegistry()
        job_id = reg.start(
            lambda r: [r.print("one"), r.print("two"), r.print("three")],
            thread_factory=_inline,
        )

        lines, next_index = reg.lines_since(job_id, 2)

        assert lines == ["three"]
        assert next_index == 3

    def test_a_caught_up_poller_is_told_nothing_happened(self) -> None:
        reg = JobRegistry()
        job_id = reg.start(lambda r: r.print("only"), thread_factory=_inline)

        assert reg.lines_since(job_id, 1) == ([], 1)

    def test_an_index_past_the_end_does_not_go_backwards(self) -> None:
        """A restarted registry, or a client that kept an index across a job it no longer
        has. Returning the whole log again would double every line it already showed."""
        reg = JobRegistry()
        job_id = reg.start(lambda r: r.print("only"), thread_factory=_inline)

        assert reg.lines_since(job_id, 99) == ([], 1)

    def test_a_negative_index_is_read_as_the_beginning(self) -> None:
        reg = JobRegistry()
        job_id = reg.start(lambda r: r.print("only"), thread_factory=_inline)

        assert reg.lines_since(job_id, -5) == (["only"], 1)


class TestStop:
    def test_a_job_with_no_stop_says_so(self) -> None:
        reg = JobRegistry()
        job_id = reg.start(lambda r: None, thread_factory=_inline)

        assert reg.stop(job_id) is False

    def test_a_job_with_a_stop_has_it_called(self) -> None:
        reg = JobRegistry()
        called: list[str] = []
        job_id = reg.start(
            lambda r: None, stop=lambda: called.append("stopped"), thread_factory=_never
        )

        assert reg.stop(job_id) is True
        assert called == ["stopped"]

    def test_stopping_an_unknown_job_is_not_a_silent_success(self) -> None:
        reg = JobRegistry()

        with pytest.raises(KeyError):
            reg.stop("nope")


class TestEviction:
    def test_finished_jobs_are_evicted_once_the_bound_is_passed(self) -> None:
        """The bound is driven, not asserted: a test that reads `MAX_FINISHED` back proves
        the constant equals itself and would pass with eviction deleted."""
        reg = JobRegistry(max_finished=3)

        ids = [reg.start(lambda r: None, thread_factory=_inline) for _ in range(5)]

        kept = {e.id for e in reg.list()}
        assert len(kept) == 3
        assert kept == set(ids[-3:]), "the oldest finished jobs go first"

    def test_a_running_job_is_never_evicted(self) -> None:
        reg = JobRegistry(max_finished=1)
        running = reg.start(lambda r: None, kind="collect", thread_factory=_never)

        for _ in range(5):
            reg.start(lambda r: None, thread_factory=_inline)

        assert reg.get(running) is not None
        assert reg.get(running).status == "running"

    def test_an_old_finished_job_is_evicted_even_below_the_count_bound(self) -> None:
        clock = [1000.0]
        reg = JobRegistry(max_finished=100, max_age_s=60.0, clock=lambda: clock[0])
        old = reg.start(lambda r: None, thread_factory=_inline)

        clock[0] += 61.0
        recent = reg.start(lambda r: None, thread_factory=_inline)

        assert reg.get(old) is None
        assert reg.get(recent) is not None

    def test_eviction_takes_the_registered_companion_with_it(self) -> None:
        """`_login_gates` is a `threading.Event` per login job, in a module beside this
        one. Evicting the job and leaving the gate is how the leak comes back wearing a
        different name."""
        reg = JobRegistry(max_finished=1)
        gates: dict[str, threading.Event] = {}

        first = reg.start(lambda r: None, thread_factory=_inline)
        gates[first] = threading.Event()
        reg.on_evict(lambda job_id: gates.pop(job_id, None))
        for _ in range(3):
            reg.start(lambda r: None, thread_factory=_inline)

        assert first not in gates


class TestEndpoints:
    @pytest.fixture
    def client(self) -> TestClient:
        from fantabot_app.api.main import app

        return TestClient(app)

    def test_the_list_endpoint_carries_no_lines(self, client: TestClient) -> None:
        from fantabot_app.api.infrastructure.jobs import registry

        registry.start(lambda r: r.print("secret-ish"), kind="lega-sync", thread_factory=_inline)

        body = client.get("/api/v1/jobs").json()

        assert body["jobs"], "a job was started; the list must show it"
        assert all("lines" not in job for job in body["jobs"])
        assert any(job["kind"] == "lega-sync" for job in body["jobs"])

    def test_since_pages_the_log(self, client: TestClient) -> None:
        from fantabot_app.api.infrastructure.jobs import registry

        job_id = registry.start(
            lambda r: [r.print("one"), r.print("two")], thread_factory=_inline
        )

        body = client.get(f"/api/v1/jobs/{job_id}?since=1").json()

        assert body["lines"] == ["two"]
        assert body["next_index"] == 2

    def test_without_since_the_whole_log_still_comes_back(self, client: TestClient) -> None:
        from fantabot_app.api.infrastructure.jobs import registry

        job_id = registry.start(
            lambda r: [r.print("one"), r.print("two")], thread_factory=_inline
        )

        body = client.get(f"/api/v1/jobs/{job_id}").json()

        assert body["lines"] == ["one", "two"]
        assert body["next_index"] == 2

    def test_stopping_a_thread_job_is_a_409_that_explains_itself(
        self, client: TestClient
    ) -> None:
        from fantabot_app.api.infrastructure.jobs import registry

        job_id = registry.start(lambda r: None, thread_factory=_inline)

        response = client.post(f"/api/v1/jobs/{job_id}/stop")

        assert response.status_code == 409
        assert response.json()["detail"] == "this job cannot be stopped"

    def test_stopping_an_unknown_job_is_a_404(self, client: TestClient) -> None:
        assert client.post("/api/v1/jobs/nope/stop").status_code == 404
