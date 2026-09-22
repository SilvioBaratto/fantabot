"""What each module can reach, by reading it rather than importing it.

**Why not import.** Importing to inspect `sys.modules` would pull in `sqlalchemy`,
`playwright` and `claude_agent_sdk`, build a `Settings` from `.env`, and — for the
very modules a layer rule is about — do the thing the rule forbids. The default test
tier also blocks sockets, so an import-based walker could not run where it matters.
An AST walk answers the question without executing a line.

**Why transitive.** A direct-import check cannot see a re-export shim. `db/importers/
matches.py` was four lines of `from fantabot.adapters.persistence.upserts import X as X`; anything
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
from collections.abc import Callable, Iterator
from functools import cache
from pathlib import Path

from _paths import REPO

SRC = REPO / "src"
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
    `from .legality import ...` and `from fantabot.domain.asta.legality import ...`
    are the same edge and a graph that saw only one of them would have holes exactly
    where this package is densest.
    """
    path = _path_of(module)
    if path is None:
        return frozenset()

    package = _package_of(module)
    found: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        found.update(_imported(node, package))
    return frozenset(found)


def _package_of(module: str) -> str:
    return module if (SRC.joinpath(*module.split("."))).is_dir() else module.rpartition(".")[0]


def _imported(node: ast.AST, package: str) -> set[str]:
    """The modules one `import` statement names; empty for any other node."""
    if isinstance(node, ast.Import):
        return {alias.name for alias in node.names}
    if not isinstance(node, ast.ImportFrom):
        return set()
    if node.level:
        owner = package.split(".")
        base = owner[: len(owner) - node.level + 1]
        root = ".".join([*base, node.module] if node.module else base)
    else:
        root = node.module or ""
    if not root:
        return set()
    # `from x import y` may name a module rather than an attribute; include both
    # readings, and let the resolver drop the one that is not a file.
    return {root, *(f"{root}.{alias.name}" for alias in node.names)}


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

    Prefix-aware so a rule can name `fantabot.adapters.persistence` and catch
    `fantabot.adapters.persistence.repositories.aste` without listing it.
    """
    return any(name == target or name.startswith(f"{target}.") for name in reachable(module))


def resolves(module: str) -> bool:
    """Is `module` ours — a file under `SRC` — rather than a third-party or stdlib leaf?"""
    return _path_of(module) is not None


def why(
    module: str, target: str, *, edges: Callable[[str], frozenset[str]] = direct_imports
) -> list[str]:
    """A shortest import path from `module` to `target`, for a failure message.

    A rule that says only "this module reaches sqlalchemy" sends the reader hunting.
    A path says which edge to cut. `edges` picks the graph: the design graph by default,
    `module_scope_edges` for what an import actually executes.
    """
    from collections import deque

    queue: deque[list[str]] = deque([[module]])
    seen = {module}
    while queue:
        path = queue.popleft()
        current = path[-1]
        for name in sorted(edges(current)) if _path_of(current) else ():
            if name == target or name.startswith(f"{target}."):
                return [*path, name]
            if name in seen or _path_of(name) is None:
                continue
            seen.add(name)
            queue.append([*path, name])
    return []


@cache
def module_scope_imports(module: str) -> frozenset[str]:
    """Only the modules that importing `module` **executes** — the other half of the story.

    `direct_imports` is the design graph and counts everything, on purpose. This is the
    runtime one, for a rule about what gets *loaded*: the numpy guard permits a lazy import
    inside a function, which is exactly what `direct_imports` is built to see. So it reads
    the module body and every block that runs at import — `if`/`else`, both arms of a
    `try`, `with`, loops, a class body — and skips function bodies and `if TYPE_CHECKING:`
    (whose `else` does run). A dynamic `importlib.import_module` is invisible to any AST
    walk; `test_lineup_imports.py` catches that one at runtime instead.
    """
    path = _path_of(module)
    if path is None:
        return frozenset()
    package = _package_of(module)
    found: set[str] = set()
    for node in _executed(ast.parse(path.read_text(encoding="utf-8")).body):
        found.update(_imported(node, package))
    return frozenset(found)


def _executed(body: list[ast.stmt]) -> Iterator[ast.stmt]:
    for node in body:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        yield node
        if isinstance(node, ast.If) and _is_type_checking(node.test):
            yield from _executed(node.orelse)
            continue
        for field in ("body", "orelse", "finalbody"):
            block = getattr(node, field, None)
            if isinstance(block, list):
                yield from _executed(block)
        for arm in (*getattr(node, "handlers", ()), *getattr(node, "cases", ())):
            yield from _executed(arm.body)


def _is_type_checking(test: ast.expr) -> bool:
    return (isinstance(test, ast.Name) and test.id == "TYPE_CHECKING") or (
        isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING"
    )


def _parents(name: str) -> set[str]:
    parts = name.split(".")
    return {".".join(parts[:i]) for i in range(1, len(parts))}


@cache
def module_scope_edges(module: str) -> frozenset[str]:
    """What must run for `module` to be imported: its module-scope imports, and the parent
    packages of those and of `module` itself — `import a.b.c` executes `a/__init__.py` and
    `a/b/__init__.py` first, and neither is named by the statement."""
    names = set(module_scope_imports(module))
    return frozenset(names.union(*map(_parents, names), _parents(module)))


@cache
def module_scope_reachable(module: str) -> frozenset[str]:
    """Everything importing `module` executes, transitively, including itself."""
    seen: set[str] = set()
    stack = [module]
    while stack:
        current = stack.pop()
        if current in seen:
            continue
        seen.add(current)
        if _path_of(current) is not None:
            stack.extend(module_scope_edges(current))
    return frozenset(seen)


def loads_at_import(module: str, target: str) -> bool:
    """Does importing `module` load `target`, or anything under it? Prefix-aware."""
    return any(
        name == target or name.startswith(f"{target}.") for name in module_scope_reachable(module)
    )


def module_source(module: str) -> str:
    """A module's text, resolved the way every other function here resolves one.

    Exposed so a test can contrast the AST walk with the raw text of the *same* file
    without doing its own path arithmetic — which is how `_paths.py` came to exist.
    """
    path = _path_of(module)
    if path is None:
        raise FileNotFoundError(f"{module} does not resolve to a file")
    return path.read_text(encoding="utf-8")


@cache
def names_used(module: str) -> frozenset[str]:
    """Every attribute and bare name the module's own code refers to, at any depth.

    References, not only calls. ``write=rtdb.place_raise`` hands the same power to
    whoever holds the callable, and a rule that counted only ``ast.Call`` nodes would
    be satisfied by moving the lambda one frame up — which is where `interface/asta.py`
    already puts it.

    Names in strings do not count, which is the reason for an AST walk rather than a
    grep: `interface/asta.py` names `place_raise` twice in prose explaining why arming
    takes two locks, and a text scan would report those as calls.
    """
    path = _path_of(module)
    if path is None:
        return frozenset()

    found: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Attribute):
            found.add(node.attr)
        elif isinstance(node, ast.Name):
            found.add(node.id)
        elif isinstance(node, ast.ImportFrom):
            found.update(alias.asname or alias.name for alias in node.names)
    return frozenset(found)


@cache
def defines(module: str) -> frozenset[str]:
    """The top-level names `module` binds: functions, classes and assignments.

    A rule that names a function in another module is silently empty once that function
    is renamed — the same failure `_paths.pkgs` exists to prevent one directory up. This
    is what lets the rule assert its own subject still exists.
    """
    path = _path_of(module)
    if path is None:
        return frozenset()

    found: set[str] = set()
    for node in ast.parse(path.read_text(encoding="utf-8")).body:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            found.add(node.name)
        elif isinstance(node, ast.Assign):
            found.update(t.id for t in node.targets if isinstance(t, ast.Name))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            found.add(node.target.id)
    return frozenset(found)
