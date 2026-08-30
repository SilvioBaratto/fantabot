"""Every name the scripts reach for actually exists. Static, socket-free.

**Why this exists.** `scripts/` is the one directory in the repo that nothing checks.
`ruff check src tests` does not lint it, `[tool.mypy] files = ["src"]` does not type it,
no test imports any scraper, and `python scripts/target_price.py --help` exits 0 even
with a broken module body — argparse builds its parser and prints help before any
attribute of an imported module is touched. So a commit that moves a function out from
under a scraper passes `pytest`, `ruff`, `mypy` and a manual `--help`, and is discovered
the next time someone actually scrapes.

That is not hypothetical: folding `scripts/_db.py` into the package is exactly that
move, and this file is the net under it.

**Static, not an import.** Importing these modules pulls in `sqlalchemy`, `httpx` and a
`Settings` read; the default tier forbids the socket and the import is not what is in
question anyway. An AST walk answers the real question — does every attribute these
scripts reference on their shared helper still exist — without running anything.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from _paths import REPO

SCRIPTS = REPO / "scripts"

#: Modules a script may import as a bare sibling name, and where the real file lives.
#: Empty since 2026-08-30, and the directory it guards is nearly empty too: the four
#: scrapers moved into ``fantabot.adapters.scraping`` / ``fantabot.application.pricing`` and their shared
#: helper into ``fantabot.adapters.persistence.scraping``, so all of that is now linted by ``ruff`` and
#: typed by ``mypy --strict``. What is left is ``resolve_aste_live.py``, which imports
#: no sibling.
#:
#: The parse check below still earns its place — ``scripts/`` remains outside both
#: tools — and this table stays so a new sibling cannot reappear unguarded.
SIBLINGS: dict[str, Path] = {}


def _scripts() -> list[Path]:
    return sorted(p for p in SCRIPTS.glob("*.py") if not p.name.startswith("__"))


def _module_level_names(path: Path) -> set[str]:
    """Every top-level def, class and assignment a module exposes."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            names.update(t.id for t in node.targets if isinstance(t, ast.Name))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
        elif isinstance(node, ast.ImportFrom):
            names.update(a.asname or a.name for a in node.names)
        elif isinstance(node, ast.Import):
            names.update((a.asname or a.name).split(".")[0] for a in node.names)
    return names


def _attribute_uses(path: Path, module: str) -> set[str]:
    """Every ``module.attr`` this script reads."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {
        node.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == module
    }


@pytest.mark.parametrize("script", _scripts(), ids=lambda p: p.name)
def test_every_script_parses(script: Path) -> None:
    ast.parse(script.read_text(encoding="utf-8"))


@pytest.mark.parametrize("script", _scripts(), ids=lambda p: p.name)
def test_every_sibling_attribute_a_script_reads_exists(script: Path) -> None:
    """The check that would have caught a fold done wrong.

    A scraper that calls ``_db.session()`` after ``session`` has moved is not a
    syntax error, is not a lint error, is not a type error under
    ``files = ["src"]``, and does not fail ``--help``. It fails on the next scrape.
    """
    for module, target in SIBLINGS.items():
        used = _attribute_uses(script, module)
        if not used:
            continue
        assert target.exists(), f"{script.name} imports {module}, which is gone"
        available = _module_level_names(target)
        missing = sorted(used - available)
        assert not missing, (
            f"{script.name} calls {module}.{{{', '.join(missing)}}}, "
            f"which {target.name} no longer defines"
        )
