"""One journal path, derived — and deliberately *not* moved.

Four modules joined `room_journal.jsonl` onto `fantabot_data_dir` by hand:
`interface/asta.py` twice (`asta room` and `asta bid`) and the app's endpoint twice. Four
literals is four chances to disagree about the evening's only record, and the app's reader
had already been written against a path the CLI's writer only sometimes uses.

**The home does not move.** `fantabot_data_dir` is `Path("./data")` and *relative*, unlike
`fantabot_harvest_dir` — so writer and reader agree only when both processes started from
the repository root. That is a real footgun and it stays, because moving it would move an
artefact the CLI owns and the 2026-09-01 audit was performed against. What this function
buys is that the footgun is stated once and every caller has the same one.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from fantabot.config import journal_path


def test_it_is_absolute_so_a_viewer_can_say_where_it_looked(tmp_path: Path) -> None:
    """"No journal yet" and "you are looking in the wrong place" are the same screen
    until the path is resolved and shown."""
    assert journal_path().is_absolute()


def test_it_names_the_file_the_cli_has_always_written(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("FANTABOT_DATA_DIR", str(tmp_path))

    assert journal_path() == (tmp_path / "room_journal.jsonl").resolve()


def test_a_fresh_settings_per_call_so_an_exported_value_wins_at_call_time(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`harvest_dir`'s reason, and the app's: the module singleton binds at import, and a
    launcher started under another working directory must see its own value."""
    monkeypatch.setenv("FANTABOT_DATA_DIR", str(tmp_path / "one"))
    first = journal_path()
    monkeypatch.setenv("FANTABOT_DATA_DIR", str(tmp_path / "two"))

    assert journal_path() != first


def _string_literals_in_code(source: str) -> set[str]:
    """Every string constant that is not a docstring.

    A text scan is wrong here and was: `application/asta_bench.py` names the file twice in
    prose, explaining that its fixtures are diffable against a real evening's journal.
    Reporting those as path joins is the same mistake the writing-call rule avoids by
    walking the AST rather than grepping.
    """
    tree = ast.parse(source)
    docstrings = {
        node.body[0].value
        for node in ast.walk(tree)
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef)
        and node.body
        and isinstance(node.body[0], ast.Expr)
        and isinstance(node.body[0].value, ast.Constant)
        and isinstance(node.body[0].value.value, str)
    }
    return {
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and node not in docstrings
    }


def test_nothing_else_names_the_journal_file_in_code() -> None:
    """The four literals this function replaced. A fifth is the drift starting again."""
    from _paths import PACKAGE

    offenders = sorted(
        str(path.relative_to(PACKAGE))
        for path in PACKAGE.rglob("*.py")
        if path.name != "config.py"
        and "room_journal.jsonl" in _string_literals_in_code(path.read_text(encoding="utf-8"))
    )
    assert not offenders, f"these name the journal file themselves: {offenders}"


def test_the_scan_can_tell_a_path_join_from_prose() -> None:
    """`application/asta_bench.py` names the file twice, both times in a docstring."""
    assert _string_literals_in_code('"""about room_journal.jsonl"""\nx = 1\n') == set()
    assert "room_journal.jsonl" in _string_literals_in_code('p = d / "room_journal.jsonl"\n')
