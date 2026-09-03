"""F2 — opt-in integration test: really start the bundled Postgres.

Runs only under `pytest -m integration` (it opens sockets and spawns a real PG18). This
is the in-repo counterpart to the cross-OS cold-install CI (SPEC A1): it proves the real
`pixeltable_pgserver` path — start → create db → export URL → psycopg2 SELECT 1 → stop —
on the developer's own machine.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, text

from fantabot_app.provisioner.postgres import ENV_DATABASE_URL, PostgresProvisioner


@pytest.mark.integration
def test_real_provision_start_connect_stop(tmp_path) -> None:
    env: dict[str, str] = {}
    prov = PostgresProvisioner(pgdata=tmp_path / "pgdata", environ=env)
    try:
        url = prov.start()
        assert url.startswith("postgresql+psycopg2://")
        assert url.endswith("/fantabot")
        assert env[ENV_DATABASE_URL] == url
        assert prov.status()["running"] is True

        engine = create_engine(url)
        try:
            with engine.connect() as conn:
                assert conn.execute(text("SELECT 1")).scalar() == 1
        finally:
            engine.dispose()
    finally:
        prov.stop()
    assert prov.status()["running"] is False
