"""The test tree mirrors the source tree, and the table saying so is complete.

Three ways this goes wrong quietly, so three checks. A file with no entry stays at the
root and nobody notices; an entry with no file is a decision about something that no
longer exists; and two files sharing a basename break pytest's module naming, since
`tests/` has no `__init__.py` and the name is derived from the path.
"""

from __future__ import annotations

import collections
from pathlib import Path

from _paths import TESTS
from _testtree import TREE


def _test_files() -> list[Path]:
    return sorted(p for p in TESTS.rglob("test_*.py") if "integration" not in p.parts)


def test_every_test_file_has_a_place() -> None:
    missing = sorted(p.name for p in _test_files() if p.name not in TREE)
    assert missing == [], f"no entry in tests/_testtree.py for: {missing}"


def test_every_entry_names_a_file_that_exists() -> None:
    have = {p.name for p in _test_files()}
    assert sorted(set(TREE) - have) == [], "the table decides where deleted files go"


def test_each_file_is_where_the_table_says() -> None:
    misplaced = [
        (p.name, str(p.parent.relative_to(TESTS)), TREE[p.name])
        for p in _test_files()
        if p.name in TREE and str(p.parent.relative_to(TESTS)) != TREE[p.name]
    ]
    assert misplaced == [], f"(file, where it is, where the table says): {misplaced}"


def test_no_two_test_files_share_a_basename() -> None:
    """`tests/` has no `__init__.py`, so pytest names a module by its path.

    Two files with the same basename in different directories then collide, and pytest
    reports `import file mismatch` at collection rather than running either.
    """
    counts = collections.Counter(p.name for p in TESTS.rglob("test_*.py"))
    clashes = sorted(name for name, n in counts.items() if n > 1)
    assert clashes == [], f"basenames used more than once: {clashes}"
