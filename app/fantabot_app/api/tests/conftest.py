"""The one shared fixture: a `TestClient` over the real app.

**There is no database here, and there was never anything for one to do.** This file used
to build a SQLite engine, `create_all` a `DeclarativeBase` and override `get_db` — three
things that each did nothing. `Base` had no models, so `create_all` and `drop_all` built
and dropped zero tables; no route declared `Depends(get_db)`, so
`dependency_overrides[get_db]` overrode nothing. The scaffold it served
(`api/infrastructure/orm/`, `api/schemas/`) is deleted, and so, since 2026-09-24, is
`get_db` itself and the `api/infrastructure/database.py` that held it: the dependency no
route ever declared outlived the override by one deletion. Sessions come from fantabot's
`database_manager`, taken where they are used.

It was also the only `create_engine`/`sessionmaker` anywhere under `api/` — the exact
thing `tests/test_fitness.py::test_api_holds_no_second_sqlalchemy_engine` forbids, kept
green only by that test skipping `tests/` directories. The skip is now opt-out rather
than blanket, and this file is inside the scan.
"""

import time
from collections.abc import Callable, Generator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from fantabot_app.api.main import app


def redirect_home(monkeypatch: pytest.MonkeyPatch, path: Path | str) -> Path:
    """Point `Path.home()` at *path* on **every** platform.

    `Path.home()` is `os.path.expanduser("~")`, and the two implementations disagree about
    which variable says where home is: `posixpath` reads `HOME`, `ntpath` reads
    `USERPROFILE` and ignores `HOME` entirely. A test that sets only `HOME` therefore
    redirects nothing on Windows — the route reads the *real* home, finds no record, and
    the assertion fails with a number rather than an explanation.

    That was `test_the_route_reads_the_home_derived_record`, the last of app-ci's Windows
    failures. `test_db_dump_route` had already hit it and set both variables; the fix did
    not spread, which is the argument for it living here instead of at each call site.

    ⚠ Setting both is required, not belt-and-braces: neither variable alone covers both
    platforms, and which one is consulted is decided by the interpreter, not by us.
    """
    monkeypatch.setenv("HOME", str(path))
    monkeypatch.setenv("USERPROFILE", str(path))
    return Path(path)


def job(client: TestClient, job_id: str) -> dict[str, Any]:
    """`GET /jobs/{job_id}`, decoded.

    Byte-identical copies of this two-liner sat in **nine** modules until 2026-09-24. It
    is here rather than there for the reason `redirect_home` is: a copy is a thing that can
    drift, and the drift is invisible until a route changes shape.
    """
    return client.get(f"/api/v1/jobs/{job_id}").json()  # type: ignore[no-any-return]


def wait_for(predicate: Callable[[], bool], timeout: float = 10.0) -> bool:
    """Poll *predicate* until it holds or *timeout* elapses. `True` if it held.

    Eight copies, with the timeout drifted to 3.0, 5.0 and 10.0 seconds and the poll
    interval to 0.02 and 0.05 — none of it decided, all of it inherited by copy.

    **The ceiling is the highest of the three, and that direction is the safe one.** This
    returns the moment the predicate holds, so the timeout is not a condition for passing:
    it only bounds how long a *failure* takes to be reported. Raising 3.0 to 10.0 therefore
    cannot turn a red test green — it can only stop a slow machine reporting a job that was
    going to finish as one that never did, which is what app-ci's Windows runner is for.
    Lowering 10.0 to 3.0 would have been the change with a failure mode.

    The interval is the finer of the two for the mirror-image reason: 0.02 reaches the
    predicate sooner, so no caller's happy path got slower.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


def stub_child_command(monkeypatch: pytest.MonkeyPatch) -> None:
    """Point the supervisor at a short-lived real child that echoes its own argv.

    A **real** child, not a fake `Popen`, and that is the decision rather than the
    convenience: what these tests are about is what the operating system does with a pipe
    and a signal, and a fake agrees with whatever the implementation happened to do.

    Six `quick_child` fixtures each held this same `setattr` on 2026-09-24 and the line is
    the part that has to stay in sync — it decides what `_argv` can read back. What those
    fixtures do *around* it is deliberately theirs and stays there: `test_harvest*` set
    `FANTABOT_HARVEST_DIR`, `test_room_*` set `FANTABOT_DATA_DIR`, `test_db_*` call
    `redirect_home`. That is not drift; it is each route's own isolation, and one fixture
    setting all three would isolate a route against a variable it does not read and say
    nothing about the one it does.
    """
    import sys

    from fantabot_app.api.infrastructure import processes

    monkeypatch.setattr(
        processes,
        "fantabot_command",
        lambda *args: [sys.executable, "-c", f"print({' '.join(args)!r}, flush=True)"],
    )


@pytest.fixture
def client() -> Generator[TestClient, None, None]:
    with TestClient(app) as test_client:
        yield test_client
