"""The code's side of the schema comparison: what alembic's scripts say the head is.

Read through alembic's own `ScriptDirectory`, so what is compared against the database is
what `alembic upgrade head` would actually apply — not a second parser of revision files
that could disagree with it. No database is involved: this is a directory of Python files.

Two properties are not obvious and are pinned. Reading the heads must not change what the
process imports (`alembic.ini` carries a cwd-relative `prepend_sys_path`, which alembic
applies to `sys.path` on load), and an install with no `alembic.ini` beside it — a wheel —
is an answer, not an error.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest
from _paths import REPO

from fantabot.adapters.persistence.alembic_scripts import alembic_ini_path, load_script_revisions

REVISION = (
    '"""{name}"""\n\nrevision = "{rev}"\ndown_revision = {down}\nbranch_labels = None\n'
    "depends_on = None\n\n\nupgrade = downgrade = lambda: None\n"
)


def _project(root: Path, chain: list[tuple[str, str | None]]) -> Path:
    """An alembic project of empty migrations: `(revision, down_revision)` per file."""
    (root / "alembic" / "versions").mkdir(parents=True)
    ini = root / "alembic.ini"
    ini.write_text(
        "[alembic]\nscript_location = %(here)s/alembic\nprepend_sys_path = src\n"
        "path_separator = os\n"
    )
    for rev, down in chain:
        body = REVISION.format(name=rev, rev=rev, down=f'"{down}"' if down else "None")
        (root / "alembic" / "versions" / f"{rev}_step.py").write_text(body)
    return ini


class TestReadingAProject:
    """A throwaway project, so the shape of the answer is pinned without the real history."""

    def test_a_linear_history_has_one_head_and_counts_each_revision_to_it(
        self, tmp_path: Path
    ) -> None:
        """`pending[r]` is how many migrations `alembic upgrade head` would apply from r."""
        ini = _project(tmp_path, [("aaa", None), ("bbb", "aaa"), ("ccc", "bbb")])

        scripts = load_script_revisions(ini)

        assert scripts is not None
        assert scripts.heads == ("ccc",)
        assert dict(scripts.pending) == {"ccc": 0, "bbb": 1, "aaa": 2}

    def test_every_revision_in_the_scripts_is_known(self, tmp_path: Path) -> None:
        """The keys are the set a database revision is checked against for "unknown"."""
        ini = _project(tmp_path, [("aaa", None), ("bbb", "aaa")])

        scripts = load_script_revisions(ini)

        assert scripts is not None
        assert set(scripts.pending) == {"aaa", "bbb"}

    def test_a_branched_history_reports_every_head(self, tmp_path: Path) -> None:
        """Two heads means a database is at head only when it holds both."""
        ini = _project(tmp_path, [("aaa", None), ("left", "aaa"), ("right", "aaa")])

        scripts = load_script_revisions(ini)

        assert scripts is not None
        assert set(scripts.heads) == {"left", "right"}
        assert scripts.pending["left"] == 0
        assert scripts.pending["right"] == 0

    def test_with_no_argument_it_reads_where_the_locator_points(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The default is the locator's answer, looked up at call time: the app's migration
        runner asks the same function, and the two must never read different directories."""
        from fantabot.adapters.persistence import alembic_scripts

        ini = _project(tmp_path, [("aaa", None), ("bbb", "aaa")])
        monkeypatch.setattr(alembic_scripts, "alembic_ini_path", lambda: ini)

        scripts = load_script_revisions()

        assert scripts is not None
        assert scripts.heads == ("bbb",)

    def test_a_missing_ini_is_scripts_unavailable_not_an_error(self, tmp_path: Path) -> None:
        """An installed wheel has no `alembic.ini` beside it; that is a state to report."""
        assert load_script_revisions(tmp_path / "alembic.ini") is None

    def test_an_ini_whose_script_directory_is_gone_is_also_unavailable(
        self, tmp_path: Path
    ) -> None:
        """`script_location` pointing at nothing must not surface as an alembic traceback."""
        ini = tmp_path / "alembic.ini"
        ini.write_text("[alembic]\nscript_location = %(here)s/nowhere\n")

        assert load_script_revisions(ini) is None

    def test_reading_the_heads_leaves_sys_path_as_it_found_it(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """alembic prepends `prepend_sys_path` on load; a read of the head must not change
        what the process goes on to import."""
        ini = _project(tmp_path, [("aaa", None)])
        monkeypatch.chdir(tmp_path)
        before = list(sys.path)

        load_script_revisions(ini)

        assert sys.path == before


class TestAMigrationThatWillNotLoad:
    """Reading the head executes every migration file, which is arbitrary code."""

    def test_the_error_propagates_for_the_caller_to_name(self, tmp_path: Path) -> None:
        """A defect in the checkout is not "scripts unavailable". Absorbing it is the
        application layer's decision, where it becomes a verdict instead of a traceback."""
        ini = _project(tmp_path, [("aaa", None)])
        (tmp_path / "alembic" / "versions" / "bbb_step.py").write_text(
            'revision = "bbb"\ndown_revision = "aaa"\nimport a_module_that_is_not_there\n'
        )

        with pytest.raises(ImportError):
            load_script_revisions(ini)

    def test_sys_path_is_restored_on_that_path_too(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The restore is in a `finally`: an exception must not leave the prepended
        directory behind for the rest of the process."""
        ini = _project(tmp_path, [("aaa", None)])
        (tmp_path / "alembic" / "versions" / "bbb_step.py").write_text(
            'revision = "bbb"\ndown_revision = "aaa"\nraise RuntimeError("half-written")\n'
        )
        monkeypatch.chdir(tmp_path)
        before = list(sys.path)

        with pytest.raises(RuntimeError):
            load_script_revisions(ini)

        assert sys.path == before


class TestTheRealHistory:
    """This checkout's own migrations, which is the head the live database is held to."""

    def test_the_locator_finds_this_checkouts_alembic_ini(self) -> None:
        """`parents[2]` of the package is the repository root in an editable install."""
        assert alembic_ini_path() == REPO / "alembic.ini"
        assert alembic_ini_path().is_file()

    def test_the_head_is_a_revision_file_in_versions(self) -> None:
        """The scripts adapter and the directory on disk agree about what exists."""
        scripts = load_script_revisions()

        assert scripts is not None
        declared = re.compile(r"""^revision(?::\s*str)?\s*=\s*['"]([0-9a-f]+)['"]""", re.MULTILINE)
        on_disk = {
            match.group(1)
            for path in (REPO / "alembic" / "versions").glob("*.py")
            for match in declared.finditer(path.read_text())
        }
        assert set(scripts.pending) == on_disk
        assert len(scripts.heads) >= 1
        assert all(scripts.pending[head] == 0 for head in scripts.heads)

    def test_the_default_load_does_not_depend_on_the_working_directory(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The hourly job and the app start wherever they start; the locator must not care."""
        monkeypatch.chdir(tmp_path)

        assert load_script_revisions() is not None
