"""The `windows_file_semantics` fixture has to refuse what Windows refuses, and nothing more.

A fixture that refuses every removal, or none, would leave `test_harvest_home.py` green
whatever `adopt` did. These pin both edges. Every assertion here is also what Windows does
natively, so they hold unchanged on the runner where the fixture does nothing.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

pytestmark = pytest.mark.usefixtures("windows_file_semantics")


def _file(path: Path) -> Path:
    path.write_bytes(b"x")
    return path


class TestAnOpenFile:
    """A handle left open is what Windows refuses to delete or move from under its owner."""

    def test_cannot_be_unlinked(self, tmp_path: Path) -> None:
        """The refusal carries the errno and the wording `adopt` hit on the runner."""
        target = _file(tmp_path / "held")

        with target.open("a+b"), pytest.raises(PermissionError) as refused:
            target.unlink()

        assert refused.value.errno == 13
        assert "being used by another process" in str(refused.value)
        assert target.exists()

    def test_cannot_be_removed(self, tmp_path: Path) -> None:
        """`os.remove` is a separate name for `os.unlink` and is guarded separately."""
        target = _file(tmp_path / "held")

        with target.open("rb"), pytest.raises(PermissionError):
            os.remove(target)

        assert target.exists()

    def test_cannot_be_renamed_away(self, tmp_path: Path) -> None:
        """A rename moves the name a handle was opened by, which Windows does not allow."""
        target = _file(tmp_path / "held")

        with target.open("rb"), pytest.raises(PermissionError):
            os.rename(target, tmp_path / "elsewhere")

        assert target.exists()
        assert not (tmp_path / "elsewhere").exists()

    def test_cannot_be_replaced_as_the_source(self, tmp_path: Path) -> None:
        """Moving an open file over another name is refused the same way."""
        target = _file(tmp_path / "held")

        with target.open("rb"), pytest.raises(PermissionError):
            os.replace(target, tmp_path / "elsewhere")

        assert target.exists()

    def test_cannot_be_replaced_as_the_target(self, tmp_path: Path) -> None:
        """Overwriting a file something holds open is refused, and the incoming file stays."""
        held = _file(tmp_path / "held")
        incoming = _file(tmp_path / "incoming")

        with held.open("rb"), pytest.raises(PermissionError):
            os.replace(incoming, held)

        assert incoming.exists()

    def test_is_freed_by_closing_it(self, tmp_path: Path) -> None:
        """The refusal lasts exactly as long as the handle does."""
        target = _file(tmp_path / "held")
        handle = target.open("rb")
        handle.close()

        target.unlink()

        assert not target.exists()


class TestAFileNobodyHolds:
    """The fixture must stay out of the way of every removal Windows allows."""

    def test_is_removed(self, tmp_path: Path) -> None:
        """An idle file goes."""
        target = _file(tmp_path / "idle")

        target.unlink()

        assert not target.exists()

    def test_is_not_confused_with_an_open_neighbour(self, tmp_path: Path) -> None:
        """The check is by file identity, not by directory or by name prefix."""
        held, idle = _file(tmp_path / "held"), _file(tmp_path / "idle")

        with held.open("rb"):
            idle.unlink()

        assert held.exists()
        assert not idle.exists()

    def test_is_renamed_and_replaced(self, tmp_path: Path) -> None:
        """Both ends of a move are idle, so both calls go through."""
        source = _file(tmp_path / "source")
        target = _file(tmp_path / "target")

        os.replace(source, target)
        os.rename(target, tmp_path / "renamed")

        assert [p.name for p in tmp_path.iterdir()] == ["renamed"]

    def test_that_does_not_exist_reports_the_ordinary_error(self, tmp_path: Path) -> None:
        """A missing file is `FileNotFoundError`, which `unlink(missing_ok=True)` relies on."""
        with pytest.raises(FileNotFoundError):
            (tmp_path / "gone").unlink()
