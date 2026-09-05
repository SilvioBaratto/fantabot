"""`harvest load` tells three database failures apart, because only one is an outage.

`SQLAlchemyError` was caught whole and reported as `database unreachable: <name>`. Found
the hard way on 2026-09-05: a `UniqueViolation` on `asta.key` — a stranded identity
sequence, nothing to do with reachability — was reported as an outage, and under
`--follow` an outage is retried. The next pass re-reads the same window and hits the same
constraint, so the loader retries for ever behind a message naming the wrong problem.
That is survivable at a terminal, where someone reads it. It is not survivable under the
supervisor, which is what runs this command next.

So the three cases are separated:

* **`OperationalError` is the outage.** Today's text, today's retry.
* **`IntegrityError` is ours.** It names the constraint and exits non-zero, in *both*
  modes: another pass cannot fix it, and the point of `--follow` is to wait for something
  that changes.
* **Anything else `SQLAlchemyError` is reported as itself**, because calling it either of
  the other two is how this defect was built in the first place.

The checkpoint does not move in any of the three, which is the invariant
`tests/application/test_aste_outage.py` states and which nothing here may weaken.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.exc import IntegrityError, OperationalError, ProgrammingError, SQLAlchemyError
from typer.testing import CliRunner

from fantabot.interface.app import app

runner = CliRunner()
ANSI = re.compile(r"\x1b\[[0-9;]*m")


def _plain(output: str) -> str:
    """Rich wraps at the terminal width; a message assertion must not depend on where."""
    return ANSI.sub("", output).replace("\n", "")


def _landing(home: Path) -> Path:
    """A seed of one auction and a landing zone holding one state for it."""
    (home / "seed.json").write_text(
        json.dumps([["a1", "1", 8, 500, 2, 28, "asta", "rilancio", 30, 60, "Alpha", "mantra"]]),
        encoding="utf-8",
    )
    landing = home / "live.jsonl"
    landing.write_text(
        json.dumps(
            {
                "seen_at": "2026-09-05T18:00:00+00:00",
                "auction_id": "a1",
                "state": {"asta": {"id": "p1", "prezzo": 10}},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    return landing


def _integrity_error() -> IntegrityError:
    """What psycopg2 raises through SQLAlchemy, with the diagnostic the message needs."""

    class _Diag:
        constraint_name = "asta_key_key"

    class _Orig(Exception):
        diag = _Diag()

    return IntegrityError("INSERT INTO asta ...", {}, _Orig("duplicate key value"))


@pytest.fixture
def home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    monkeypatch.setenv("FANTABOT_HARVEST_DIR", str(tmp_path))
    return tmp_path


def _repo_raising(exc: BaseException) -> type:
    class _Repo:
        def __init__(self, session: Any) -> None:
            pass

        def known_player_ids(self) -> frozenset[int]:
            return frozenset()

        def upsert_auctions(self, rows: Any) -> None:
            raise exc

    return _Repo


@pytest.fixture
def failing_write(monkeypatch: pytest.MonkeyPatch):
    """Point the loader's write phase at a repository that raises whatever is asked."""
    from contextlib import contextmanager

    from fantabot.adapters.persistence import database_manager
    from fantabot.adapters.persistence.repositories import aste as aste_repo

    @contextmanager
    def fake_session():
        yield None

    monkeypatch.setattr(database_manager, "get_session", fake_session)

    def install(exc: BaseException) -> None:
        monkeypatch.setattr(aste_repo, "AsteRepository", _repo_raising(exc))

    return install


def _run(*extra: str):
    return runner.invoke(app, ["harvest", "load", *extra])


def test_an_operational_error_keeps_todays_outage_text(home, failing_write) -> None:
    _landing(home)
    failing_write(OperationalError("SELECT 1", {}, Exception("could not connect")))

    result = _run()

    plain = _plain(result.output)
    assert result.exit_code == 1
    assert "database unreachable: OperationalError" in plain
    assert "Collection is unaffected" in plain


