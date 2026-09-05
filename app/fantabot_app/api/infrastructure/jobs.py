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
import builtins
import inspect
import re
import threading
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

#: How many *finished* jobs are kept, and for how long. Both bounds, not one: a quiet week
#: would keep a failed sync's log for ever under a count bound alone, and a busy evening
#: would blow past a count under an age bound alone. Neither ever touches a running job.
MAX_FINISHED = 50
MAX_AGE_S = 3600.0

#: Rich console markup: ``[green]``, ``[/green]``, ``[bold]``, ``[yellow]`` and friends.
#: Deliberately anchored to a closing ``]`` with no nested bracket, so a line that
#: legitimately contains ``[2, 23]`` (a roster band) or ``[1, 2]`` survives intact.
_RICH_TAG = re.compile(r"\[/?[a-z][a-z0-9 _.#-]*\]")


class BufferingReporter:
    """A fantabot ``Reporter`` that appends each printed line to a buffer.

    The use cases it wraps write for a Rich console, so their lines carry markup:
    ``Encryption key: [green]ok[/green]``. A terminal renders that as colour; a
    browser renders it as literal square brackets. The markup is stripped here — at
    the one seam between a Rich-speaking producer and a non-Rich consumer — rather
    than in the use cases, which are shared with the CLI and are right as they are.
    """

    def __init__(self) -> None:
        self.lines: list[str] = []
        #: True while the job is parked waiting for the human to confirm. The UI needs
        #: this to know when to re-enable its button: a login may ask more than once,
        #: because confirming before the browser has written the credential is a normal
        #: mistake rather than a failure.
        self.awaiting_confirm = False

    def print(self, *objects: Any, **kwargs: Any) -> None:
        self.lines.append(_RICH_TAG.sub("", " ".join(str(obj) for obj in objects)))


@dataclass
class JobState:
    id: str
    status: str = "running"  # running | done | error
    lines: list[str] = field(default_factory=list)
    ok: bool | None = None
    error: str | None = None
    #: The live reporter, held so `awaiting_confirm` reads current state rather than a
    #: copy — the same reason `lines` is the reporter's own list and not a snapshot.
    reporter: BufferingReporter | None = None
    #: What this job is, for the listing: "lega-sync", "news-fetch", "auth-login".
    kind: str = "job"
    #: Monotonic, and paired with a wall-clock stamp: the age bound must not move when the
    #: system clock does, and the UI cannot render a monotonic number.
    started_monotonic: float = 0.0
    started_at: str = ""
    #: How this job is asked to stop, when it can be. `None` is the honest answer for every
    #: job today: they are daemon threads, and a thread cannot be interrupted from outside.
    #: The endpoint answers 409 rather than pretending — see `JobRegistry.stop`.
    stop: Callable[[], None] | None = None

    @property
    def awaiting_confirm(self) -> bool:
        return self.reporter is not None and self.reporter.awaiting_confirm

    @property
    def finished(self) -> bool:
        return self.status != "running"


@dataclass(frozen=True)
class JobSummary:
    """One row of `GET /jobs` — deliberately without `lines`.

    The list is polled to render a table of a few rows; sending every job's whole log to
    draw it is the same defect `?since=` fixes one job at a time.
    """

    id: str
    kind: str
    status: str
    started_at: str
    line_count: int
    ok: bool | None
    stoppable: bool


JobFn = Callable[[BufferingReporter], Any | Awaitable[Any]]
ThreadFactory = Callable[[Callable[[], None]], None]


