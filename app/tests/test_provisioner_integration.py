"""F2 — opt-in integration test: really start the bundled Postgres.

Runs only under `pytest -m integration` (it opens sockets and spawns a real PG18). This
is the in-repo counterpart to the cross-OS cold-install CI (SPEC A1): it proves the real
`pixeltable_pgserver` path — start → create db → export URL → psycopg2 SELECT 1 → stop.

**The data directory is not `tmp_path`, and that is the whole point of this file's
setup.** `app-ci`'s Windows job failed here on its first ever run — the backend step had
always failed before it, so this step had never executed on Windows at all:

    initdb: error: could not create directory
      "C:/Users/runneradmin/AppData/Local/Temp/pytest-of-runneradmin": File exists

Read the two halves and it is not mysterious. pytest creates its base directory with
`rootdir.mkdir(mode=0o700)` (`_pytest/tmpdir.py:168`), which on Windows is an owner-only
ACL; and PostgreSQL re-executes `initdb` under a **restricted process token** on Windows,
which cannot traverse it. `pg_mkdir_p` then gets "does not exist" from `stat` and "already
exists" from `mkdir` on the same path, which is exactly the contradiction the message
reports.

**So this was never a product defect, and the fix is not to work around one.** The app's
real data directory is `~/.fantabot/pgdata` (`fantabot_app/paths.py:22`), created by
`PostgresProvisioner.start` with a plain `mkdir(parents=True, exist_ok=True)` — default,
inherited ACLs, nothing hardened. A real Windows user was never on the failing path; only
this test was, because only this test put pgdata somewhere pytest had locked down.

Using a home-relative directory therefore does two things at once: it escapes the
hardened tree, and it exercises the same *shape* of path the product actually uses.
`tempfile.mkdtemp()` would not do — it also creates at 0o700.
"""

from __future__ import annotations

import os
import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from fantabot_app.provisioner.postgres import ENV_DATABASE_URL, PostgresProvisioner


@pytest.fixture
def unhardened_pgdata() -> Iterator[Path]:
    """A data directory with the permissions a real install would have.

    Under `~`, like the product's own, and named for the pid so two runs on one machine
    cannot collide. Removed in a `finally`: this spawns a real PG18 and a leaked data
    directory is ~40 MB.
    """
    root = Path.home() / f".fantabot-itest-{os.getpid()}"
    try:
        root.mkdir(parents=True, exist_ok=True)
        yield root / "pgdata"
    finally:
        shutil.rmtree(root, ignore_errors=True)


@pytest.mark.integration
def test_real_provision_start_connect_stop(unhardened_pgdata: Path) -> None:
    env: dict[str, str] = {}
    prov = PostgresProvisioner(pgdata=unhardened_pgdata, environ=env)
    try:
        url = prov.start()
        assert url.startswith("postgresql+psycopg2://")
        # Not `endswith("/fantabot")`: on macOS/Linux this is the socket form, and the
        # socket directory lands *after* the database name — under $TMPDIR rather than in
        # pgdata whenever the pgdata path is too long for sun_path.
        assert make_url(url).database == "fantabot"
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
