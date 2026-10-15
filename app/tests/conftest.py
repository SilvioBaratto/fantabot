"""Fixtures for the launcher suite.

`windows_file_semantics` exists because the one Windows runner is the only place a file this
process still holds open is refused removal, and a defect of that kind is found by `app-ci`
after the push or by a user. It makes a POSIX machine refuse the same way.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from typing import Any

import pytest

#: What `open()` leaves a Windows file with: no `FILE_SHARE_DELETE`. CPython opens through the
#: CRT's `_SH_DENYNO`, which shares read and write and nothing else, so a delete or a rename
#: of a file with any handle open is `ERROR_SHARING_VIOLATION` (32), surfaced as errno 13.
_SHARING_VIOLATION = (
    13,
    "The process cannot access the file because it is being used by another process",
)


def is_open_here(path: str | os.PathLike[str]) -> bool:
    """Whether any descriptor of this process refers to the file at `path`.

    Asks the kernel by inode instead of tracking `open()` calls, so a handle counts however
    it was made — `Path.open`, `os.open`, a library's own — which is what a Windows share
    check sees. A symlink is looked at as a link: removing one never touches its target.
    """
    try:
        target = os.stat(path, follow_symlinks=False)
    except OSError:
        return False
    for name in os.listdir("/dev/fd"):
        try:
            held = os.fstat(int(name))
        except (OSError, ValueError):
            continue
        if (held.st_dev, held.st_ino) == (target.st_dev, target.st_ino):
            return True
    return False


def _refusing(real: Callable[..., Any], *, paths: int) -> Callable[..., Any]:
    """Wrap `real` so it raises a sharing violation if any of its first `paths` arguments is open.

    Args:
        real: The `os` function being replaced.
        paths: How many leading positional arguments are file paths. A rename or a replace
            has two, and Windows refuses to overwrite a target something has open just as it
            refuses to move a source something has open.

    Returns:
        A function with `real`'s call signature.
    """

    def guarded(*args: Any, **kwargs: Any) -> Any:
        """Refuse an open file, otherwise defer to the real call."""
        for candidate in args[:paths]:
            if is_open_here(candidate):
                raise PermissionError(*_SHARING_VIOLATION, os.fspath(candidate), 32)
        return real(*args, **kwargs)

    return guarded


@pytest.fixture
def windows_file_semantics(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make `os.unlink`, `remove`, `rename` and `replace` refuse a file held open here.

    On Windows this does nothing, because the operating system already enforces the rule and
    there is no `/dev/fd` there for the inode walk to ask.

    Only that rule is modelled. A directory with an open file inside, a file open in
    *another* process, and `msvcrt.locking` byte-range locks are not, so a green run here
    is evidence about this process's own handles and no more.
    """
    if os.name == "nt":
        return
    monkeypatch.setattr(os, "unlink", _refusing(os.unlink, paths=1))
    monkeypatch.setattr(os, "remove", _refusing(os.remove, paths=1))
    monkeypatch.setattr(os, "rename", _refusing(os.rename, paths=2))
    monkeypatch.setattr(os, "replace", _refusing(os.replace, paths=2))
