"""The one shared fixture: a `TestClient` over the real app.

**There is no database here, and there was never anything for one to do.** This file used
to build a SQLite engine, `create_all` a `DeclarativeBase` and override `get_db` — three
things that each did nothing. `Base` had no models, so `create_all` and `drop_all` built
and dropped zero tables; no route declares `Depends(get_db)`, so
`dependency_overrides[get_db]` overrode nothing. The scaffold it served
(`api/infrastructure/orm/`, `api/schemas/`) is deleted.

It was also the only `create_engine`/`sessionmaker` anywhere under `api/` — the exact
thing `tests/test_fitness.py::test_api_holds_no_second_sqlalchemy_engine` forbids, kept
green only by that test skipping `tests/` directories. The skip is now opt-out rather
than blanket, and this file is inside the scan.
"""

from collections.abc import Generator
from pathlib import Path

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


@pytest.fixture
def client() -> Generator[TestClient, None, None]:
    with TestClient(app) as test_client:
        yield test_client
