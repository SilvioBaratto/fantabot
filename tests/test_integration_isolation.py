"""The `db` tier runs against the development database, which holds real data.

That is the point — the contracts these tests pin are about ordering, windowing and NULL
handling, which a fake session cannot settle. It is also the hazard: the database is
*shared*, so a test that borrows a real row is not isolated from whatever `news fetch` or
the scrapers last wrote, and its result depends on the calendar.

This file is the standing guard, checked statically so it runs in the default socket-free
tier. It exists because the repository has already paid for this lesson once: the
`canary_player` fixture in ``test_news_fetch_write.py`` records that ``pytest -m db`` was
deleting a real player's weekly reading, twice per test, and CLAUDE.md's rule is that a
past Wednesday cannot be regenerated. That fixture fixed one file; the rule belongs to all
of them.

The guard was one literal substring, ``"SELECT id FROM players"``, over
``tests/integration/`` alone. Both halves of that were too small. The subset that runs
against the **canonical** database — the ``dbdata`` marker — had grown from five modules
to thirteen, three of them outside ``tests/integration/`` and so outside the glob
entirely; and the borrow this file is named after had been rewritten as a *repository
call* (``load_pool(session, "2026/27")[0].id``), which a substring cannot see. What the
guard asserts now is the rule rather than one spelling of it:

    a ``dbdata`` test must not take a real seed row as its subject, nor delete seed rows.

**And the unit it judges is the test's whole reachable surface, not its body.** The
version before this one walked function bodies, and the hazard it looks for is a *pairing*
— a real row taken, and then written to. So moving either half of the pair four lines up
into a ``@pytest.fixture`` split it across two units and neither one offended, with no
change whatsoever in what reaches Postgres. Measured against ``test_clearing_sales.py``: a
borrow and a ``DELETE`` in one function failed, and **nine** rearrangements of the same
two statements all passed — the borrow in a same-file fixture; in a fixture in the
enclosing ``conftest``; in a class fixture chained to a module one; in a plain
module-level helper; the *write* in an ``autouse`` fixture; the write in a fixture pulled
in by ``@pytest.mark.usefixtures`` on the test; the same by way of the module's own
``pytestmark``; a whole-table ``DELETE`` in a ``conftest`` fixture; and the borrow fetched
by ``request.getfixturevalue``.

A fixture is part of the unit, so ``_surface`` folds in every fixture a unit requests (its
own module's, its class's, and every enclosing ``conftest``'s, transitively), every
``autouse`` fixture that applies to it, every ``usefixtures`` name, and every helper
function it calls by name — in its own module or imported from ``conftest``. All nine
now fail.

Two boundaries are stated rather than assumed. What a static walk cannot reach — a
plugin's fixture, one registered through ``pytest_plugins``, one fetched by a computed
name — is pinned by ``test_every_fixture_a_dbdata_unit_requests_is_resolved`` and
``test_no_dbdata_module_reaches_a_fixture_by_a_computed_name``, which fail when the
unreached surface grows. And because both checks assert an *empty* list against a clean
tree, every way of examining nothing reads exactly like finding nothing: the positive
controls below build each offence in a temporary module and assert it comes back named,
through the fixture hop the body-only version could not make.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Iterable
from dataclasses import dataclass
from functools import cache
from pathlib import Path

import pytest
from _paths import INTEGRATION, TESTS

#: The borrow: *selecting ids* out of the table to use as test subjects. Whichever ids come
#: back are real players with real readings, so the test collides with production rows on
#: `(data_run, player_id)` and reads back somebody else's data.
#:
#: Narrow on purpose. ``SELECT count(*) FROM players`` asserts a table-level invariant and
#: ``DELETE FROM players WHERE id = :p`` is a synthetic fixture cleaning up after itself —
#: both are correct, and a guard that flagged them would be turned off rather than obeyed.
BORROW = "SELECT id FROM players"


def _code_strings(path: Path) -> list[str]:
    """Every string literal that is not a docstring.

    Docstrings are excluded deliberately: the two fixtures that already fixed this bug
    *describe* the old query in prose, and a grep-based guard would flag the very comments
    warning against it.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docstrings = {
        id(node.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
    }
    return [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in docstrings
    ]


def _dbdata_modules() -> list[Path]:
    """Every test module that reaches the canonical database, wherever it lives.

    Keyed on the marker, not on a directory: three of the thirteen sit under
    ``tests/adapters/persistence/`` and a glob of ``tests/integration/`` never saw them.
    Any spelling of the marker counts — module ``pytestmark``, a class decorator, a
    function decorator — because all three reach ``get_closest_marker("dbdata")`` and so
    all three get the canonical DSN.
    """
    found: list[Path] = []
    for path in sorted(TESTS.rglob("test_*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        if any(
            isinstance(node, ast.Attribute)
            and node.attr == "dbdata"
            and isinstance(node.value, ast.Attribute)
            and node.value.attr == "mark"
            for node in ast.walk(tree)
        ):
            found.append(path)
    return found


#: Resolved once at import: the parametrization below is built from it, and a list that
#: came back empty would turn every test in this file into zero test cases reported green.
#: `test_the_dbdata_modules_are_found` is what stops that.
DBDATA_MODULES = _dbdata_modules()

#: Every file the borrow check judges: the whole integration directory (its db-only
#: members share a database across the tier, so a borrow is wrong there too) plus every
#: `dbdata` module wherever it lives.
BORROW_SCOPE = sorted(set(INTEGRATION.glob("test_*.py")) | set(DBDATA_MODULES))


def test_the_dbdata_modules_are_found() -> None:
    """A scan over a moved directory examines no files and reports success.

    The count is a floor and the two named files are the two that *write*, so this fails
    if either is renamed away from the checks below rather than fixed.
    """
    names = {path.name for path in DBDATA_MODULES}

    assert len(DBDATA_MODULES) >= 13, f"only {len(DBDATA_MODULES)} dbdata modules found"
    assert {"test_db.py", "test_news_fetch_write.py"} <= names, names
    assert any(path.parent != INTEGRATION for path in DBDATA_MODULES), (
        "the dbdata modules outside tests/integration/ have gone missing from the scan"
    )

    # And the unit walk inside them: a `_units` that resolved every marker to False would
    # leave both checks below iterating over nothing and reporting green.
    judged = [
        f"{path.name}::{unit.label}"
        for path in DBDATA_MODULES
        for unit in _units(ast.parse(path.read_text(encoding="utf-8")))
        if unit.marked and unit.label != "<module>"
    ]
    assert len(judged) >= 110, f"only {len(judged)} dbdata units judged: {judged[:5]}"
    assert any(name.startswith("test_db.py::TestPlayersSeed.") for name in judged), (
        "a class-level `pytestmark = pytest.mark.dbdata` is no longer being resolved"
    )
    assert not any(name.startswith("test_db.py::TestSentimentReadPath.") for name in judged), (
        "the classes moved to `fantabot_test` are being judged as canonical again"
    )


@pytest.mark.parametrize("path", BORROW_SCOPE, ids=lambda p: p.name)
def test_no_integration_test_borrows_a_real_player_id(path: Path) -> None:
    """Use a synthetic id instead — one with no `quotazioni` row, so `load_pool` never
    returns it and no real reading can share its key."""
    offenders = [text for text in _code_strings(path) if BORROW in text]

    assert offenders == [], (
        f"{path.name} borrows real player ids: {offenders}. "
        "Use the `synthetic_players` fixture (tests/conftest.py) — the db tier shares a "
        "database "
        "with real data, and a borrowed row collides with production."
    )


# --- the `dbdata` bypass: the canonical database is not a scratch database ------------
#
# `tier_database_url` (tests/conftest.py) sends a `dbdata`-marked node to the **canonical**
# `fantabot` database, because there is no fixture for 614,163 recorded rows. CLAUDE.md
# granted that bypass to files that *read* the seed. Reading is what the marker is for and
# nothing below touches it. What these two checks are about is the other half.
#
# Both are judged per *unit* — one function, one method, or the module's own top level,
# **together with everything that unit reaches**: the fixtures it requests, the `autouse`
# fixtures that apply to it, and the helpers it calls. The hazard is a pairing and not
# either half alone, so a unit boundary drawn at the `def` is a boundary an author can
# step over without changing a single query. `test_db.py` still calls `load_pool` in three
# read-only tests and writes synthetic rows in a dozen others; a module-level check would
# flag it for doing two safe things in one file, and a guard that cries wolf is one that
# gets turned off.

#: Selecting an **id**: whatever comes back is a real player, which the unit then uses as
#: a subject. Keyed on the projection rather than on a list of tables — `SELECT count(*)`,
#: `SELECT md5(...)` and `SELECT riassunto ...` are assertions *about* the corpus and are
#: exactly what `dbdata` is for, while a table list goes stale the next time one is added.
SUBJECT_SQL = re.compile(
    r"\bSELECT\s+(?:DISTINCT\s+)?(?:\w+\s*\.\s*)?(?:id|player_id)\b.*?\bFROM\b", re.I | re.S
)

#: A write, as a statement.  `UPDATE` is matched with its `SET`, so the word in prose or
#: in an `ON CONFLICT DO UPDATE` tail does not count on its own.
WRITE_SQL = re.compile(
    r"\b(?:INSERT\s+INTO|DELETE\s+FROM|TRUNCATE)\b|\bUPDATE\s+\"?\w+\"?\s+SET\b", re.I
)

#: A write, as a call. Whole-word prefixes, so the read `recorded_auctions` does not match
#: `record` and `appearances` does not match `append`.
WRITE_CALL = re.compile(
    r"^(?:upsert|append|insert|delete|remove|record|purge|truncate|backfill|exclude"
    r"|save|store|write|sync)(?:_|$)"
)

#: `DELETE`/`TRUNCATE`/`UPDATE` with no `WHERE` takes the whole table. Against the
#: canonical database that is the seed, and the outer rollback is the only thing between
#: it and a re-scrape — which for a past Wednesday cannot be run at all.
WHOLE_TABLE_SQL = re.compile(r"\b(?:DELETE\s+FROM|TRUNCATE|UPDATE\s+\"?\w+\"?\s+SET)\b", re.I)


def _fantabot_names(tree: ast.Module) -> frozenset[str]:
    """Names this module imports from production code, at module or function scope.

    Function-scope imports are counted because that is where the borrow was hiding:
    `from fantabot.adapters.persistence.news_pool import load_pool` sits inside
    `_already_stored_pool_player`, four lines above the write it feeds.
    """
    return frozenset(
        alias.asname or alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        and (node.module or "").split(".")[0] == "fantabot"
        for alias in node.names
    )


def _conftest_names(tree: ast.Module) -> frozenset[str]:
    """Names this module imports from `conftest`, at module or function scope.

    `tests/` has no `__init__.py`, so `from conftest import make_synthetic_players` is how
    a test module reaches a shared helper that is not a fixture. Such a helper is part of
    the caller's surface exactly as a same-file helper is.
    """
    return frozenset(
        alias.asname or alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and (node.module or "") == "conftest"
        for alias in node.names
    )


def _marks_dbdata(nodes: Iterable[ast.AST]) -> bool:
    """Is `pytest.mark.dbdata` anywhere in these nodes?"""
    return any(
        isinstance(inner, ast.Attribute)
        and inner.attr == "dbdata"
        and isinstance(inner.value, ast.Attribute)
        and inner.value.attr == "mark"
        for node in nodes
        for inner in ast.walk(node)
    )


def _pytestmark(body: list[ast.stmt]) -> list[ast.AST]:
    """The `pytestmark = ...` assignments directly in this module or class body."""
    return [
        stmt
        for stmt in body
        if isinstance(stmt, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "pytestmark" for target in stmt.targets)
    ]


def _decorator_name(node: ast.expr) -> str:
    """`pytest.fixture` -> "fixture", `fixture(scope=...)` -> "fixture"."""
    target = node.func if isinstance(node, ast.Call) else node
    if isinstance(target, ast.Attribute):
        return target.attr
    return target.id if isinstance(target, ast.Name) else ""


def _usefixtures(nodes: Iterable[ast.AST]) -> tuple[str, ...]:
    """The names in `pytest.mark.usefixtures("a", "b")`, anywhere inside *nodes*.

    A fixture requested this way never appears in the signature, so a walk over parameters
    alone cannot see it — and `autouse=True` is not the only way to be handed one without
    naming it in a `def`. Walked rather than read off the top of a decorator list, because
    the same marker is legal in a module or class `pytestmark`, where it applies to every
    test under it and is spelled inside a list.
    """
    return tuple(
        argument.value
        for node in nodes
        for inner in ast.walk(node)
        if isinstance(inner, ast.Call) and _decorator_name(inner) == "usefixtures"
        for argument in inner.args
        if isinstance(argument, ast.Constant) and isinstance(argument.value, str)
    )


def _parametrized(decorators: list[ast.expr]) -> tuple[str, ...]:
    """Argnames `@pytest.mark.parametrize` supplies, which are values and not fixtures.

    Excluded from the unresolved-fixture ratchet below, and only from that: a parametrized
    argname has no definition to walk into because there is nothing behind it but the
    literal list in the decorator. `indirect=` is the exception and is deliberately **not**
    excluded — there the argname *is* a fixture name, and one this walk must find.
    """
    names: list[str] = []
    for node in decorators:
        if not isinstance(node, ast.Call) or _decorator_name(node) != "parametrize":
            continue
        if any(
            keyword.arg == "indirect"
            and not (isinstance(keyword.value, ast.Constant) and not keyword.value.value)
            for keyword in node.keywords
        ):
            continue
        if not node.args:
            continue
        argnames = node.args[0]
        if isinstance(argnames, ast.Constant) and isinstance(argnames.value, str):
            names.extend(part.strip() for part in argnames.value.split(","))
        elif isinstance(argnames, ast.List | ast.Tuple):
            names.extend(
                element.value
                for element in argnames.elts
                if isinstance(element, ast.Constant) and isinstance(element.value, str)
            )
    return tuple(name for name in names if name)


@dataclass(frozen=True)
class Unit:
    """One function, one method, or a module's own top level — and how it is wired in.

    `nodes` is the unit's own code. `params`, `usefixtures` and `calls` are what it
    *reaches*, and `_surface` follows them; judging `nodes` alone is what let a borrow
    move into a fixture and disappear.
    """

    label: str
    marked: bool
    nodes: tuple[ast.AST, ...]
    #: The signature, minus `self`/`cls`. For a test or a fixture these are fixture names.
    params: tuple[str, ...]
    usefixtures: tuple[str, ...]
    #: Argnames `parametrize` fills in — values, not fixtures, and so not walked into.
    parametrized: tuple[str, ...]
    #: Bare `name(...)` calls anywhere inside, including inside nested `def`s.
    calls: tuple[str, ...]
    #: `self.name(...)` calls, resolved against the unit's own class.
    methods: tuple[str, ...]
    is_fixture: bool
    autouse: bool
    #: "" at module level, "Outer.Inner." for a method.
    scope: str

    @property
    def is_test(self) -> bool:
        return self.label.rpartition(".")[2].startswith("test")

    @property
    def takes_fixtures(self) -> bool:
        return self.is_test or self.is_fixture


def _make_unit(
    stmt: ast.FunctionDef | ast.AsyncFunctionDef,
    prefix: str,
    marked: bool,
    inherited_usefixtures: tuple[str, ...] = (),
) -> Unit:
    fixture_decorators = [
        node for node in stmt.decorator_list if _decorator_name(node) == "fixture"
    ]
    autouse = any(
        keyword.arg == "autouse"
        and isinstance(keyword.value, ast.Constant)
        and bool(keyword.value.value)
        for node in fixture_decorators
        if isinstance(node, ast.Call)
        for keyword in node.keywords
    )
    signature = stmt.args
    params = tuple(
        argument.arg
        for argument in (*signature.posonlyargs, *signature.args, *signature.kwonlyargs)
        if argument.arg not in {"self", "cls"}
    )

    nodes = tuple(ast.walk(stmt))
    calls = tuple(
        node.func.id
        for node in nodes
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    )
    methods = tuple(
        node.func.attr
        for node in nodes
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "self"
    )
    return Unit(
        label=f"{prefix}{stmt.name}",
        marked=marked,
        nodes=nodes,
        params=params,
        usefixtures=(*inherited_usefixtures, *_usefixtures(stmt.decorator_list)),
        parametrized=_parametrized(stmt.decorator_list),
        calls=calls,
        methods=methods,
        is_fixture=bool(fixture_decorators),
        autouse=autouse,
        scope=prefix,
    )


def _units(tree: ast.Module) -> list[Unit]:
    """Every function, every method, and the module's own top level.

    A nested `def` folds into the unit that holds it: `_fake_fetch`'s inner `fetch_all` is
    the same piece of code as far as "did this read a real row and then write it" goes.

    `marked` resolves the marker the way `get_closest_marker` does — module `pytestmark`,
    class decorator or class `pytestmark`, function decorator — so a module may hold both
    kinds, and a test that needs only a schema can be moved to `fantabot_test` where it
    belongs instead of being exempted from the rule.

    **The module's own top level is always flagged**, in any module that carries the
    marker anywhere. Its helpers are shared, and which side of the split calls one is not
    a question a static walk can answer; the borrow this guard was widened for lived in
    exactly such a helper.
    """
    inherited = _marks_dbdata(_pytestmark(tree.body))
    units: list[Unit] = []
    residue: list[ast.AST] = []

    def take(body: list[ast.stmt], prefix: str, marked: bool, requested: tuple[str, ...]) -> None:
        requested = (*requested, *_usefixtures(_pytestmark(body)))
        for stmt in body:
            if isinstance(stmt, ast.FunctionDef | ast.AsyncFunctionDef):
                own = marked or _marks_dbdata(stmt.decorator_list)
                units.append(_make_unit(stmt, prefix, own, requested))
            elif isinstance(stmt, ast.ClassDef):
                own = (
                    marked
                    or _marks_dbdata(stmt.decorator_list)
                    or _marks_dbdata(_pytestmark(stmt.body))
                )
                take(
                    stmt.body,
                    f"{prefix}{stmt.name}.",
                    own,
                    (*requested, *_usefixtures(stmt.decorator_list)),
                )
                residue.extend(
                    node
                    for inner in stmt.body
                    if not isinstance(inner, ast.FunctionDef | ast.AsyncFunctionDef)
                    for node in ast.walk(inner)
                )
            else:
                residue.extend(ast.walk(stmt))

    take(tree.body, "", inherited, ())
    units.append(
        Unit(
            label="<module>",
            marked=True,
            nodes=tuple(residue),
            params=(),
            usefixtures=(),
            parametrized=(),
            calls=tuple(
                node.func.id
                for node in residue
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            ),
            methods=(),
            is_fixture=False,
            autouse=False,
            scope="",
        )
    )
    return units


def _docstring_ids(tree: ast.Module) -> frozenset[int]:
    return frozenset(
        id(node.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
    )


@dataclass(frozen=True)
class Parsed:
    """One parsed module, kept alive by `_parsed`'s cache.

    The cache is load-bearing and not an optimisation: `docstrings` is a set of `id()`s
    into `tree`, and a tree that were collected would hand those ids back to some other
    object.
    """

    path: Path
    tree: ast.Module
    docstrings: frozenset[int]
    imported: frozenset[str]
    from_conftest: frozenset[str]
    units: tuple[Unit, ...]


@cache
def _parsed(path: Path) -> Parsed:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return Parsed(
        path=path,
        tree=tree,
        docstrings=_docstring_ids(tree),
        imported=_fantabot_names(tree),
        from_conftest=_conftest_names(tree),
        units=tuple(_units(tree)),
    )


@cache
def _conftests(path: Path) -> tuple[Path, ...]:
    """The `conftest.py` files pytest loads for *path*, nearest first.

    Walked rather than hard-coded at `tests/conftest.py`: there is one today, and a
    `tests/integration/conftest.py` added tomorrow would otherwise be a fixture source
    this guard cannot see — which is the whole shape of the hole it is closing.
    """
    found: list[Path] = []
    directory = path.parent
    while True:
        candidate = directory / "conftest.py"
        if candidate.is_file():
            found.append(candidate)
        if directory == TESTS or directory.parent == directory:
            break
        directory = directory.parent
    return tuple(found)


#: Fixtures pytest and its bundled plugins supply. Listed so that a parameter this walk
#: cannot resolve is a real signal — a fixture source outside the reach of the static walk
#: — rather than noise from `tmp_path`.
#:
#: Read by `_unresolved` **only**. The walk itself resolves every name first and reaches a
#: local definition wherever there is one, so a module that shadows one of these is walked
#: rather than waved through.
PYTEST_FIXTURES = frozenset(
    {
        "cache",
        "capfd",
        "capfdbinary",
        "caplog",
        "capsys",
        "capsysbinary",
        "capteesys",
        "doctest_namespace",
        "monkeypatch",
        "pytestconfig",
        "pytester",
        "record_property",
        "record_testsuite_property",
        "record_xml_attribute",
        "recwarn",
        "request",
        "testdir",
        "tmp_path",
        "tmp_path_factory",
        "tmpdir",
        "tmpdir_factory",
    }
)


def _fixtures_in(parsed: Parsed, scope: str) -> dict[str, Unit]:
    return {
        unit.label[len(scope) :]: unit
        for unit in parsed.units
        if unit.is_fixture and unit.scope == scope
    }


def _functions_in(parsed: Parsed) -> dict[str, Unit]:
    return {unit.label: unit for unit in parsed.units if unit.scope == ""}


def _scopes(parsed: Parsed, unit: Unit) -> list[tuple[Parsed, str]]:
    """Where a fixture name requested by *unit* is looked up, in pytest's own order.

    Class body, then the module, then each enclosing `conftest` nearest first. A fixture
    that itself requests a fixture is resolved from **its own** module's chain, not the
    test's — a `conftest` fixture cannot see one defined in a test module.
    """
    chain: list[tuple[Parsed, str]] = []
    if unit.scope:
        chain.append((parsed, unit.scope))
    chain.append((parsed, ""))
    chain.extend((_parsed(conftest), "") for conftest in _conftests(parsed.path))
    return chain


def _resolve_fixture(parsed: Parsed, unit: Unit, name: str) -> tuple[Parsed, Unit] | None:
    for owner, scope in _scopes(parsed, unit):
        fixtures = _fixtures_in(owner, scope)
        if name in fixtures:
            return owner, fixtures[name]
    return None


def _autouse_for(parsed: Parsed, unit: Unit) -> list[tuple[Parsed, Unit]]:
    """Every `autouse` fixture that applies to *unit*, from its class out to `conftest`."""
    found: list[tuple[Parsed, Unit]] = []
    for owner, scope in _scopes(parsed, unit):
        found.extend(
            (owner, fixture)
            for fixture in _fixtures_in(owner, scope).values()
            if fixture.autouse
        )
    return found


def _resolve_helpers(parsed: Parsed, unit: Unit) -> list[tuple[Parsed, Unit]]:
    """The functions *unit* calls by name: same module, `conftest`-imported, or own class.

    A helper is the same hole as a fixture with none of the pytest machinery — `_borrow()`
    four lines above the write it feeds is exactly the shape `test_news_fetch_write.py`
    had.
    """
    found: list[tuple[Parsed, Unit]] = []
    local = _functions_in(parsed)
    for name in unit.calls:
        if name in local:
            found.append((parsed, local[name]))
            continue
        if name not in parsed.from_conftest:
            continue
        for conftest in _conftests(parsed.path):
            other = _parsed(conftest)
            if name in _functions_in(other):
                found.append((other, _functions_in(other)[name]))
                break
    if unit.scope:
        siblings = {sibling.label: sibling for sibling in parsed.units}
        found.extend(
            (parsed, siblings[f"{unit.scope}{name}"])
            for name in unit.methods
            if f"{unit.scope}{name}" in siblings
        )
    return found


@dataclass(frozen=True)
class Fragment:
    """A piece of a unit's surface, with the module that piece belongs to.

    `docstrings` and `imported` travel with it: a fixture in `conftest` is judged against
    `conftest`'s own imports, so a name that is production code there does not become one
    in the test module that borrows the fixture, or the other way round.
    """

    origin: str
    nodes: tuple[ast.AST, ...]
    docstrings: frozenset[int]
    imported: frozenset[str]


def _reachable(parsed: Parsed, unit: Unit) -> list[tuple[Parsed, Unit]]:
    """Everything *unit* reaches: its body, its fixtures, their fixtures, its helpers.

    Breadth-first with a visited set keyed on `(module, label)`, so a fixture two tests
    share is walked once per test and a cycle terminates. One walk, used by both the
    fragment view below and the unresolved-name view: two copies of it would make a
    mutation in either one invisible.
    """
    seen = {(parsed.path, unit.label)}
    reached = [(parsed, unit)]
    queue = list(reached)

    if unit.is_test:
        queue.extend(_admit(_autouse_for(parsed, unit), seen, reached))

    while queue:
        owner, current = queue.pop()
        neighbours: list[tuple[Parsed, Unit]] = []
        if current.takes_fixtures:
            # Every name, including one that shadows a pytest builtin: `PYTEST_FIXTURES`
            # excuses a name from the *ratchet* below, and excusing it here too would
            # make a local `@pytest.fixture def tmp_path` a place to hide a borrow.
            neighbours.extend(
                found
                for name in (*current.params, *current.usefixtures)
                for found in [_resolve_fixture(owner, current, name)]
                if found is not None
            )
        neighbours.extend(_resolve_helpers(owner, current))
        queue.extend(_admit(neighbours, seen, reached))

    return reached


def _admit(
    candidates: list[tuple[Parsed, Unit]],
    seen: set[tuple[Path, str]],
    reached: list[tuple[Parsed, Unit]],
) -> list[tuple[Parsed, Unit]]:
    fresh = []
    for owner, unit in candidates:
        key = (owner.path, unit.label)
        if key in seen:
            continue
        seen.add(key)
        reached.append((owner, unit))
        fresh.append((owner, unit))
    return fresh


def _surface(parsed: Parsed, unit: Unit) -> tuple[Fragment, ...]:
    """`_reachable`, flattened into the code the two checks below read."""
    return tuple(
        Fragment(
            origin="" if index == 0 else f"{owner.path.name}::{found.label}",
            nodes=found.nodes,
            docstrings=owner.docstrings,
            imported=owner.imported,
        )
        for index, (owner, found) in enumerate(_reachable(parsed, unit))
    )


def _unresolved(parsed: Parsed, unit: Unit) -> list[str]:
    """Fixture names *unit* and everything it reaches request but this walk cannot find.

    These are the surface the guard does **not** see: a fixture from a third-party plugin,
    one registered through `pytest_plugins`, one fetched by a computed name through
    `request.getfixturevalue`. There are none today and
    `test_every_fixture_a_dbdata_unit_requests_is_resolved` is what keeps it that way —
    a new one is a hole in this guard and has to be answered, not absorbed.
    """
    missing: list[str] = []
    for owner, reached in _reachable(parsed, unit):
        if not reached.takes_fixtures:
            continue
        missing.extend(
            f"{reached.label}({name})"
            for name in (*reached.params, *reached.usefixtures)
            if name not in PYTEST_FIXTURES
            and name not in reached.parametrized
            and _resolve_fixture(owner, reached, name) is None
        )
    return missing


def _strings(nodes: tuple[ast.AST, ...], docstrings: frozenset[int]) -> list[tuple[int, str]]:
    return [
        (node.lineno, node.value)
        for node in nodes
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in docstrings
    ]


def _writes(nodes: tuple[ast.AST, ...], strings: list[tuple[int, str]]) -> list[str]:
    found = [f"line {line}: {sql.strip()[:60]}" for line, sql in strings if WRITE_SQL.search(sql)]
    for node in nodes:
        if not isinstance(node, ast.Call):
            continue
        name = node.func.attr if isinstance(node.func, ast.Attribute) else (
            node.func.id if isinstance(node.func, ast.Name) else ""
        )
        if WRITE_CALL.match(name):
            found.append(f"line {node.lineno}: {name}(...)")
    return found


def _subjects(
    nodes: tuple[ast.AST, ...], strings: list[tuple[int, str]], imported: frozenset[str]
) -> list[str]:
    """Where a unit could have got a **real** row from.

    Two shapes, and the second is the general one rather than another literal: a bare call
    to a *function* this module imported from `fantabot`. Production code reading the
    canonical database is how a real row gets into a test, and `load_pool` was only the
    spelling in play. A constructor is excluded by its capital, because building the
    repository under test is the point of these files.
    """
    found = [
        f"line {line}: {sql.strip()[:60]}" for line, sql in strings if SUBJECT_SQL.search(sql)
    ]
    for node in nodes:
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in imported
            and not node.func.id[:1].isupper()
        ):
            found.append(f"line {node.lineno}: {node.func.id}(...)")
    return found


def _where(fragment: Fragment, found: list[str]) -> list[str]:
    """Tag a finding with the fixture or helper it came from, when it is not the body."""
    if not fragment.origin:
        return found
    return [f"{fragment.origin} {item}" for item in found]


def _judged(path: Path) -> list[tuple[str, tuple[Fragment, ...]]]:
    """The units in *path* that run against the canonical database — and never none.

    Each one comes with its whole surface. Both checks below iterate this rather than
    filtering `_units` themselves. One shared filter, asserted once: two copies of the
    same loop make a mutation in either one invisible, and with the tree clean a check
    that silently examines nothing reads exactly like a check that found nothing.
    """
    parsed = _parsed(path)
    judged = [
        (unit.label, _surface(parsed, unit)) for unit in parsed.units if unit.marked
    ]
    assert len(judged) >= 2, (
        f"{path.name} carries the `dbdata` marker but only {len(judged)} unit(s) resolved "
        "as running against the canonical database — the checks below would examine "
        "nothing and pass"
    )
    return judged


def _offences(path: Path) -> list[str]:
    """Units of *path* that take a real row somewhere in their surface and write somewhere
    in it. Lifted out of the assertion below so that a *positive* control can call it:
    an offence-finder that silently returned nothing would satisfy `== []` for ever, and
    that is the shape this whole file exists to refuse.
    """
    offences = []
    for label, fragments in _judged(path):
        subjects: list[str] = []
        writes: list[str] = []
        for fragment in fragments:
            strings = _strings(fragment.nodes, fragment.docstrings)
            subjects += _where(fragment, _subjects(fragment.nodes, strings, fragment.imported))
            writes += _where(fragment, _writes(fragment.nodes, strings))
        if subjects and writes:
            offences.append(f"{label}: took {subjects} and then wrote {writes}")
    return offences


def _whole_table_offences(path: Path) -> list[str]:
    """`DELETE`/`TRUNCATE`/`UPDATE` with no `WHERE`, anywhere in a judged unit's surface."""
    return [
        f"{label}, {fragment.origin or path.name} line {line}: {sql.strip()[:70]}"
        for label, fragments in _judged(path)
        for fragment in fragments
        for line, sql in _strings(fragment.nodes, fragment.docstrings)
        if WHOLE_TABLE_SQL.search(sql) and not re.search(r"\bWHERE\b", sql, re.I)
    ]


@pytest.mark.parametrize("path", DBDATA_MODULES, ids=lambda p: p.name)
def test_no_dbdata_unit_takes_a_real_row_as_its_subject(path: Path) -> None:
    """Read the seed all you like; do not write to what you read back.

    This is the shape the substring guard could not see. `test_news_fetch_write.py`'s
    `_already_stored_pool_player` — now `_a_pool_of_two_with_the_canary_already_stored`,
    which fakes the pool reader instead — took `load_pool(session, "2026/27")[0].id`, a
    real pool player, and then `upsert_rows(..., force=True)` and a hard `DELETE`,
    committed through `database_manager.get_session()` rather than rolled back. Only the
    1900-01-01 run day kept it off a real reading, and a mitigation is not a guard.

    The two halves are looked for across the unit's **surface**, not its body: a borrow in
    a fixture the test requests and a `DELETE` in the test is the same evening's traffic
    against Postgres as both in one function.
    """
    offences = _offences(path)

    assert offences == [], (
        f"{path.name} runs against the canonical database (`dbdata`) and these units take "
        f"a real row as a subject and write to it:\n  " + "\n  ".join(offences) + "\n"
        "Take the subject from `make_synthetic_players` (tests/conftest.py) instead — a "
        "synthetic id has no `quotazioni` row, so no real reading can share its key."
    )


@pytest.mark.parametrize("path", DBDATA_MODULES, ids=lambda p: p.name)
def test_no_dbdata_module_writes_to_a_whole_table(path: Path) -> None:
    """A `DELETE`, `TRUNCATE` or `UPDATE` with no `WHERE`, against the 614k-row seed.

    The rollback is real and it is not the point: it makes the blast radius survivable,
    not intended. `DELETE FROM match_grain` is ~50,000 recorded rows, and the only thing
    that decides whether they come back is whether the transaction reaches its teardown.

    Judged over the surface for the same reason as the check above, with one more: a
    whole-table statement in a `conftest` fixture belongs to every test that requests it,
    and `conftest.py` carries no `dbdata` marker of its own to be scanned under.
    """
    offences = _whole_table_offences(path)

    assert offences == [], (
        f"{path.name} runs against the canonical database (`dbdata`) and writes to whole "
        f"tables:\n  " + "\n  ".join(offences) + "\n"
        "Key the statement to the rows the test made, or move the test off `dbdata`."
    )


# --- the positive controls: the two checks above, shown catching ----------------------
#
# Both assert an empty list against a clean tree, so every way of *examining nothing*
# reads exactly like finding nothing: a `_surface` that stops at the `def`, a `_where`
# that drops what a fixture contributed, a `_judged` that resolves no unit. Each of these
# builds the offence and asserts it comes back named, through the fixture hop that the
# body-only version of this guard could not make.


def _sample(directory: Path, name: str, source: str) -> Path:
    path = directory / name
    path.write_text(source, encoding="utf-8")
    return path


SAMPLE_HEADER = """import pytest
from sqlalchemy import text

pytestmark = [pytest.mark.db, pytest.mark.dbdata]
"""


def test_a_borrow_in_a_fixture_is_caught(tmp_path: Path) -> None:
    """The refutation this widening answers, kept as a test.

    The borrow and the write are in different `def`s and the traffic to Postgres is
    identical to having both in one. The offence has to name `borrowed` — not just the
    test — or the message sends the reader to a function that does not contain the query.
    """
    path = _sample(
        tmp_path,
        "test_borrowed_in_a_fixture.py",
        SAMPLE_HEADER
        + '''

@pytest.fixture
def borrowed(db_session):
    return db_session.execute(
        text("SELECT player_id FROM asta_assignment ORDER BY player_id LIMIT 1")
    ).scalar_one()


def test_it(db_session, borrowed):
    db_session.execute(
        text("DELETE FROM player_sentiment WHERE player_id = :p"), {"p": borrowed}
    )
''',
    )

    (offence,) = _offences(path)

    assert offence.startswith("test_it:")
    assert "test_borrowed_in_a_fixture.py::borrowed" in offence
    assert "SELECT player_id FROM asta_assignment" in offence
    assert "DELETE FROM player_sentiment" in offence


def test_a_whole_table_write_in_an_enclosing_conftest_is_caught(tmp_path: Path) -> None:
    """The second check, through the hop it could not make before.

    `conftest.py` carries no `dbdata` marker of its own, so nothing ever scanned it; the
    fixture belongs to whichever marked test requests it, and that is where it is judged.
    """
    _sample(
        tmp_path,
        "conftest.py",
        '''import pytest
from sqlalchemy import text


@pytest.fixture
def wiped(db_session):
    db_session.execute(text("DELETE FROM player_sentiment"))
''',
    )
    path = _sample(
        tmp_path,
        "test_wiped_by_a_conftest.py",
        SAMPLE_HEADER
        + '''

def test_it(db_session, wiped):
    assert db_session
''',
    )

    (offence,) = _whole_table_offences(path)

    assert offence.startswith("test_it, conftest.py::wiped ")
    assert "DELETE FROM player_sentiment" in offence


def test_a_clean_sample_produces_no_offence(tmp_path: Path) -> None:
    """The other side of both controls: the same shapes, keyed to a synthetic id, pass.

    Without this the two above would be satisfied by a finder that flagged everything, and
    a guard that flags everything is one that gets deleted.
    """
    _sample(
        tmp_path,
        "conftest.py",
        '''import pytest
from sqlalchemy import text


@pytest.fixture
def wiped(db_session):
    db_session.execute(text("DELETE FROM player_sentiment WHERE player_id = 9100000000"))
''',
    )
    path = _sample(
        tmp_path,
        "test_clean.py",
        SAMPLE_HEADER
        + '''

@pytest.fixture
def subject(db_session):
    return 9100000000


def test_it(db_session, subject, wiped):
    db_session.execute(
        text("DELETE FROM player_sentiment WHERE player_id = :p"), {"p": subject}
    )
''',
    )

    assert _offences(path) == []
    assert _whole_table_offences(path) == []


# --- the boundary of the static walk, stated rather than assumed ---------------------


def test_the_conftest_chain_is_walked_and_reaches_the_root() -> None:
    """A `_conftests` that found nothing would make every check above body-only again.

    This is the non-vacuity of the widening itself: `db_session` is in `tests/conftest.py`
    and every `dbdata` test asks for it, so if the chain resolves that name the fixture
    surface is genuinely being followed.
    """
    for path in DBDATA_MODULES:
        chain = _conftests(path)
        assert TESTS / "conftest.py" in chain, f"{path.name} cannot see tests/conftest.py"

    parsed = _parsed(DBDATA_MODULES[0])
    tests = [unit for unit in parsed.units if unit.is_test and "db_session" in unit.params]
    assert tests, f"{parsed.path.name} has no test taking `db_session`"

    resolved = _resolve_fixture(parsed, tests[0], "db_session")
    assert resolved is not None and resolved[0].path == TESTS / "conftest.py"

    # and the chain it pulls behind it, which is where a whole-table statement would hide
    surface = {fragment.origin for fragment in _surface(parsed, tests[0])}
    assert {
        "conftest.py::db_session",
        "conftest.py::db_connection",
        "conftest.py::db_engine",
        "conftest.py::_no_sockets",
    } <= surface, surface


@pytest.mark.parametrize("path", DBDATA_MODULES, ids=lambda p: p.name)
def test_every_fixture_a_dbdata_unit_requests_is_resolved(path: Path) -> None:
    """The ratchet on what this guard cannot see.

    Resolving fixtures statically is sound only for the sources the walk knows: the test
    module, its classes, the enclosing `conftest` chain, and pytest's own builtins. It is
    **not** sound for a fixture from a third-party plugin, one registered through
    `pytest_plugins`, or one fetched by a computed name through `request.getfixturevalue`
    — in each of those the definition is not reachable from the request site.

    There are none in the `dbdata` tier today. This fails the moment there is one, which
    is the honest form of that boundary: an unreachable fixture is a place a borrow could
    be hidden again, and it has to be answered rather than absorbed.
    """
    parsed = _parsed(path)
    missing = sorted(
        {name for unit in parsed.units if unit.marked for name in _unresolved(parsed, unit)}
    )

    assert missing == [], (
        f"{path.name} requests fixtures this walk cannot resolve: {missing}. "
        "The guard cannot read what such a fixture does, so a borrow inside one is "
        "invisible. Define it in the module or the conftest chain, or widen the walk."
    )


def test_no_dbdata_module_reaches_a_fixture_by_a_computed_name() -> None:
    """`request.getfixturevalue("x")` and `pytest_plugins` are the two ways a fixture
    arrives without being named in a signature or a `usefixtures`.

    Both are legal pytest and neither is reachable from a `def`, so they are refused in
    this tier rather than silently unwalked.
    """
    offenders = [
        f"{path.name}: {call}"
        for path in (*DBDATA_MODULES, *{c for p in DBDATA_MODULES for c in _conftests(p)})
        for call in ("getfixturevalue", "pytest_plugins")
        if call in path.read_text(encoding="utf-8")
    ]

    assert offenders == [], (
        f"a fixture arrives by a computed name: {offenders}. The static walk in this file "
        "cannot follow it, so what that fixture does to the canonical database is unjudged."
    )




#: Every way pytest hands a test something, in one module, so the walk that follows them
#: is tested rather than trusted. Each wiring here is one of the five shapes the reviewer
#: moved a borrow into and watched slip through the body-only guard.
SURFACE_SAMPLE = '''
import pytest


@pytest.fixture
def db_row():
    ...


@pytest.fixture
def borrowed(db_row):
    ...


@pytest.fixture(autouse=True)
def _always():
    ...


@pytest.fixture
def _by_marker():
    ...


def helper():
    ...


def _never_called():
    ...


class TestThing:
    @pytest.fixture
    def chained(self, borrowed):
        ...

    @pytest.mark.usefixtures("_by_marker")
    @pytest.mark.parametrize("value", [1, 2])
    def test_it(self, chained, value):
        helper()
        self.sibling()

    def sibling(self):
        ...
'''


def test_the_surface_walk_follows_every_wiring_pytest_offers(tmp_path: Path) -> None:
    """The edges, one assertion, so a mutation to any of them shows up here.

    A set rather than a subset on purpose, and `_never_called` is in the sample for that
    reason: a walk that folded in the whole module would also pass a subset check, and
    would make every test in a file offend as soon as any one function in it wrote.
    """
    sample = tmp_path / "test_sample.py"
    sample.write_text(SURFACE_SAMPLE, encoding="utf-8")
    parsed = _parsed(sample)
    (unit,) = [found for found in parsed.units if found.label == "TestThing.test_it"]

    assert {fragment.origin for fragment in _surface(parsed, unit)} == {
        "",  # its own body
        "test_sample.py::TestThing.chained",  # a class fixture it names
        "test_sample.py::borrowed",  # which that fixture names, in the module
        "test_sample.py::db_row",  # and which *that* one names
        "test_sample.py::_always",  # autouse, named nowhere
        "test_sample.py::_by_marker",  # named in `usefixtures`, not in the signature
        "test_sample.py::helper",  # a plain call
        "test_sample.py::TestThing.sibling",  # a call through `self`
    }

    # `value` comes from `parametrize`: a value, with nothing behind it to walk into, and
    # so neither part of the surface nor a hole in it.
    assert unit.parametrized == ("value",)
    assert _unresolved(parsed, unit) == []


def test_a_fixture_with_no_definition_in_reach_is_reported(tmp_path: Path) -> None:
    """The other side of the ratchet, so `_unresolved` cannot go green by going empty.

    `test_every_fixture_a_dbdata_unit_requests_is_resolved` asserts an empty list against a
    clean tree, which a `_unresolved` that always returned `[]` would satisfy for ever.
    Here a fixture with no definition anywhere in reach — a plugin's, or one registered
    through `pytest_plugins` — has to come back named, and through a *fixture* as well as
    a test, because that is the hop the ratchet has to make to be worth anything.
    """
    sample = tmp_path / "test_unreachable.py"
    sample.write_text(
        "import pytest\n\n\n"
        "@pytest.fixture\n"
        "def local(from_some_plugin):\n    ...\n\n\n"
        "def test_it(local, monkeypatch):\n    ...\n",
        encoding="utf-8",
    )
    parsed = _parsed(sample)
    (unit,) = [found for found in parsed.units if found.label == "test_it"]

    assert _unresolved(parsed, unit) == ["local(from_some_plugin)"]


def test_a_fixture_shadowing_a_pytest_builtin_is_still_walked(tmp_path: Path) -> None:
    """`PYTEST_FIXTURES` excuses a name from the ratchet, never from the walk.

    A module is free to define `@pytest.fixture def tmp_path`, and pytest then uses that
    one. Skipping the name because it is on the builtin list would make such a fixture the
    one place in the tier a borrow could still sit unread.
    """
    sample = tmp_path / "test_shadowed.py"
    sample.write_text(
        "import pytest\n\n\n"
        "@pytest.fixture\n"
        "def tmp_path():\n    ...\n\n\n"
        "def test_it(tmp_path):\n    ...\n",
        encoding="utf-8",
    )
    parsed = _parsed(sample)
    (unit,) = [found for found in parsed.units if found.label == "test_it"]

    assert "tmp_path" in PYTEST_FIXTURES
    assert {fragment.origin for fragment in _surface(parsed, unit)} == {
        "",
        "test_shadowed.py::tmp_path",
    }


def test_usefixtures_on_a_pytestmark_reaches_every_test_under_it(tmp_path: Path) -> None:
    """The same marker one level up, where it names no `def` at all.

    `pytestmark = [pytest.mark.usefixtures(...)]` at module or class level applies to every
    test below it, and a decorator-only reading of the marker sees none of them — the same
    hole as the fixture, one scope out.
    """
    sample = tmp_path / "test_marked.py"
    sample.write_text(
        "import pytest\n\n"
        "pytestmark = [pytest.mark.dbdata, pytest.mark.usefixtures(\"_module_wide\")]\n\n\n"
        "@pytest.fixture\n"
        "def _module_wide():\n    ...\n\n\n"
        "@pytest.fixture\n"
        "def _class_wide():\n    ...\n\n\n"
        "class TestIt:\n"
        "    pytestmark = pytest.mark.usefixtures(\"_class_wide\")\n\n"
        "    def test_it(self):\n        ...\n",
        encoding="utf-8",
    )
    parsed = _parsed(sample)
    (unit,) = [found for found in parsed.units if found.label == "TestIt.test_it"]

    assert unit.usefixtures == ("_module_wide", "_class_wide")
    assert {fragment.origin for fragment in _surface(parsed, unit)} == {
        "",
        "test_marked.py::_module_wide",
        "test_marked.py::_class_wide",
    }


def test_an_indirect_parametrize_argname_is_still_a_fixture() -> None:
    """`indirect=` turns the argname back into a fixture request.

    Excluding it would be the narrowing this file exists to refuse: the name then *does*
    have a definition, and a borrow could sit in it.
    """
    plain = ast.parse("@pytest.mark.parametrize('listone', ['mantra'])\ndef f(listone): ...")
    indirect = ast.parse(
        "@pytest.mark.parametrize('listone', ['mantra'], indirect=True)\ndef f(listone): ..."
    )

    assert _parametrized(plain.body[0].decorator_list) == ("listone",)  # type: ignore[attr-defined]
    assert _parametrized(indirect.body[0].decorator_list) == ()  # type: ignore[attr-defined]


# --- one synthetic-id convention, and the band it reserves ---------------------------


def test_the_ids_a_test_names_for_itself_cannot_land_in_the_fixture_range() -> None:
    """Three conventions had grown up: `9_000_000_001`, `999_999_999` and nine `999_00N`.

    The last is six digits, inside the range a real `players.id` reaches — which is the
    whole reason `SYNTHETIC_PLAYER_BASE` sits at 9.1e9. They are one maker now, and the
    split inside it is load-bearing: `make_synthetic_players` allocates offsets 0..999 and
    its rows are rolled back, while `synthetic_id` is for the rows a test **commits** and
    for ids that must not resolve at all. An overlap would put a committed row where a
    rolled-back test believes it made its own.
    """
    from conftest import SYNTHETIC_FIXTURE_RANGE, SYNTHETIC_PLAYER_BASE, synthetic_id

    assert synthetic_id(SYNTHETIC_FIXTURE_RANGE) == SYNTHETIC_PLAYER_BASE + SYNTHETIC_FIXTURE_RANGE

    with pytest.raises(ValueError, match="hands out"):
        synthetic_id(SYNTHETIC_FIXTURE_RANGE - 1)


def test_the_fixture_maker_cannot_run_past_its_own_band() -> None:
    """The other half of the same fence, so the band is enforced from both sides rather
    than asserted in a comment."""
    from conftest import SYNTHETIC_FIXTURE_RANGE, make_synthetic_players

    with pytest.raises(ValueError, match="run past offset"):
        make_synthetic_players(None, SYNTHETIC_FIXTURE_RANGE + 1)  # type: ignore[arg-type]


# --- the `db` tier writes to its own database (T2.6) ---------------------------------


def test_the_test_database_is_refused_when_it_is_the_canonical_one() -> None:
    """The guard is a hard failure, not a warning.

    Deleting the compose stack removed the tier's separate database. Without this, the
    default DSN — now the app's canonical `fantabot` — would silently receive 149
    write-marked tests. `canary_player` in `test_news_fetch_write.py` records what that
    already cost once.
    """
    from conftest import CanonicalDatabaseError, refuse_canonical

    canonical = "postgresql+psycopg2://postgres:@/fantabot?host=/tmp/pg"

    with pytest.raises(CanonicalDatabaseError) as caught:
        refuse_canonical(canonical, canonical)

    assert "fantabot" in str(caught.value)
    assert "FANTABOT_TEST_DATABASE_URL" in str(caught.value)


def test_a_separate_database_on_the_same_server_is_allowed() -> None:
    """Same socket, different database — which is exactly what `db create` makes."""
    from conftest import refuse_canonical

    test_url = "postgresql+psycopg2://postgres:@/fantabot_test?host=/tmp/pg"

    assert refuse_canonical(test_url, "postgresql+psycopg2://postgres:@/fantabot?host=/tmp/pg") == (
        test_url
    )


def test_the_refusal_is_by_database_name_alone() -> None:
    """Deliberately over-broad: a database called `fantabot` on another host is refused
    too. Fail closed — the cost of a false refusal is one environment variable, and the
    cost of a false pass is a week of readings."""
    from conftest import CanonicalDatabaseError, refuse_canonical

    with pytest.raises(CanonicalDatabaseError):
        refuse_canonical(
            "postgresql+psycopg2://postgres:@elsewhere.test:5432/fantabot",
            "postgresql+psycopg2://postgres:@/fantabot?host=/tmp/pg",
        )