def _now_iso() -> str:
    """Wall-clock start, for the UI. The *bounds* use a monotonic reading beside it."""
    return datetime.now(UTC).isoformat()


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
    """Thread-safe registry of running/finished jobs, bounded in both count and age."""

    def __init__(
        self,
        *,
        max_finished: int = MAX_FINISHED,
        max_age_s: float = MAX_AGE_S,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._jobs: dict[str, JobState] = {}
        self._lock = threading.Lock()
        self._max_finished = max_finished
        self._max_age_s = max_age_s
        self._clock = clock
        #: Called with a job id as it is evicted. `_login_gates` registers here: it holds a
        #: `threading.Event` per login job in a module beside this one, and evicting the job
        #: while leaving the gate is how the leak comes back wearing a different name.
        self._on_evict: list[Callable[[str], Any]] = []

    def on_evict(self, callback: Callable[[str], Any]) -> None:
        """Register a companion to forget alongside an evicted job."""
        with self._lock:
            self._on_evict.append(callback)

    def _sweep(self) -> None:
        """Evict, then tell the companions — the callbacks run outside the lock.

        Run both when a job *starts* and when one *finishes*. Starting alone is not
        enough: a job is still running while its own start sweeps, so the registry would
        settle at the bound plus whatever finished after the last start.
        """
        with self._lock:
            evicted = self._evict()
            callbacks = list(self._on_evict)
        for job_id in evicted:
            for callback in callbacks:
                callback(job_id)

    def _evict(self) -> list[str]:
        """Drop finished jobs past either bound. Caller holds the lock; returns the ids.

        A running job is never a candidate, whatever its age: eviction is about the memory
        a *record* costs, and a job still writing into its own buffer is not a record yet.
        """
        finished = [s for s in self._jobs.values() if s.finished]
        now = self._clock()
        stale = {s.id for s in finished if now - s.started_monotonic > self._max_age_s}
        keep = [s for s in finished if s.id not in stale]
        overflow = {s.id for s in sorted(keep, key=lambda s: s.started_monotonic)[
            : max(0, len(keep) - self._max_finished)
        ]}
        for job_id in stale | overflow:
            del self._jobs[job_id]
        return sorted(stale | overflow)

    def start(
        self,
        fn: JobFn,
        *,
        kind: str = "job",
        stop: Callable[[], None] | None = None,
        id_factory: Callable[[], str] = lambda: uuid.uuid4().hex,
        thread_factory: ThreadFactory = _spawn_daemon,
    ) -> str:
        job_id = id_factory()
        reporter = BufferingReporter()
        # lines is the reporter's own buffer, so /jobs/{id} sees progress live.
        state = JobState(
            id=job_id,
            lines=reporter.lines,
            reporter=reporter,
            kind=kind,
            stop=stop,
            started_monotonic=self._clock(),
            started_at=_now_iso(),
        )
        with self._lock:
            self._jobs[job_id] = state
        self._sweep()

        def run() -> None:
            try:
                result = _invoke(fn, reporter)
                state.ok = _result_ok(result)
                state.status = "done"
            except Exception as exc:  # noqa: BLE001 — a job failure must not crash the server
                state.status = "error"
                state.error = f"{type(exc).__name__}: {exc}"
            finally:
                self._sweep()

        thread_factory(run)
        return job_id

    def get(self, job_id: str) -> JobState | None:
        with self._lock:
            return self._jobs.get(job_id)

    def list(self) -> builtins.list[JobSummary]:
        """Every job, newest first, without a single log line.

        `builtins.list` in the annotation because the method shadows the builtin inside
        this class body, and `list[JobSummary]` there names *this method*.
        """
        with self._lock:
            states = sorted(
                self._jobs.values(), key=lambda s: s.started_monotonic, reverse=True
            )
            return [
                JobSummary(
                    id=s.id,
                    kind=s.kind,
                    status=s.status,
                    started_at=s.started_at,
                    line_count=len(s.lines),
                    ok=s.ok,
                    stoppable=s.stop is not None,
                )
                for s in states
            ]

    def lines_since(self, job_id: str, since: int) -> tuple[builtins.list[str], int]:
        """The lines from `since` onward, and the index to ask for next time.

        An index past the end returns nothing and the *real* end, never the whole log
        again: a client that kept an index across a restart would otherwise be handed
        every line it has already shown, as if it were new.
        """
        state = self.get(job_id)
        if state is None:
            raise KeyError(job_id)
        lines = list(state.lines)
        start = min(max(since, 0), len(lines))
        return lines[start:], len(lines)

    def stop(self, job_id: str) -> bool:
        """Ask a job to stop. `False` means it has no way to be stopped — not that it failed.

        Every job here is a daemon thread and a thread cannot be interrupted from outside,
        so `False` is the honest answer for all of them today. The caller turns it into a
        409 with a reason; a control that pretended otherwise would be worse than none.
        """
        state = self.get(job_id)
        if state is None:
            raise KeyError(job_id)
        if state.stop is None:
            return False
        state.stop()
        return True


# Process-wide registry the endpoints and actions share.
registry = JobRegistry()
