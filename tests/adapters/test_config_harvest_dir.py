"""The harvest artefacts have one home, and it is derived rather than relative.

`live.jsonl`, its `.offset`, its `.state`, the seed and the listone bridge sat under
`./data/aste_live/` — reachable only from the repository root. The app's working
directory is wherever its launcher was started, so a collector started from the app and
a `harvest load` typed in a terminal addressed two different landing zones. This is the
same argument that made `bundled_database_url` derive from `bundled_pgdata()`
(root `CLAUDE.md`, "One database, and it is the app's"), applied to the files instead of
the database.

Two properties, and both are why this is a function rather than a constant:

* **Computed at call time.** `Path.home()` read at import binds the home of whichever
  process imported first; a test that repoints `HOME` — and the launcher, which may run
  under a different one — must see the new path.
* **An export still wins.** `FANTABOT_HARVEST_DIR` is how an operator whose home volume
  cannot hold 1.4 GB of landing zone keeps the artefacts where they are.
"""

from __future__ import annotations

import pathlib
from pathlib import Path

import pytest

from fantabot.config import harvest_dir


@pytest.fixture(autouse=True)
def _no_ambient_dir(monkeypatch: pytest.MonkeyPatch) -> None:
    """An exported FANTABOT_HARVEST_DIR would decide every assertion here."""
    monkeypatch.delenv("FANTABOT_HARVEST_DIR", raising=False)


@pytest.fixture
def home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    monkeypatch.setattr(pathlib.Path, "home", classmethod(lambda _cls: tmp_path))
    return tmp_path


def test_the_default_is_under_the_dot_fantabot_home(home: Path) -> None:
    assert harvest_dir() == home / ".fantabot" / "aste_live"


def test_it_is_computed_at_call_time_so_a_repointed_home_is_seen(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    first, second = tmp_path / "one", tmp_path / "two"

    monkeypatch.setattr(pathlib.Path, "home", classmethod(lambda _cls: first))
    assert harvest_dir() == first / ".fantabot" / "aste_live"

    monkeypatch.setattr(pathlib.Path, "home", classmethod(lambda _cls: second))
    assert harvest_dir() == second / ".fantabot" / "aste_live"


def test_an_exported_directory_wins(
    monkeypatch: pytest.MonkeyPatch, home: Path, tmp_path: Path
) -> None:
    elsewhere = tmp_path / "external" / "aste_live"
    monkeypatch.setenv("FANTABOT_HARVEST_DIR", str(elsewhere))

    assert harvest_dir() == elsewhere


def test_it_is_not_relative_to_the_working_directory(home: Path) -> None:
    """The whole point: `data/aste_live` resolved against a cwd nobody controls."""
    assert harvest_dir().is_absolute()
    assert "data/aste_live" not in str(harvest_dir())