def test_an_integrity_error_names_the_constraint_and_is_not_an_outage(home, failing_write) -> None:
    _landing(home)
    failing_write(_integrity_error())

    result = _run()

    plain = _plain(result.output)
    assert result.exit_code == 1
    assert "asta_key_key" in plain
    assert "unreachable" not in plain


def test_an_integrity_error_does_not_retry_under_follow(home, failing_write, monkeypatch) -> None:
    """`--follow` waits for something that changes. The next pass re-reads the same
    window and hits the same constraint, so waiting is the one thing that cannot help.

    The sleep is booby-trapped rather than counted: before this split the command
    retried for ever here, and a test that merely asserted the exit code would have
    hung the suite instead of failing it.
    """
    _landing(home)
    failing_write(_integrity_error())

    def never(seconds: float) -> None:
        raise AssertionError("the loader slept — it is retrying a constraint violation")

    monkeypatch.setattr("time.sleep", never)

    result = _run("--follow", "--interval", "0.01")

    assert result.exit_code != 0
    assert not isinstance(result.exception, AssertionError), result.exception
    assert "asta_key_key" in _plain(result.output)


def test_an_operational_error_still_retries_under_follow(home, failing_write, monkeypatch) -> None:
    """The outage path is unchanged: it sleeps and comes back, for ever if it must."""
    _landing(home)
    failing_write(OperationalError("SELECT 1", {}, Exception("could not connect")))

    slept: list[float] = []

    def stop_after_two(seconds: float) -> None:
        slept.append(seconds)
        if len(slept) >= 2:
            raise KeyboardInterrupt

    monkeypatch.setattr("time.sleep", stop_after_two)

    result = _run("--follow", "--interval", "0.01")

    assert len(slept) >= 2, "the outage path stopped retrying under --follow"
    assert "database unreachable" in _plain(result.output)
    # CliRunner turns the interrupt into the shell's own 130; the point is that it
    # was still looping when the test stopped it, not how the runner reported that.
    assert result.exit_code == 130


def test_any_other_sqlalchemy_error_is_reported_as_itself(home, failing_write) -> None:
    """Not an outage and not a constraint. Calling it either is how this defect began."""
    _landing(home)
    failing_write(ProgrammingError("SELECT nope", {}, Exception('column "nope" does not exist')))

    plain = _plain(_run().output)

    assert "ProgrammingError" in plain
    assert "unreachable" not in plain
    assert "constraint" not in plain.lower()


@pytest.mark.parametrize(
    "exc",
    [
        OperationalError("SELECT 1", {}, Exception("could not connect")),
        _integrity_error(),
        ProgrammingError("SELECT nope", {}, Exception("boom")),
    ],
    ids=["operational", "integrity", "other"],
)
def test_the_checkpoint_never_moves_whichever_failure_it_was(home, failing_write, exc) -> None:
    """The invariant `test_aste_outage.py` states, held across all three branches.

    A checkpoint that advanced past an unwritten window would skip it for ever, and the
    landing zone's guarantee — an outage costs catch-up time, never a record — would be
    worth nothing.
    """
    from fantabot.application.harvest_loader import Checkpoint

    landing = _landing(home)
    failing_write(exc)

    _run()

    assert Checkpoint(landing).read() == 0


def test_the_three_branches_are_disjoint_types() -> None:
    """`IntegrityError` and `OperationalError` are siblings, not one inside the other.

    Written down because the ordering of the `except` clauses would be load-bearing if
    they were not, and a future reader must not have to re-derive that from sqlalchemy.
    """
    assert not issubclass(IntegrityError, OperationalError)
    assert not issubclass(OperationalError, IntegrityError)
    assert issubclass(IntegrityError, SQLAlchemyError)
    assert issubclass(OperationalError, SQLAlchemyError)
