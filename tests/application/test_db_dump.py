"""Where a dump lands, and the two refusals it carries (T26).

All of this lived inside `db_dump`'s Typer body, where the app could not reach any of
it — and the app has to reach it, because the *path* is the deliverable. `SPEC.md` §8
Never #4 forbids a browser download of a dump: handing over the bytes puts the file
wherever the browser puts downloads, which is a directory neither guard covers. So the
app names the path instead, and naming a path it did not derive is how two surfaces come
to disagree about where the dump is.

**The refusal has to happen before anything runs.** A dump of this database is minutes
and ~2 GB; discovering at the end that it landed on the volume the dump exists to
survive the loss of is a discovery worth making first. `dump_target` is therefore a pure
function of a home and a date, and raises rather than returning something a caller has
to remember to check.

The clock is a parameter for `application/scrape.py`'s reason: a module that reads it has
tests that go red each midnight, and the filename is a date.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import IO

import pytest

from fantabot.application.db_dump import (
    DumpRefused,
    DumpWrote,
    PgDumpFailed,
    PgDumpMissing,
    dump_target,
    pg_dump_argv,
    run_dump,
)

SOCKET = "postgresql+psycopg2://postgres:@/fantabot?host=/Users/x/.fantabot/pgdata"
A_DAY = date(2026, 9, 20)


def test_the_dump_is_named_for_the_day_it_was_taken() -> None:
    """One file per day, in the home it was given — not in the working directory."""
    assert dump_target(Path("/Users/x"), A_DAY) == Path("/Users/x/fantabot-db-20260920.dump")


def test_a_home_on_an_external_volume_is_refused() -> None:
    """The guard the shell script carried, and the reason this is not a boolean.

    The dump exists to survive the loss of the volume the repository is on. Writing it
    there is not a worse dump, it is no dump at all — and it is also one `git add -A`
    from a commit with a remote to push the `league_tokens` rows to.
    """
    with pytest.raises(DumpRefused):
        dump_target(Path("/Volumes/External SSD/home"), A_DAY)


def test_the_refusal_names_the_path_it_refused() -> None:
    """`/Volumes/` is not a sentence an operator can act on; the whole path is."""
    with pytest.raises(DumpRefused) as refused:
        dump_target(Path("/Volumes/External SSD/home"), A_DAY)

    assert "/Volumes/External SSD/home/fantabot-db-20260920.dump" in str(refused.value)


def test_the_driver_suffix_is_stripped_for_libpq() -> None:
    """`postgresql+psycopg2://` is SQLAlchemy's spelling; libpq rejects it."""
    argv = pg_dump_argv(SOCKET)

    assert argv[0] == "pg_dump"
    assert argv[-1] == "postgresql://postgres:@/fantabot?host=/Users/x/.fantabot/pgdata"


def test_the_dump_stays_in_the_custom_format() -> None:
    """`-Fc` is what makes `pg_restore` selective; a plain SQL dump is not the artefact."""
    assert "-Fc" in pg_dump_argv(SOCKET)


def test_a_written_dump_reports_the_path_and_the_bytes_that_landed(tmp_path: Path) -> None:
    """The size is measured off the file, not counted by the caller.

    It is the only evidence available that `pg_dump` wrote anything: the process exits 0
    on an empty database too, and a 0-byte dump is the failure worth noticing.
    """
    def fake_pg_dump(argv: list[str], stdout: IO[bytes]) -> int:
        stdout.write(b"PGDMP" + b"x" * 95)
        return 0

    target = tmp_path / "fantabot-db-20260920.dump"
    wrote = run_dump(target, SOCKET, run=fake_pg_dump)

    assert wrote == DumpWrote(path=target, size_bytes=100)


def test_a_missing_pg_dump_is_its_own_outcome(tmp_path: Path) -> None:
    """Not on PATH and failed-while-running need different remedies, so different names."""
    def absent(argv: list[str], stdout: IO[bytes]) -> int:
        raise FileNotFoundError(2, "No such file or directory", "pg_dump")

    with pytest.raises(PgDumpMissing):
        run_dump(tmp_path / "d.dump", SOCKET, run=absent)


def test_a_non_zero_exit_is_its_own_outcome(tmp_path: Path) -> None:
    """`pg_dump` exiting 1 is the database being down, which is a thing to go and fix."""
    def fails(argv: list[str], stdout: IO[bytes]) -> int:
        return 1

    with pytest.raises(PgDumpFailed):
        run_dump(tmp_path / "d.dump", SOCKET, run=fails)
