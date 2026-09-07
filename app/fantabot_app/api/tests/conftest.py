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

import pytest
from fastapi.testclient import TestClient

from fantabot_app.api.main import app


@pytest.fixture
def client() -> Generator[TestClient, None, None]:
    with TestClient(app) as test_client:
        yield test_client
