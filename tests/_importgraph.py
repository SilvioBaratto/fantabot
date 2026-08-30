"""What each module can reach, by reading it rather than importing it.

**Why not import.** Importing to inspect `sys.modules` would pull in `sqlalchemy`,
`playwright` and `claude_agent_sdk`, build a `Settings` from `.env`, and — for the
very modules a layer rule is about — do the thing the rule forbids. The default test
tier also blocks sockets, so an import-based walker could not run where it matters.
An AST walk answers the question without executing a line.

**Why transitive.** A direct-import check cannot see a re-export shim. `db/importers/
matches.py` was four lines of `from fantabot.db.upserts import X as X`; anything
importing it reached the whole upsert layer while appearing to import a leaf. The
repository has had two of those.

**Why function-level and `TYPE_CHECKING` imports count.** They are the interesting
cases, not the edge cases. `asta_engine/prices.py` looks pure — its only `sqlalchemy`
mention is under `TYPE_CHECKING` and its repository import is inside a function body —
and it reaches Postgres on every call. `news/pool.py` and `asta_engine/stateentry.py`
are the same shape. A walker that only read module-level imports would report all
three as clean, which is precisely the reassurance nobody needs.
"""

from __future__ import annotations

import ast
from collections.abc import Iterator
from functools import cache
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"
PACKAGE = SRC / "fantabot"


def modules() -> Iterator[str]:
    """Every module in the package, as a dotted name."""
    for path in sorted(PACKAGE.rglob("*.py")):
        parts = path.relative_to(SRC).with_suffix("").parts
        if parts[-1] == "__init__":
            parts = parts[:-1]
        if parts:
            yield ".".join(parts)


def _path_of(module: str) -> Path | None:
    base = SRC.joinpath(*module.split("."))
    for candidate in (base.with_suffix(".py"), base / "__init__.py"):
        if candidate.is_file():
            return candidate
    return None


@cache
def direct_imports(module: str) -> frozenset[str]:
    """Every module named by an import anywhere in `module`, at any nesting depth.

    Relative imports are resolved against the module's own package, because
    `from .legality import ...` and `from fantabot.asta_engine.legality import ...`
    are the same edge and a graph that saw only one of them would have holes exactly
    where this package is densest.
    """
    path = _path_of(module)
    if path is None:
        return frozenset()

    package = module if (SRC.joinpath(*module.split("."))).is_dir() else module.rpartition(".")[0]
    found: set[str] = set()

    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                owner = package.split(".")
                base = owner[: len(owner) - node.level + 1]
                root = ".".join([*base, node.module] if node.module else base)
            else:
                root = node.module or ""
            if not root:
                continue
            found.add(root)
            # `from x import y` may name a module rather than an attribute; include
            # both readings, and let the resolver drop the one that is not a file.
            found.update(f"{root}.{alias.name}" for alias in node.names)

    return frozenset(found)


@cache
def reachable(module: str) -> frozenset[str]:
    """Everything `module` can reach, transitively, including itself.

    Third-party and stdlib names are kept as leaves — `sqlalchemy`, `typer` and
    `playwright` are the point of most rules — but are not followed, since their
    internals say nothing about this package's layering.
    """
    seen: set[str] = set()
    stack = [module]
    while stack:
        current = stack.pop()
        if current in seen:
            continue
        seen.add(current)
        # "Ours" is "resolves to a file under SRC", not a name prefix: the walker has
        # to be testable against a tree that is not this one, and a name test makes
        # every synthetic fixture silently return no edges at all.
        if _path_of(current) is not None:
            stack.extend(direct_imports(current))
    return frozenset(seen)


def reaches(module: str, target: str) -> bool:
    """Does `module` reach `target`, or anything under it?

    Prefix-aware so a rule can name `fantabot.db` and catch
    `fantabot.db.repositories.aste` without listing it.
    """
    return any(name == target or name.startswith(f"{target}.") for name in reachable(module))


def why(module: str, target: str) -> list[str]:
    """A shortest import path from `module` to `target`, for a failure message.

    A rule that says only "this module reaches sqlalchemy" sends the reader hunting.
    A path says which edge to cut.
    """
    from collections import deque

    queue: deque[list[str]] = deque([[module]])
    seen = {module}
    while queue:
        path = queue.popleft()
        current = path[-1]
        for name in sorted(direct_imports(current)) if _path_of(current) else ():
            if name == target or name.startswith(f"{target}."):
                return [*path, name]
            if name in seen or _path_of(name) is None:
                continue
            seen.add(name)
            queue.append([*path, name])
    return []
