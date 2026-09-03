"""An in-process job runner for the app's safe triggers (lega sync, news fetch, login).

Those use cases are long-ish, narrate as they run, and — for login — block on a headed
browser. So the API starts each on a background daemon thread and lets the UI poll
``GET /jobs/{id}``. State lives in memory: a job in flight is lost if the process
restarts, which is fine because every fantabot write is an upsert (re-run it). There is no
app-owned DB table (SPEC keeps the schema fantabot's).

The reporter is fantabot's ``Reporter`` protocol (a ``.print`` sink), so it drops straight
into ``lega_sync.collect(reporter=...)`` / ``auth_login.run(report=...)``; here it buffers
lines for the job log. The thread factory is injected so tests run jobs synchronously.
"""

from __future__ import annotations

import asyncio
import inspect
import threading
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any


class BufferingReporter:
    """A fantabot ``Reporter`` that appends each printed line to a buffer."""

    def __init__(self) -> None:
        self.lines: list[str] = []

    def print(self, *objects: Any, **kwargs: Any) -> None:
        self.lines.append(" ".join(str(obj) for obj in objects))


@dataclass
class JobState:
    id: str
    status: str = "running"  # running | done | error
    lines: list[str] = field(default_factory=list)
    ok: bool | None = None
    error: str | None = None


JobFn = Callable[[BufferingReporter], Any | Awaitable[Any]]
ThreadFactory = Callable[[Callable[[], None]], None]


def _spawn_daemon(run: Callable[[], None]) -> None:
    threading.Thread(target=run, daemon=True).start()


def _invoke(fn: JobFn, reporter: BufferingReporter) -> Any:
    """Call ``fn``; run it to completion with ``asyncio.run`` if it is async."""
    if inspect.iscoroutinefunction(fn):
        return asyncio.run(fn(reporter))
    result = fn(reporter)
    if inspect.iscoroutine(result):
        return asyncio.run(result)
    return result


def _result_ok(result: Any) -> bool:
    """A SyncResult-style ``.ok`` wins; a bool return is taken as-is; else success."""
    ok = getattr(result, "ok", None)
    if ok is not None:
        return bool(ok)
    if isinstance(result, bool):
        return result
    return True


class JobRegistry:
    """Thread-safe registry of running/finished jobs."""

    def __init__(self) -> None:
        self._jobs: dict[str, JobState] = {}
        self._lock = threading.Lock()

    def start(
        self,
        fn: JobFn,
        *,
        id_factory: Callable[[], str] = lambda: uuid.uuid4().hex,
        thread_factory: ThreadFactory = _spawn_daemon,
    ) -> str:
        job_id = id_factory()
        reporter = BufferingReporter()
        # lines is the reporter's own buffer, so /jobs/{id} sees progress live.
        state = JobState(id=job_id, lines=reporter.lines)
        with self._lock:
            self._jobs[job_id] = state

        def run() -> None:
            try:
                result = _invoke(fn, reporter)
                state.ok = _result_ok(result)
                state.status = "done"
            except Exception as exc:  # noqa: BLE001 — a job failure must not crash the server
                state.status = "error"
                state.error = f"{type(exc).__name__}: {exc}"

        thread_factory(run)
        return job_id

    def get(self, job_id: str) -> JobState | None:
        with self._lock:
            return self._jobs.get(job_id)


# Process-wide registry the endpoints and actions share.
registry = JobRegistry()
