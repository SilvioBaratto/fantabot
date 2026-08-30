"""Move source modules to their W6 destinations: `git mv` plus an import rewrite.

Run as `python scripts/move_modules.py <package-or-module> [...]`, naming today's dotted
names relative to `fantabot` (`db`, `db.repositories.aste`, `agentkit`). Every module at or
under a named prefix moves to the destination `tests/_destinations.py` gives it.

**Why a script and not `git mv` by hand.** The rename is the easy half. Each move
invalidates every import of that module across 106 source files and 97 test files, and a
missed one does not always fail: a module that is imported inside a function body raises
only when that function runs, which for `asta bid` is during a live auction. The rewrite
has to be exhaustive and mechanical, and the diff has to be readable as "renames and
import lines only" -- which is what the P12 checkpoint asserts.

**Why it reads the destinations from the test.** The map is checked there for
completeness, layer agreement and collisions. A second copy here would be a second thing
to keep in step, and this whole phase exists because copies drift.

Three things it does that are easy to forget by hand:

* `git mv <pkg> <dest>` **nests silently when `<dest>` already exists** -- it exits 0 and
  produces `adapters/persistence/db/engine.py`. So directories are created only when they
  are not themselves a move target, and each move is checked afterwards.
* `__init__.py` is created for every new package directory, or the modules are importable
  by accident of namespace packages and the layer test's module list goes wrong.
* Imports are rewritten longest-name-first. Rewriting `fantabot.db` before
  `fantabot.db.repositories` would turn the latter into
  `adapters/persistence.repositories`.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

STUB = '"""Layer package. See tests/test_layers.py."""\n'

ROOT = Path(__file__).resolve().parent.parent
PACKAGE = ROOT / "src" / "fantabot"
sys.path.insert(0, str(ROOT / "tests"))

from _destinations import OVERRIDE, destination  # noqa: E402
from test_layers import UNPLACED, layer_of  # noqa: E402


def _modules() -> list[str]:
    out = []
    for path in sorted(PACKAGE.rglob("*.py")):
        parts = path.relative_to(PACKAGE).with_suffix("").parts
        if parts[-1] == "__init__":
            parts = parts[:-1]
        if parts:
            out.append(".".join(parts))
    return out


def _selected(prefixes: list[str]) -> list[str]:
    chosen = [
        m
        for m in _modules()
        if any(m == p or m.startswith(f"{p}.") for p in prefixes)
        # Namespace packages belong to no layer and have no computed destination -- but
        # one with an explicit entry in the map has been placed deliberately, and leaving
        # it behind strands its re-exports in an empty directory.
        and (f"fantabot.{m}" not in UNPLACED or m in OVERRIDE)
    ]
    if not chosen:
        raise SystemExit(f"nothing matches {prefixes}; known: {_modules()}")
    return chosen


def _git(*args: str) -> None:
    subprocess.run(["git", *args], cwd=ROOT, check=True)


def _as_one_directory(prefix: str) -> str | None:
    """The single destination directory a whole package can be renamed onto, or None.

    A directory `git mv` is preferred wherever it is valid: it carries the package's
    `__init__.py` files, which several of these have real re-exports in, and git records
    the whole thing as renames. It is valid only when every module under the package keeps
    its relative position -- `db/models/aste.py` landing at
    `adapters/persistence/models/aste.py` and nothing landing in another layer. `tokens/`
    fails this by design: half of it is domain and half is adapters.
    """
    directory = PACKAGE / prefix.replace(".", "/")
    if not directory.is_dir():
        return None
    roots = set()
    for module in _selected([prefix]):
        source = PACKAGE / (module.replace(".", "/") + ".py")
        if not source.is_file():
            continue
        target = Path(destination(module, layer_of(f"fantabot.{module}")))
        relative = source.relative_to(directory)
        if target.parts[-len(relative.parts):] != relative.parts:
            return None
        roots.add("/".join(target.parts[: -len(relative.parts)]))
    return roots.pop() if len(roots) == 1 else None


def move(prefixes: list[str]) -> dict[str, str]:
    """Perform the renames. Returns `old dotted -> new dotted`."""
    renames: dict[str, str] = {}
    remaining = []
    for prefix in prefixes:
        root = _as_one_directory(prefix)
        if root is None:
            remaining.append(prefix)
            continue
        # Listed before the rename, not after: `_selected` reads the tree, and after the
        # `git mv` the old names are gone. Computing the map afterwards found nothing,
        # left the files moved and every import unrewritten, and the whole point of the
        # script is that those two halves cannot come apart.
        members = _selected([prefix])
        target = PACKAGE / root
        if target.exists():
            raise SystemExit(f"{target} exists — `git mv` would nest inside it, not rename")
        target.parent.mkdir(parents=True, exist_ok=True)
        init = target.parent / "__init__.py"
        if target.parent != PACKAGE and not init.exists():
            init.write_text(STUB)
            _git("add", str(init))
        _git("mv", str(PACKAGE / prefix.replace(".", "/")), str(target))
        # One entry, not one per module. The rewrite is a prefix substitution, so
        # `fantabot.db` -> `fantabot.adapters.persistence` also carries
        # `fantabot.db.models.aste`. Listing members instead missed the package names
        # themselves -- `_selected` drops namespace packages, so `from fantabot.db import
        # database_manager` survived in 87 places while the files had already moved.
        assert members  # the move is only meaningful if it carried something
        renames[f"fantabot.{prefix}"] = f"fantabot.{root.replace('/', '.')}"
    members = _selected(remaining) if remaining else []
    for module in members:
        source = PACKAGE / (module.replace(".", "/") + ".py")
        if not source.is_file():
            # A package: its source is its `__init__.py`. Several of these carry real
            # re-exports, so where they land is a decision, not a default -- hence an
            # explicit entry in the destination map rather than a computed one.
            source = PACKAGE / module.replace(".", "/") / "__init__.py"
        if not source.is_file():
            raise SystemExit(f"{module}: neither a module nor a package")
        target = PACKAGE / destination(module, layer_of(f"fantabot.{module}"))
        target.parent.mkdir(parents=True, exist_ok=True)
        for parent in [target.parent, *target.parent.parents]:
            if parent == PACKAGE:
                break
            init = parent / "__init__.py"
            if not init.exists():
                init.write_text(STUB)
                _git("add", str(init))
        # A stub this script wrote is not a conflict: a package whose modules split
        # across layers has its own `__init__.py` placed by hand, and the first child to
        # move creates a stub at that very path. Anything else is a real collision.
        if target.exists() and target.read_text() == STUB:
            _git("rm", "-q", "-f", str(target))
        if target.exists():
            raise SystemExit(f"{target} already exists — refusing to nest")
        _git("mv", str(source), str(target))
        parts = target.relative_to(PACKAGE).with_suffix("").parts
        # A package's module name is the directory, not `<pkg>.__init__` -- which as a
        # rename-map value rewrote every `fantabot.tokens` into
        # `fantabot.domain.tokens.__init__`.
        if parts[-1] == "__init__":
            parts = parts[:-1]
        new = ".".join(parts)
        renames[f"fantabot.{module}"] = f"fantabot.{new}"
    return renames


def rewrite(renames: dict[str, str], roots: list[Path]) -> int:
    """Rewrite every dotted reference. Longest name first, so prefixes do not collide."""
    ordered = sorted(renames.items(), key=lambda kv: -len(kv[0]))
    touched = 0
    for root in roots:
        for path in sorted(root.rglob("*.py")):
            text = original = path.read_text(encoding="utf-8")
            for old, new in ordered:
                text = re.sub(rf"(?<![\w.]){re.escape(old)}(?![\w])", new, text)
            if text != original:
                path.write_text(text, encoding="utf-8")
                touched += 1
    return touched


def _sweep_pycache() -> None:
    """Remove `__pycache__` before moving.

    `git mv` on a directory moves the tracked files and leaves an untracked
    `__pycache__` behind, so the source directory survives the rename -- and the next
    run then refuses, reporting that the destination exists. mypy also reads the stale
    `.pyc` and reports attributes missing from a module that has moved.
    """
    import shutil

    for cache in list(ROOT.rglob("__pycache__")):
        shutil.rmtree(cache, ignore_errors=True)


def main() -> None:
    prefixes = sys.argv[1:]
    if not prefixes:
        raise SystemExit(__doc__)
    _sweep_pycache()
    renames = move(prefixes)
    touched = rewrite(renames, [ROOT / "src", ROOT / "tests", ROOT / "alembic"])
    for old, new in sorted(renames.items()):
        print(f"  {old}  ->  {new}")
    print(f"\n{len(renames)} modules moved, {touched} files rewritten.")
    print("Now: ruff check --fix src tests && scripts/gate.sh")


if __name__ == "__main__":
    main()
