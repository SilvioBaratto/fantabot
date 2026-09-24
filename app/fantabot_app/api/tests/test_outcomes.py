"""Four failures, four screens — and the `except Exception` that made them one.

`except Exception -> found=False` sat on `/asta/plan`, `/lineup/plan` and
`/asta/target-prices`. A database that would not open, a season nobody had scraped, a
roster the optimizer could not seed, a lega that had never been synced and a corpus shape
with no recorded sales all rendered as **"No plan yet"**, under a suggestion to sync the
lega — the right remedy for one of them and a wasted evening for the other four.

`/lineup/plan` looked solved and was not: it already had a `reason`, and every failure
produced the *same* one.

The rule these three now keep is written down in `api/outcomes.py`: **degrade open on a
status read, fail closed on a decision.** Since 2026-09-24 it is kept by six — every
endpoint module that imports from `api/outcomes.py`, discovered rather than listed, which
is how `room.py` (the file that module calls the model, and the last one holding a bare
handler) came under the ban it was the example for.

The discovery reads **all four spellings of an import**, and its result is compared to
`PINNED_DECISION_ROUTES` for equality. Both are repairs of the same defect one level down:
a walk that read two spellings let a new route escape the ban, and a `<=` pin over four of
the six names let a route leave the scan without a single test going red.

Two properties are asserted here, and the second is the one that rots without a test:

* Each induced failure produces a **different** outcome.
* The tuple of outcomes a route can return is **exactly** the pinned one — compared for
  equality, so a route that gains an outcome must say so and one that loses an outcome must
  delete its name. A set that only ever grows stops meaning anything.

**Every list on that path is now discovered, and the last two were the same defect twice.**
The routes were a hand-written list beside a working discovery, which is what let `room.py`
escape the ban it was the example for. The *models* were a hand-written list beside a
working discovery too, and measured 2026-09-24 it held five of nine: `teams.py`'s two and
`lineup.py::SubmitResult` — the route that puts an XI on the live platform — were absent,
and nothing went red, because a parametrization that omits a case omits its failures with
it. Only `PINNED_OUTCOMES`' *values* are written by hand now, and its keys are compared to
the discovery for equality.

**And the ban's own matcher read one of five spellings of the thing it bans.** It tested
`isinstance(handler.type, ast.Name)`, so `except (Exception,)`, `except (Exception,
OSError)`, `except builtins.Exception` and — the strongest of the four — a plain `except:`
all walked past it. The last catches `KeyboardInterrupt` and `SystemExit` as well, making
it strictly worse than the construct the ban is named for and the one spelling of it the
ban could not see at all; the second is what somebody writes when they *narrow* a handler
in answer to this very ban, and it catches exactly what `except Exception` catches.
`_caught_names` reads all five, and the superset is enumerated over a generated corpus
rather than claimed.
"""

from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace
from typing import Any, NamedTuple, Self

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError, SQLAlchemyError

from fantabot_app.api.main import app
from fantabot_app.api.outcomes import (
    ASTA_ADVISORY_OUTCOMES,
    ASTA_PLAN_OUTCOMES,
    LINEUP_CURRENT_OUTCOMES,
    LINEUP_PLAN_OUTCOMES,
    TARGET_PRICES_OUTCOMES,
    because,
)
from fantabot_app.api.v1.endpoints.lineup import SUBMIT_OUTCOMES
from fantabot_app.api.v1.endpoints.room import OUTCOMES as ROOM_OUTCOMES
from fantabot_app.api.v1.endpoints.teams import BACKFILL_OUTCOMES, SNAPSHOT_OUTCOMES

ENDPOINTS = Path(__file__).resolve().parent.parent / "v1" / "endpoints"


#: The module a decision route imports its vocabulary from, named by its **final
#: component**. The discovery below matches on that alone — `_imports_outcomes` says why
#: the loose direction is the safe one.
OUTCOMES_MODULE = "outcomes"


def _referenced_modules(tree: ast.Module) -> set[str]:
    """Every module name this tree's imports name, in every spelling of an import.

    Four spellings reach one module. Walking `ast.ImportFrom` and reading `node.module`
    saw two of them:

    | spelling                                  | node                         | seen before |
    |-------------------------------------------|------------------------------|-------------|
    | `from fantabot_app.api.outcomes import x` | `ImportFrom(module="a.b.c")` | yes         |
    | `from .outcomes import x`                 | `ImportFrom(level > 0)`      | yes         |
    | `from fantabot_app.api import outcomes`   | `ImportFrom(module="a.b")`   | **no**      |
    | `import fantabot_app.api.outcomes`        | `ast.Import`                 | **no**      |

    The last two are the hole this closes. A scratch decision route spelled either way,
    holding a bare `except Exception`, ran inside the whole api suite on 2026-09-24 for
    **414 passed, nothing red** — the ban's stated goal ("a new decision route is covered
    by default") unmet for every spelling but the two in the first rows. Restoring the old
    predicate under the enumeration below still fails seven of its ten positive rows.

    A relative level is kept as leading dots rather than resolved against the endpoints
    package. Resolution can only *lose* a name — `from .outcomes import x` resolves to a
    module that does not exist, and dropping it would narrow the very set this widens —
    and the only thing asked of these strings is their final component.
    """
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            # `import a.b.c` and `import a.b.c as d` both carry the dotted path in `name`.
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = "." * node.level + (node.module or "")
            names.add(base)
            prefix = base if base.endswith(".") else f"{base}."
            # `from <pkg> import <name>` may be importing the *module* `<pkg>.<name>`, and
            # that is the spelling `room.py`'s neighbours are one refactor away from.
            names.update(
                f"{prefix}{alias.name}" for alias in node.names if alias.name != "*"
            )
    return names


def _imports_outcomes(tree: ast.Module) -> bool:
    """Does this module import `api/outcomes.py`?

    True when any name its imports reference ends in a component called `outcomes`.

    **A strict superset of the equality it replaces, and the proof is mechanical.** The old
    test was `module.endswith("api.outcomes") or (node.level and module == "outcomes")`
    over `ast.ImportFrom` only. Both arms imply the referenced name's final component is
    `outcomes` — the first because ending in `".outcomes"` *is* having `outcomes` last, the
    second by equality — and `_referenced_modules` collects every string the old code read
    (`node.module`, with the level restored as dots) plus two more kinds. So every tree the
    old form matched, this matches; `test_the_discovery_reads_every_spelling_of_the_import`
    enumerates both sets rather than asserting it.

    Deliberately loose in the other direction. Matching *any* module whose last component
    is `outcomes` could pull in an unrelated one, and what that costs is a route being held
    to a rule it should be held to anyway. What being narrow costs is a route escaping the
    ban — which is the defect this replaces, now for the second time.
    """
    return any(
        name.rpartition(".")[2] == OUTCOMES_MODULE for name in _referenced_modules(tree)
    )


def _decision_routes() -> tuple[str, ...]:
    """Every endpoint module that imports from `api/outcomes.py`, discovered at import.

    Sorted, so the parametrization ids are stable and a diff of them is readable.
    """
    return tuple(
        path.name
        for path in sorted(ENDPOINTS.glob("*.py"))
        if _imports_outcomes(ast.parse(path.read_text(encoding="utf-8")))
    )


#: The routes the bare-`except` ban covers. A value, not a list somebody maintains — see
#: `TestTheBanCoversEveryRouteThatNamesAnOutcome` for what the hand-written one cost.
DECISION_ROUTES = _decision_routes()

#: The same set, named. `DECISION_ROUTES` is *discovered*; this is the ratchet it is
#: compared against, and the comparison is `==`, so the discovered set cannot shrink either.
#:
#: The `<=` this replaces held four of the six names, and that was the second half of the
#: same defect. Measured 2026-09-24: respelling `exclusions.py`'s import as
#: `from fantabot_app.api import outcomes` took the api suite from **414 passed to 412
#: passed with nothing red** — two parametrized cases simply stopped existing. A guard
#: whose own input set can shrink in silence is not a guard, and both of the tests below
#: are parametrized over this one.
#:
#: A new decision route therefore fails this file on the day it is written. That is the
#: cost and it is the point: adding the name here is the moment somebody reads the rule.
PINNED_DECISION_ROUTES = frozenset(
    {"asta.py", "exclusions.py", "lineup.py", "pricing.py", "room.py", "teams.py"}
)


def _callee_name(func: ast.expr) -> str | None:
    """What a call's callee is called, by final component: `models.RoomCheck` -> `RoomCheck`.

    One reader, because the model discovery and the outcome scan have to agree on what
    counts as a call to a model. Two copies of that answer is the defect this file exists
    to keep out of the routes themselves.
    """
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _models_naming_outcomes(tree: ast.Module) -> set[str]:
    """Every model in one endpoint module that has an `outcome` at all.

    Two sources, unioned, because either alone has a hole:

    * a class defined here whose body annotates a field called `outcome` — the
      declaration, which is there even before the first construction site;
    * a callee passed `outcome=` — a model declared somewhere else and merely built here.

    Loose in the same direction as everything else in this file: a helper that happens to
    take an `outcome=` keyword would be discovered and would then have to be pinned or
    explained. What that costs is somebody reading the rule. What being narrow costs is a
    model returning names nobody compared to a tuple, which is what
    `TestTheRoutesReturnOnlyWhatTheyPin`'s hand-written list cost for three models at once.
    """
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            if any(
                isinstance(stmt, ast.AnnAssign)
                and isinstance(stmt.target, ast.Name)
                and stmt.target.id == "outcome"
                for stmt in node.body
            ):
                found.add(node.name)
        elif isinstance(node, ast.Call) and any(
            keyword.arg == "outcome" for keyword in node.keywords
        ):
            called = _callee_name(node.func)
            if called is not None:
                found.add(called)
    return found


def _outcome_models() -> tuple[tuple[str, str], ...]:
    """Every `(route, model)` under the ban, discovered — the parametrization below.

    Sorted, so the ids are stable and a diff of them is readable.
    """
    return tuple(
        sorted(
            (filename, model)
            for filename in DECISION_ROUTES
            for model in _models_naming_outcomes(
                ast.parse((ENDPOINTS / filename).read_text(encoding="utf-8"))
            )
        )
    )


#: What the equality test below runs over. Discovered from `DECISION_ROUTES`, which is
#: itself discovered — so a new model on a new route is covered the day it is written.
OUTCOME_MODELS = _outcome_models()

#: The tuple each discovered model is compared against, and the **only** hand-written thing
#: left on this path. Its keys are compared to `OUTCOME_MODELS` for equality, so it cannot
#: be incomplete in silence — which the list it replaces was.
#:
#: That list was five `(file, model, tuple)` triples written out beside a discovery that
#: found six routes, and measured 2026-09-24 it was missing three models:
#: `teams.py`'s two, and `lineup.py::SubmitResult` — the route that submits an XI to the
#: live platform. All three *were* pinned, in `test_team_maintenance_routes.py` and
#: `test_lineup_submit_route.py`, each with its own copy of this scan; what was missing was
#: anything that would notice a fourth. A hand-written list beside a working discovery is
#: the defect, whether or not the names it omits happen to be covered somewhere else.
#:
#: The tuples are imported, never re-typed: `room.py`, `lineup.py` and `teams.py` declare
#: theirs beside the route rather than in `api/outcomes.py`, and a literal copied here
#: would be a second answer that can disagree with the first.
PINNED_OUTCOMES: dict[tuple[str, str], tuple[str, ...]] = {
    ("asta.py", "AstaAdvisory"): ASTA_ADVISORY_OUTCOMES,
    ("asta.py", "AstaPlan"): ASTA_PLAN_OUTCOMES,
    ("lineup.py", "CurrentLineup"): LINEUP_CURRENT_OUTCOMES,
    ("lineup.py", "LineupPlan"): LINEUP_PLAN_OUTCOMES,
    ("lineup.py", "SubmitResult"): SUBMIT_OUTCOMES,
    ("pricing.py", "TargetPricesReport"): TARGET_PRICES_OUTCOMES,
    ("room.py", "RoomCheck"): ROOM_OUTCOMES,
    ("teams.py", "BackfillResult"): BACKFILL_OUTCOMES,
    ("teams.py", "TeamSnapshotResult"): SNAPSHOT_OUTCOMES,
}


class OutcomeScan(NamedTuple):
    """What one module's `<Model>(outcome=...)` calls said, and what defeated the read.

    `unreadable` is not an omission — it is a failure. A site the scan cannot fold is a
    site that narrows `named` in silence, and when the value it carried is also named at
    another site, even the `==` comparison below cannot see it go.
    """

    named: set[str]
    unreadable: list[str]


def outcomes_returned(filename: str, model: str) -> OutcomeScan:
    """`outcomes_in`, over one endpoint module read off disk.

    Module-level, and shared with `test_room.py`, which asks exactly this of `room.py`'s
    `RoomCheck`. Two copies of one scan are two answers that can disagree, and the
    hand-written route list this file replaced is what that costs.
    """
    source = (ENDPOINTS / filename).read_text(encoding="utf-8")
    return outcomes_in(filename, ast.parse(source), model)


def _bound_to(tree: ast.Module, model: str) -> set[str]:
    """Local names assigned straight from `model(...)` — `body = SubmitResult(...)`.

    Deliberately not type inference: one assignment statement whose value is the
    constructor, read in the same module. It is what lets `outcomes_in` follow
    `body.model_copy(update={"outcome": ...})` back to the model it copies.
    """
    bound: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            targets: list[ast.expr] = list(node.targets)
            value: ast.expr | None = node.value
        elif isinstance(node, ast.AnnAssign):
            targets, value = [node.target], node.value
        else:
            continue
        if not isinstance(value, ast.Call) or _callee_name(value.func) != model:
            continue
        bound.update(target.id for target in targets if isinstance(target, ast.Name))
    return bound


def _is_a_copy_of(receiver: ast.expr, model: str, bound: set[str]) -> bool:
    """Is `<receiver>.model_copy(...)` copying this model?

    Two receivers are resolvable: a name `_bound_to` saw assigned from the constructor, and
    the constructor itself, chained. Anything else — a call to a helper, an attribute, a
    subscript — is not attributable to a model at all, and attributing it to whichever
    model happens to be under the scan would be worse than not reading it.

    That is a real hole, and it is closed one level up rather than guessed at here:
    `test_no_outcome_literal_in_a_decision_route_escapes_every_scan` collects every
    `outcome` literal in the file by *any* shape and fails if the per-model scans together
    do not account for it. A literal the attribution cannot place therefore fails the
    suite instead of disappearing from a pinned tuple.
    """
    if isinstance(receiver, ast.Name):
        return receiver.id in bound
    if isinstance(receiver, ast.Call):
        return _callee_name(receiver.func) == model
    return False


def _outcome_literals(tree: ast.Module) -> set[str]:
    """Every string written as an `outcome` anywhere in one module, by any shape.

    Deliberately blind to which model it belongs to — that is the point. It is the total
    the per-model scans are checked against, so a site no model's scan can reach fails
    rather than vanishing.
    """
    found: set[str] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.keyword)
            and node.arg == "outcome"
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        ):
            found.add(node.value.value)
        elif isinstance(node, ast.Dict):
            for key, value in zip(node.keys, node.values, strict=True):
                if (
                    isinstance(key, ast.Constant)
                    and key.value == "outcome"
                    and isinstance(value, ast.Constant)
                    and isinstance(value.value, str)
                ):
                    found.add(value.value)
    return found


def _outcome_in_update(label: str, node: ast.Call) -> tuple[set[str], list[str]]:
    """What one `model_copy(...)` on a bound name says the outcome becomes.

    A copy with no `update=` keeps the outcome its constructor already named, and is
    therefore silent here rather than unreadable. Everything else that hides a key — a
    non-literal mapping, a `**` splat, a non-literal value — is a failure, on
    `OutcomeScan`'s rule: a site the scan cannot fold narrows `named` in silence.
    """
    named: set[str] = set()
    unreadable: list[str] = []
    if any(keyword.arg is None for keyword in node.keywords):
        return named, [
            (
                f"{label}:{node.lineno} model_copy(**...) splats its arguments — this "
                "scan cannot tell whether one of them is `update`"
            )
        ]
    for keyword in node.keywords:
        if keyword.arg != "update":
            continue
        mapping = keyword.value
        if not isinstance(mapping, ast.Dict):
            unreadable.append(
                f"{label}:{node.lineno} model_copy(update={ast.unparse(mapping)}) is not "
                "a dict literal"
            )
            continue
        for key, item in zip(mapping.keys, mapping.values, strict=True):
            if key is None:
                unreadable.append(
                    f"{label}:{node.lineno} model_copy(update=...) splats a mapping, "
                    "which may carry an `outcome` this scan cannot read"
                )
                continue
            if not (isinstance(key, ast.Constant) and key.value == "outcome"):
                continue
            if isinstance(item, ast.Constant) and isinstance(item.value, str):
                named.add(item.value)
            else:
                unreadable.append(f"{label}:{node.lineno} outcome={ast.unparse(item)}")
    return named, unreadable


def outcomes_in(label: str, tree: ast.Module, model: str) -> OutcomeScan:
    """Every literal passed as `outcome=` to `model(...)` in one parsed module.

    Split from the directory walk for the reason `test_acting_jobs.starts_in` was: a scan
    proved only against the tree it already passes on is a scan whose blind spots are
    exactly the ones nobody looked for. `test_the_outcome_scan_reads_every_shape_of_the_call`
    plants each shape below.

    Three shapes count as unreadable rather than absent, each of which the `ast.Name` +
    `ast.Constant` reader used to drop without a word:

    * `models.RoomCheck(...)` — an `ast.Attribute` callee. Read, not skipped.
    * `outcome=SOME_CONSTANT` — anything that is not a string literal.
    * a call with no `outcome=` at all. `AstaPlan`, `LineupPlan` and `TargetPricesReport`
      all **default** `outcome` (`"planned"`, `"planned"`, `"priced"`), so a site that
      omits it returns an outcome the scan never saw.

    And a fourth shape counts as *present*, because ignoring it was a silent undercount:
    `body.model_copy(update={"outcome": "..."})`, where `body` was assigned from this
    model's own constructor in this module. `lineup.py` builds one `SubmitResult` and then
    returns five copies of it, so a reader of constructors alone found **four** of
    `SUBMIT_OUTCOMES`' seven names — and `no_matchday`, `not_armed` and
    `all_modules_refused`, the three a dry run and a closed matchday actually render, were
    invisible. Attribution is one assignment statement read in one module (`_bound_to`),
    not type inference: a name bound to something else would only widen the set a model is
    held to, which is the loose direction this file takes everywhere.
    """
    named: set[str] = set()
    unreadable: list[str] = []
    bound = _bound_to(tree, model)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        called = _callee_name(node.func)
        if (
            called == "model_copy"
            and isinstance(node.func, ast.Attribute)
            and _is_a_copy_of(node.func.value, model, bound)
        ):
            named_here, unreadable_here = _outcome_in_update(label, node)
            named |= named_here
            unreadable += unreadable_here
            continue
        if called != model:
            continue

        keywords = [kw for kw in node.keywords if kw.arg == "outcome"]
        if not keywords:
            unreadable.append(
                f"{label}:{node.lineno} {model}(...) passes no `outcome=` — it returns "
                "the field's default, which this scan cannot see"
            )
            continue
        for keyword in keywords:
            value = keyword.value
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                named.add(value.value)
            else:
                unreadable.append(
                    f"{label}:{node.lineno} outcome={ast.unparse(value)}"
                )
    return OutcomeScan(named, unreadable)


#: The two names that catch everything one of these routes can raise. A bare `except:`
#: names neither and is worse than both — it is `BaseException` without saying so, and it
#: is the one spelling of the construct this ban is named for.
CATCH_ALL_NAMES = frozenset({"Exception", "BaseException"})


def _caught_names(node: ast.expr) -> set[str]:
    """Every exception class one `except` type expression names, by final component.

    Four kinds of node can carry that name, and `isinstance(handler.type, ast.Name)` read
    one of them. The fifth spelling of the ban's own construct has no type expression at
    all and is `_catches_everything`'s first clause.

    | spelling                               | node            | read before |
    |----------------------------------------|-----------------|-------------|
    | `except Exception`                     | `ast.Name`      | yes         |
    | `except builtins.Exception`            | `ast.Attribute` | **no**      |
    | `except (Exception,)`                  | `ast.Tuple`     | **no**      |
    | `except (Exception, OSError)`          | `ast.Tuple`     | **no**      |
    | `except (*(Exception, OSError), Value)`| `ast.Starred`   | **no**      |
    | `except:`                              | `None`          | **no**      |

    `except (Exception, OSError)` is the one to expect in the wild: it is the natural thing
    to write when somebody *narrows* a handler in answer to this very ban, and it behaves
    exactly as `except Exception` does — `OSError` is already a subclass, so the tuple
    catches everything the first name does. A ban that reads it as a narrowing is a ban
    that rewards the appearance of a fix.

    A tuple nests, so the walk recurses. At **runtime** a nested tuple does not catch:
    CPython 3.11 raises `TypeError: catching classes that do not inherit from BaseException
    is not allowed` at match time, measured in this venv. That makes it broken code rather
    than a working bare handler — and this scan flags it either way, because a hole in the
    reader is the thing being closed and "it would have crashed anyway" is not a reason to
    keep one.

    A `Starred` element unpacks whatever it is given, so the walk recurses into it and
    finds `Exception` only when it is written out there too — `except (*(Exception,
    OSError), ValueError)`. A list display unpacks the same way (`except (*[Exception],
    ValueError)` catches everything at runtime, measured in this venv), so `ast.List`
    recurses beside `ast.Tuple`; a bare list is not a valid handler type, but a starred one
    builds a perfectly ordinary tuple.

    That is the limit, and it is the same limit `except FAMILIES` has: an expression that
    is not a name, an attribute or a display of them names nothing this reader can resolve
    and contributes nothing. A handler built by a call or a variable is invisible here, and
    no such handler exists in `endpoints/`; if one is written,
    `test_each_covered_route_has_something_to_find` still sees the clause, but the ban does
    not read it.

    **Matched on the final component alone**, as `_imports_outcomes` matches an import: a
    module's own class called `Exception` would be flagged, and what that costs is a route
    being held to a rule it should be held to anyway. What being narrow costs is every row
    marked **no** above — measured 2026-09-24, four of them escaped the ban entirely.
    """
    if isinstance(node, ast.Name):
        return {node.id}
    if isinstance(node, ast.Attribute):
        return {node.attr}
    if isinstance(node, ast.Tuple | ast.List):
        return {name for element in node.elts for name in _caught_names(element)}
    if isinstance(node, ast.Starred):
        return _caught_names(node.value)
    return set()


def _catches_everything(handler: ast.ExceptHandler) -> bool:
    """Does this `except` clause catch everything the route can raise?

    `handler.type is None` is the bare `except:`, and it is the strongest of the five: it
    catches `KeyboardInterrupt` and `SystemExit` too, so it is strictly worse than
    `except Exception` — and it was the one spelling of the banned construct the ban could
    not see at all.
    """
    return handler.type is None or bool(_caught_names(handler.type) & CATCH_ALL_NAMES)


def bare_handlers_in(label: str, tree: ast.Module) -> list[str]:
    """Every catch-all handler in one parsed module, as `label:lineno spelling`.

    Split from the directory walk for the reason `outcomes_in` was: a scan proved only
    against the tree it already passes on is a scan whose blind spots are exactly the ones
    nobody looked for. Every `endpoints/` handler under the ban is spelled `except <Name>`
    today, so every other spelling is unreachable from disk and a mutation of it kills
    nothing — which is precisely when a widening goes unproved. So the spellings are
    planted, and `_the_matcher_this_replaces` enumerates the before-set against them.

    `ast.walk` reaches an `except*` clause as well: `ast.TryStar` holds ordinary
    `ast.ExceptHandler` nodes, so `except* Exception as eg:` is read like any other.
    """
    return [
        f"{label}:{handler.lineno} "
        f"{'except:' if handler.type is None else f'except {ast.unparse(handler.type)}'}"
        for handler in ast.walk(tree)
        if isinstance(handler, ast.ExceptHandler) and _catches_everything(handler)
    ]


def _bare_handlers(filename: str) -> list[str]:
    """`bare_handlers_in`, over one endpoint module read off disk."""
    source = (ENDPOINTS / filename).read_text(encoding="utf-8")
    return bare_handlers_in(filename, ast.parse(source))


def _plan(**params: Any) -> dict[str, Any]:
    with TestClient(app) as client:
        return client.get("/api/v1/asta/plan", params={"league_id": 4103937, **params}).json()


class TestTheRoutesReturnOnlyWhatTheyPin:
    """Read from the source. A route that returns an unlisted outcome does not fail — it
    renders a screen the frontend has no branch for, which is a blank one.
    """

    def test_every_model_that_names_an_outcome_is_pinned(self) -> None:
        """The list's own completeness, which nothing checked.

        `TestTheRoutesReturnOnlyWhatTheyPin` held five hand-written triples beside a
        discovery that already found six routes, and three models were simply absent from
        it: `teams.py`'s `TeamSnapshotResult` and `BackfillResult`, and — the one nobody
        would have guessed — `lineup.py::SubmitResult`, the route that puts an XI on the
        live platform. Nothing went red, because a parametrized list that omits a case
        omits its failures with it.

        `==`, so it fails in both directions: a new model that names an outcome fails here
        until somebody pins it, and a pinned name whose model is gone fails here too.
        """
        assert set(OUTCOME_MODELS) == set(PINNED_OUTCOMES), (
            f"the discovery found {sorted(OUTCOME_MODELS)} and this file pins "
            f"{sorted(PINNED_OUTCOMES)}. New in the discovery is a model returning "
            "outcomes nothing compares to a tuple — give it one. Gone from it is a model "
            "that stopped naming outcomes, or a scan that stopped reading it."
        )

    @pytest.mark.parametrize(
        ("source", "models"),
        [
            # The declaration. Only the `ClassDef` source sees this one — there is no
            # construction site in the tree at all.
            ("class Foo(BaseModel): outcome: str", {"Foo"}),
            ("class Foo(BaseModel): outcome: str = 'planned'", {"Foo"}),
            # The construction. Only the call source sees these — the class is declared in
            # another module, which is how `room_bid.py`'s neighbours would spell it.
            ("Foo(outcome='x')", {"Foo"}),
            ("models.Foo(outcome='x')", {"Foo"}),
            # The loose direction, pinned rather than left to be discovered by surprise: a
            # helper taking an `outcome=` keyword is discovered and must then be pinned or
            # explained. That costs somebody reading the rule; being narrow costs a model
            # returning names no tuple covers.
            ("build_result(outcome='x')", {"build_result"}),
            # And the other direction, which is what stops "return every name" from
            # passing — a discovery that found everything would make `PINNED_OUTCOMES`
            # unmaintainable and the equality above meaningless.
            ("class Foo(BaseModel): reason: str", set()),
            ("Foo(reason='x')", set()),
            ("outcome = 'x'", set()),
            ("def f(outcome: str) -> None: pass", set()),
        ],
        ids=lambda value: value if isinstance(value, str) else "",
    )
    def test_the_model_discovery_reads_a_declaration_and_a_construction(
        self, source: str, models: set[str]
    ) -> None:
        """Both halves of the union, each planted so that each is observable.

        Every model in `endpoints/` is both declared and constructed in the same file, so
        either half alone finds all nine and deleting the other changes nothing
        measurable — two gates on one question, where the first makes the second
        unobservable. The rows above are the only place the two are told apart.
        """
        assert _models_naming_outcomes(ast.parse(source)) == models

    @pytest.mark.parametrize(("filename", "model"), OUTCOME_MODELS)
    def test_the_outcomes_it_returns_are_exactly_the_ones_it_pins(
        self, filename: str, model: str
    ) -> None:
        # `room_bid.py` is deliberately outside all of this, and the reason is the design
        # rather than an exemption: it **forwards** `check_room`'s outcome instead of
        # naming four of its own, so it imports no vocabulary, the route discovery never
        # sees it, and a scan for literals would see only `started`. Re-listing them in the
        # route to satisfy this scan would be the second set of reasons that one resolution
        # path exists to prevent. Its real invariant — that the pin covers everything
        # `check_room` can say — is asserted in `test_room_bid.py`, where the two tuples can
        # be compared directly.
        pinned = PINNED_OUTCOMES[filename, model]
        scan = outcomes_returned(filename, model)

        assert not scan.unreadable, (
            f"the scan could not read {scan.unreadable}. Spell the outcome as a string "
            "literal or teach `outcomes_returned` the new shape — an outcome it cannot "
            "read is a screen it cannot police."
        )
        returned = scan.named
        assert returned, f"{filename} names no outcome at all — this scan reads nothing"
        assert returned == set(pinned), (
            f"{filename} returns {sorted(returned)} and pins {sorted(pinned)}; "
            "a route that gains an outcome must say so, and one that loses an outcome "
            "must delete its name"
        )

    @pytest.mark.parametrize(
        ("source", "named", "unreadable"),
        [
            ("RoomCheck(outcome='resolved')", {"resolved"}, 0),
            # The callee the `ast.Name`-only reader dropped without a word.
            ("models.RoomCheck(outcome='resolved')", {"resolved"}, 0),
            # Unreadable, not absent. A dropped site narrows the scan in silence, and when
            # the value it carried is named elsewhere too, even `==` cannot see it go.
            ("RoomCheck(outcome=RESOLVED)", set(), 1),
            ("RoomCheck(outcome=x if y else z)", set(), 1),
            # `outcome` defaults on three of the five models, so a site that omits it
            # returns a name this scan never read. That is a failure, not a skip.
            ("RoomCheck(reason='')", set(), 1),
            ("RoomCheck(**payload)", set(), 1),
            # And the limit: another model's call is not this model's.
            ("AstaPlan(outcome='planned')", set(), 0),
            # `model_copy(update=...)`, which `lineup.py` returns five times and a reader of
            # constructors alone scored as four outcomes out of seven.
            (
                "b = RoomCheck(outcome='resolved'); b.model_copy(update={'outcome': 'refused'})",
                {"resolved", "refused"},
                0,
            ),
            # Chained on the constructor, with no name in between.
            (
                "RoomCheck(outcome='resolved').model_copy(update={'outcome': 'refused'})",
                {"resolved", "refused"},
                0,
            ),
            # A copy that does not touch `outcome` keeps the one the constructor named, so
            # it is silent rather than unreadable — the only shape here that is.
            (
                "b = RoomCheck(outcome='resolved'); b.model_copy(update={'reason': 'x'})",
                {"resolved"},
                0,
            ),
            # ...and every shape that hides the key is a failure, on `OutcomeScan`'s rule.
            (
                "b = RoomCheck(outcome='resolved'); b.model_copy(update={'outcome': NAME})",
                {"resolved"},
                1,
            ),
            (
                "b = RoomCheck(outcome='resolved'); b.model_copy(update=payload)",
                {"resolved"},
                1,
            ),
            (
                "b = RoomCheck(outcome='resolved'); b.model_copy(update={**payload})",
                {"resolved"},
                1,
            ),
            ("b = RoomCheck(outcome='resolved'); b.model_copy(**kw)", {"resolved"}, 1),
            # And the limit again: a copy of another model is not this model's.
            (
                "o = AstaPlan(outcome='planned'); o.model_copy(update={'outcome': 'nope'})",
                set(),
                0,
            ),
        ],
        ids=lambda value: value if isinstance(value, str) else "",
    )
    def test_the_outcome_scan_reads_every_shape_of_the_call(
        self, source: str, named: set[str], unreadable: int
    ) -> None:
        """The scan, against planted calls rather than only the tree it passes on.

        Every construction site in `endpoints/` is a bare `Name(outcome="literal")` today,
        so widening the reader changes nothing measurable and a mutation of it kills no
        test. That is precisely when a widening goes unproved — so the shapes are planted.
        """
        scan = outcomes_in("planted.py", ast.parse(source), "RoomCheck")

        assert (scan.named, len(scan.unreadable)) == (named, unreadable)

    @pytest.mark.parametrize("filename", DECISION_ROUTES)
    def test_the_per_model_scans_account_for_every_outcome_the_route_writes(
        self, filename: str
    ) -> None:
        """The attribution's own hole, closed by arithmetic instead of by cleverness.

        `outcomes_returned` reads a model's constructor and the copies of it this module
        can attribute to that model. A site it cannot place — `helper().model_copy(update=
        {"outcome": "x"})`, say — would simply not appear in any pinned set, and an outcome
        missing from every tuple renders a screen the frontend has no branch for.

        So the literals are counted twice: once per model, and once over the whole file
        with no attribution at all. **Equality, not containment.** `<=` would pass while
        `_outcome_literals` quietly read fewer shapes than it does — and the dict half of
        it is what finds `lineup.py`'s three `model_copy` outcomes, so a narrowing there is
        exactly the failure this file keeps meeting. It is `OutcomeScan.unreadable`'s
        argument — a site the scan cannot fold narrows the answer in silence — applied
        where the silence is a whole construction shape rather than one argument.
        """
        tree = ast.parse((ENDPOINTS / filename).read_text(encoding="utf-8"))
        written = _outcome_literals(tree)
        placed: set[str] = set()
        for route, model in OUTCOME_MODELS:
            if route == filename:
                placed |= outcomes_returned(route, model).named

        assert written == placed, (
            f"{filename} writes {sorted(written - placed)} as an outcome that no model's "
            f"scan reads, and its models are scanned as returning {sorted(placed - written)} "
            "that the file does not write. Either a construction shape is unreachable "
            "from `outcomes_returned`'s attribution — teach it the shape — or a model is "
            "not being discovered, or `_outcome_literals` has stopped reading one."
        )

    @pytest.mark.parametrize(
        "filename", [name for name, _ in dict.fromkeys(OUTCOME_MODELS)]
    )
    def test_a_route_with_a_pinned_model_writes_at_least_one_outcome(
        self, filename: str
    ) -> None:
        """Non-vacuity for the equality above: two empty sets are equal.

        `_outcome_literals` returning `set()` for every file would satisfy the comparison
        on every route whose models also scanned empty, and the guard would report six
        passes over nothing. A route that has a pinned model has written a name.
        """
        written = _outcome_literals(ast.parse((ENDPOINTS / filename).read_text(encoding="utf-8")))

        assert written, (
            f"{filename} has a pinned model and this scan reads no outcome literal in it "
            "at all — the comparison above is two empty sets"
        )

    @pytest.mark.parametrize("filename", DECISION_ROUTES)
    def test_no_decision_route_catches_bare_exception(self, filename: str) -> None:
        """The criterion, literally: `except Exception` in none of them.

        An earlier version of this test allowed one per route as the `unreachable` outcome,
        on the argument that banning it trades "we could not ask" for a 500. That was a
        weaker rule substituted for a written one, and the written one is right: a 500 is
        logged, alarming and unmistakably a fault, where a bare handler turns an
        unanticipated bug into a tidy page. The set of things that can actually go wrong on
        these routes is small enough to name — `SQLAlchemyError`, `OSError`, `TokenError`,
        `httpx.HTTPError` and the planner's own refusals — and anything outside it is a bug
        in this repository.
        """
        bare = _bare_handlers(filename)

        assert not bare, (
            f"{filename} catches everything at {bare}. Name the families the route can "
            "actually fail on; let the rest reach FastAPI as a 500. A tuple that holds "
            "`Exception` is not a narrowing — `except (Exception, OSError)` catches "
            "exactly what `except Exception` catches."
        )

    @pytest.mark.parametrize(
        ("clause", "banned"),
        [
            # The one spelling the `ast.Name` test read.
            ("except Exception:", True),
            ("except Exception as exc:", True),
            ("except BaseException:", True),
            # ...and the four it was blind to, each measured escaping it on 2026-09-24.
            ("except:", True),
            ("except (Exception,) as exc:", True),
            ("except (Exception, OSError) as exc:", True),
            ("except (OSError, BaseException):", True),
            ("except builtins.Exception as exc:", True),
            # A tuple nests. It raises `TypeError` at match time rather than catching, so
            # it is broken code either way — but a reader with a hole is the defect here.
            ("except ((Exception, OSError), ValueError):", True),
            ("except (*FAMILIES, Exception):", True),
            # The `Starred` branch, which nothing above reaches: with the star unread, the
            # tuple still finds a plain `Exception` element beside it. Only a star over a
            # display that *holds* the name needs the recursion — and both of these catch
            # everything at runtime, measured in this venv.
            ("except (*(Exception, OSError), ValueError):", True),
            ("except (*[Exception], ValueError):", True),
            # `except*` reaches the walk as an ordinary `ExceptHandler`.
            ("except* Exception as eg:", True),
            # And the other direction, which is what stops `return True` from passing: a
            # matcher that banned everything would cover every handler and check nothing.
            ("except OSError:", False),
            ("except (SQLAlchemyError, OSError) as exc:", False),
            ("except (TokenRejected, AppKeyRejected) as exc:", False),
            ("except errors.TokenError as exc:", False),
            ("except ((ValueError, OSError), TypeError):", False),
            ("except (*FAMILIES, ValueError):", False),
            ("except (*(OSError, ValueError), TypeError):", False),
            ("except (*[OSError], ValueError):", False),
            # The limit, stated as a row: a type this reader cannot resolve is not read.
            ("except FAMILIES:", False),
            ("except resolve_families():", False),
        ],
        ids=lambda value: value if isinstance(value, str) else "",
    )
    def test_the_ban_reads_every_spelling_of_the_construct(
        self, clause: str, banned: bool
    ) -> None:
        """The matcher itself, against planted clauses rather than the tree it passes on.

        Every handler under the ban in `endpoints/` is spelled `except <Name>` today, so
        four of the five spellings never reach the scan from disk and a mutation of them
        kills no test. That is exactly when a widening goes unproved — so they are planted.
        """
        found = bare_handlers_in("planted.py", ast.parse(_a_try_block(clause)))

        assert bool(found) is banned, found

    def test_the_ban_sees_every_spelling_it_used_to_and_four_more(self) -> None:
        """The superset claim, enumerated in code rather than asserted in prose.

        Round one of this tier shipped a "strict superset" that had quietly dropped its
        most sensitive entry, so a widening here is not believed until both sets are built
        and compared. `_the_matcher_this_replaces` generates the before-set over the same
        corpus this one reads; the assertion is that the after-set **contains** it with
        zero losses, and that it is strictly larger, and that the four spellings named in
        the defect are the reason it is larger.
        """
        corpus = _handler_corpus()

        before = {clause for clause, handler in corpus.items() if _the_matcher_this_replaces(handler)}
        after = {clause for clause, handler in corpus.items() if _catches_everything(handler)}

        assert before, "the before-set is empty — this corpus measures nothing"
        assert not before - after, (
            f"the widening LOST {sorted(before - after)}: every clause the old matcher "
            "caught must still be caught, or this is not a superset"
        )
        assert before < after, (
            "the after-set is not strictly larger, so nothing was widened — check the "
            "corpus before the matcher"
        )
        assert {
            "except:",
            "except (Exception,):",
            "except (Exception, OSError):",
            "except builtins.Exception:",
        } <= after - before, sorted(after - before)
        # And not a widening to everything. The benign half of the corpus is generated
        # from the same spellings over atoms that are real families, so this cannot drift
        # away from what was measured above: `_catches_everything` returning `True`
        # unconditionally would cover every route and check nothing, which is the shape of
        # "widened" this tier keeps finding.
        benign = {
            clause
            for atom in BENIGN_ATOMS
            for clause in _spellings_of(atom)
        }
        assert benign, "the benign half is empty — the negative direction is unmeasured"
        assert not after & benign, sorted(after & benign)


class TestTheBanCoversEveryRouteThatNamesAnOutcome:
    """The ban's scope, asserted rather than typed out.

    It was a hand-written list — `["asta.py", "lineup.py", "pricing.py", "room_bid.py"]` —
    and by 2026-09-24 it was wrong in both directions at once. It **omitted `room.py`**,
    the file `api/outcomes.py` calls "the model and got there first", which held two
    bare `except Exception`; and it **included `room_bid.py`**, which contains no `except`
    clause at all, so that quarter of the parametrization scanned a file with nothing to
    find and reported a pass. Three real files covered, the canonical one missed, and the
    count padded back to four.

    The list is gone. A decision route is now *discovered*: a module under `endpoints/`
    that imports from `api/outcomes.py` has adopted this rule by importing the vocabulary
    it is written in, and `because(exc)` is only callable from inside an `except` clause —
    so importing it is a statement that the module names its failures. A new route is
    covered the day it is written rather than the day somebody remembers this list.

    `room_bid.py` is not in the discovered set and that is the design, not a gap: it names
    no outcome of its own, forwarding `check_room`'s (see `test_room_bid.py`), and it
    catches nothing. The day it grows a handler it will need `because` to build the reason,
    and the discovery picks it up then.
    """

    def test_the_discovery_finds_exactly_the_routes_that_name_their_failures(self) -> None:
        """Equality, in both directions, because one direction was the whole defect.

        This was `{"asta.py", "lineup.py", "pricing.py", "room.py"} <= set(...)` — four of
        the six names, and a `<=`. So `exclusions.py` and `teams.py` could leave the
        discovered set for any reason at all and every parametrized assertion in this file
        would simply run fewer times. Measured: respelling one route's import as
        `from fantabot_app.api import outcomes` took the api suite from 414 passed to 412
        passed with **nothing red**.

        `==` fails in both directions. A route that stops being discovered fails here; a
        route that starts being discovered fails here too, and adding its name is the
        moment somebody reads what the ban is.
        """
        assert set(DECISION_ROUTES) == set(PINNED_DECISION_ROUTES), (
            f"the discovery found {sorted(DECISION_ROUTES)} and this file pins "
            f"{sorted(PINNED_DECISION_ROUTES)}. Gone from the discovery is a route that "
            "escaped the bare-`except` ban — check how it spells its import before "
            "deleting the name. New in it is a route that has adopted the rule and must "
            "be named here."
        )

    @pytest.mark.parametrize(
        ("source", "seen"),
        [
            # The two spellings the `ImportFrom`-only walk already read.
            ("from fantabot_app.api.outcomes import because", True),
            ("from .outcomes import because", True),
            ("from ...outcomes import because", True),
            # The two it was blind to. Each was planted as a real endpoint module holding a
            # bare `except Exception` on 2026-09-24 and the whole api suite stayed green.
            ("from fantabot_app.api import outcomes", True),
            ("import fantabot_app.api.outcomes", True),
            # ...and their neighbours, because a fix aimed at two literal spellings is how
            # this arrived here a second time.
            ("import fantabot_app.api.outcomes as oc", True),
            ("from . import outcomes", True),
            ("from .. import outcomes", True),
            ("from fantabot_app.api import outcomes as oc", True),
            ("from fantabot_app.api import because, outcomes", True),
            # And the other direction, which is what stops "return True" from passing.
            ("from fantabot_app.api.infrastructure.jobs import registry", False),
            ("from fantabot_app.api import main", False),
            ("import fantabot.config", False),
            ("from fantabot_app.api.outcomes_helpers import because", False),
            ("from . import room", False),
            ("outcomes = 1", False),
        ],
        ids=lambda value: value if isinstance(value, str) else "",
    )
    def test_the_discovery_reads_every_spelling_of_the_import(
        self, source: str, seen: bool
    ) -> None:
        """The superset claim, enumerated rather than asserted.

        The rows marked `True` are the after-set and the first three of them are the whole
        before-set: every tree the old equality matched is here and still matches, and the
        rest are what it missed. The rows marked `False` are what keeps the widening from
        being a widening to everything — a `_imports_outcomes` that answered `True`
        unconditionally would cover every route and check nothing, which is the shape of
        "widened" that this tier keeps finding.
        """
        assert _imports_outcomes(ast.parse(source)) is seen

    @pytest.mark.parametrize("filename", DECISION_ROUTES)
    def test_each_covered_route_has_something_to_find(self, filename: str) -> None:
        """Non-vacuity in the second: no entry may be a file with no `except` in it.

        `room_bid.py` passed the old ban four times over exactly this way — a scan of a
        file that cannot fail it. An entry that holds no handler is padding, and padding in
        a parametrized guard reads as a higher number of passing cases.
        """
        handlers = [
            node.lineno
            for node in ast.walk(ast.parse((ENDPOINTS / filename).read_text(encoding="utf-8")))
            if isinstance(node, ast.ExceptHandler)
        ]

        assert handlers, (
            f"{filename} imports from api/outcomes.py and holds no `except` at all — "
            "either it does not name failures after all, or the discovery is reading the "
            "wrong thing"
        )


class TestFourFailuresFourScreens:
    """Induced, not described. Each patches one thing and reads the outcome back."""

    def test_a_database_that_will_not_open_is_unreachable(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from fantabot.adapters.persistence import database_manager

        def boom() -> None:
            raise OperationalError("SELECT 1", {}, OSError("db unreachable"))

        monkeypatch.setattr(database_manager, "get_session", boom)

        body = _plan()

        assert body["outcome"] == "unreachable"
        assert "OperationalError" in body["reason"]

    def test_a_lega_that_was_never_synced_is_no_lega(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The one that is easy to miss: the planner would happily run. With no snapshot
        the format, the budget and the roster band are all defaults, so the plan is three
        guesses wearing an answer's clothes."""
        from fantabot.application import lega_reads as reads

        monkeypatch.setattr(reads, "latest_settings", lambda *_a, **_k: None)
        monkeypatch.setattr(
            "fantabot.adapters.persistence.database_manager.get_session", _fake_session
        )

        body = _plan()

        assert body["outcome"] == "no_lega"
        assert "lega sync" in body["reason"]

    @pytest.mark.parametrize(
        ("error", "outcome"),
        [
            ("NoSentimentRows", "no_sentiment"),
            ("NoCorpus", "no_corpus"),
            ("EmptyPool", "empty_pool"),
            ("InfeasibleRoster", "infeasible"),
        ],
    )
    def test_each_planner_refusal_gets_its_own_screen(
        self, monkeypatch: pytest.MonkeyPatch, error: str, outcome: str
    ) -> None:
        from fantabot.application import lega_reads as reads
        from fantabot.application import plan_request as pr
        from fantabot.domain.asta.optimizer import InfeasibleRoster
        from fantabot.domain.asta.prices import NoCorpus

        raised: Exception = {
            "NoSentimentRows": pr.NoSentimentRows("no rows in the database"),
            "NoCorpus": NoCorpus("3x777 mantra", []),
            "EmptyPool": pr.EmptyPool("no classic players for season 2026/27"),
            "InfeasibleRoster": InfeasibleRoster("no schema can be seeded within budget"),
        }[error]

        def boom(*_a: object, **_k: object) -> None:
            raise raised

        monkeypatch.setattr(
            reads, "latest_settings", lambda *_a, **_k: _snapshot()
        )
        monkeypatch.setattr(pr, "build_plan", boom)
        monkeypatch.setattr(
            "fantabot.adapters.persistence.database_manager.get_session", _fake_session
        )

        body = _plan()

        assert body["outcome"] == outcome
        assert body["reason"], "a named outcome with no reason is half the fix"

    def test_an_unknown_system_is_its_own_screen_not_no_data(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`system` reaches a `WHERE listone = :system`, so a typo selected no rows and read
        as "no training data" — sending the operator to scrape a season when the fix is a
        spelling. Two remedies behind one screen is what this module exists to stop."""
        with TestClient(app) as client:
            body = client.get(
                "/api/v1/asta/target-prices", params={"system": "mantr"}
            ).json()

        assert body["outcome"] == "unknown_system", body
        assert "classic" in body["reason"] and "mantra" in body["reason"]

    def test_the_corpus_shape_reaches_the_plan(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """1.17. The route hardcoded 8x500 and exposed no way to change it, so an operator
        could not price a riparazione or a friend's league — "explicit in code, unreachable
        to the operator", which is the bug 1.6 named and fixed one surface along."""
        from fantabot.application import plan_request as pr

        captured: list[object] = []

        def spy(session: object, request: object) -> object:
            captured.append(request)
            raise pr.EmptyPool("stop here — the request is what this test is about")

        monkeypatch.setattr(
            "fantabot.application.lega_reads.latest_settings", lambda *_a, **_k: _snapshot()
        )
        monkeypatch.setattr(pr, "build_plan", spy)
        monkeypatch.setattr(
            "fantabot.adapters.persistence.database_manager.get_session", _fake_session
        )

        with TestClient(app) as client:
            client.get(
                "/api/v1/asta/plan",
                params={"league_id": 4103937, "teams": 10, "credits": 1000},
            )

        assert captured, "the route did not reach build_plan"
        assert (captured[0].num_teams, captured[0].num_credits) == (10, 1000)  # type: ignore[attr-defined]

    def test_the_five_refusals_are_five_distinct_screens(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The property, stated once: no two of them render the same thing."""
        assert len(set(ASTA_PLAN_OUTCOMES)) == len(ASTA_PLAN_OUTCOMES)
        assert "planned" in ASTA_PLAN_OUTCOMES


class TestTheLineupRouteDoesNotCallATimeoutACredentialProblem:
    """`apileague` maps *every* failure onto `TokenError` — deliberately, because both
    `httpx.RequestError.request` and a bare traceback can render the `Authorization`
    header. A bare `except TokenError` would therefore report a timeout as a credential
    problem, which is `endpoints/room.py`'s ordering lesson exactly."""

    @pytest.mark.parametrize(
        ("error", "outcome"),
        [
            ("ApiTimeout", "unreachable"),
            ("ApiUnavailable", "unreachable"),
            ("TokenRejected", "refused"),
            ("AppKeyRejected", "refused"),
            ("TokenMissing", "no_credential"),
            ("TokenExpired", "no_credential"),
        ],
    )
    def test_each_token_error_family_maps_to_its_own_outcome(
        self, monkeypatch: pytest.MonkeyPatch, error: str, outcome: str
    ) -> None:
        from fantabot.adapters.http import apileague
        from fantabot.domain.tokens import errors

        raised: Exception = {
            "ApiTimeout": errors.ApiTimeout(10.0),
            "ApiUnavailable": errors.ApiUnavailable(503),
            "TokenRejected": errors.TokenRejected(4103937),
            "AppKeyRejected": errors.AppKeyRejected(),
            "TokenMissing": errors.TokenMissing(4103937),
            "TokenExpired": errors.TokenExpired(4103937, "yesterday"),
        }[error]

        def boom(*_a: object, **_k: object) -> None:
            raise raised

        monkeypatch.setenv("FANTABOT_ENCRYPTION_KEY", _A_VALID_KEY)
        monkeypatch.setattr(
            "fantabot.config.settings.fantabot_encryption_key", _A_VALID_KEY, raising=False
        )
        monkeypatch.setattr(apileague, "my_team", boom)
        monkeypatch.setattr(
            "fantabot.adapters.persistence.database_manager.get_session", _fake_session
        )

        with TestClient(app) as client:
            body = client.get("/api/v1/lineup/plan", params={"league_id": 4103937}).json()

        assert body["outcome"] == outcome, body

    def test_no_key_at_all_is_a_credential_problem_and_says_what_to_do(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "fantabot.config.settings.fantabot_encryption_key", "", raising=False
        )

        with TestClient(app) as client:
            body = client.get("/api/v1/lineup/plan", params={"league_id": 4103937}).json()

        assert body["outcome"] == "no_credential"
        assert "connect" in body["reason"].lower()


def test_because_is_one_typed_line_and_not_a_traceback() -> None:
    """A driver traceback says the call failed; it does not say which of five things did,
    and it is not something to put on a page."""
    line = because(RuntimeError("could not connect\nsecond line\nthird line"))

    assert line == "RuntimeError: could not connect"
    assert "\n" not in line


@pytest.mark.parametrize(
    "exc",
    [
        OSError(),
        TimeoutError(),
        ConnectionResetError(),
        SQLAlchemyError(""),
        RuntimeError(""),
        # Whitespace says nothing either, and reads as `"OSError:    "` without the strip.
        OSError("   \n  "),
    ],
    ids=lambda exc: type(exc).__name__,
)
def test_an_exception_with_nothing_to_say_is_named_rather_than_raising(exc: Exception) -> None:
    """The families these routes actually name, and every one of them stringifies to
    nothing — `""` for the first five, whitespace for the last.

    `"".splitlines()` is `[]`, so `[0]` raised `IndexError` *inside* the `except` clause
    that called `because` to keep the fault off the 500 path: the helper turned the tidy
    page it exists to build into the 500 it exists to avoid. A socket that times out, a
    database handle that will not open and a transport error carrying its cause's empty
    string are not exotic; they are what an outage looks like.

    Equality, not "it did not raise". A fix that only stopped the crash would render
    `"OSError: "` — a colon promising a reason that is not there — and pass a test that
    asked for no more than survival.
    """
    assert because(exc) == type(exc).__name__


# -- helpers ------------------------------------------------------------------------------

#: Atoms that name something catching everything, and atoms that name a real family. The
#: corpus below is the cross product of these with every spelling an `except` clause has,
#: so the two halves cannot drift apart or be maintained against each other by hand.
CATCH_ALL_ATOMS = ("Exception", "BaseException", "builtins.Exception")
BENIGN_ATOMS = (
    "OSError",
    "ValueError",
    "TokenRejected",
    "errors.TokenError",
    "sqlalchemy.exc.SQLAlchemyError",
)


def _a_try_block(clause: str) -> str:
    """One `except` clause, wrapped in the smallest tree that parses it."""
    return f"try:\n    pass\n{clause}\n    pass\n"


def _spellings_of(atom: str) -> tuple[str, ...]:
    """Every way one exception name can be written into an `except` clause.

    Generated rather than typed, because a corpus maintained by hand beside a matcher
    maintained by hand is two lists that agree until one of them is edited — which is the
    defect one level up in this same file.
    """
    return (
        f"except {atom}:",
        f"except {atom} as exc:",
        f"except ({atom},):",
        f"except ({atom}, OSError):",
        f"except (OSError, {atom}) as exc:",
        f"except (({atom}, OSError), ValueError):",
        f"except (*FAMILIES, {atom}):",
        f"except* {atom} as eg:",
    )


def _handler_corpus() -> dict[str, ast.ExceptHandler]:
    """Every spelling of a handler this ban can meet, as `clause -> the parsed handler`.

    The bare `except:` is in the catch-all half and has no atom: it names nothing, which is
    why it escaped a matcher that read `handler.type`.
    """
    clauses = ["except:"] + [
        clause
        for atom in CATCH_ALL_ATOMS + BENIGN_ATOMS
        for clause in _spellings_of(atom)
    ]
    corpus: dict[str, ast.ExceptHandler] = {}
    for clause in clauses:
        [handler] = [
            node
            for node in ast.walk(ast.parse(_a_try_block(clause)))
            if isinstance(node, ast.ExceptHandler)
        ]
        corpus[clause] = handler
    return corpus


def _the_matcher_this_replaces(handler: ast.ExceptHandler) -> bool:
    """`_bare_handlers`' predicate as it read until 2026-09-24, kept for one purpose.

    A superset claim is worth exactly what its enumeration is worth, and an enumeration
    needs both sets. This generates the before-set;
    `test_the_ban_sees_every_spelling_it_used_to_and_four_more` is the only caller, and it
    is not the rule any more — nothing else may ask it anything.
    """
    return isinstance(handler.type, ast.Name) and handler.type.id in CATCH_ALL_NAMES


def _a_valid_key() -> str:
    """A throwaway Fernet key, minted rather than written down.

    `TokenCipher(key)` validates, so a plausible-looking literal fails construction and the
    route answers `no_credential` before reaching the call the test is about — which is how
    the first version of this file reported four network failures as credential problems.
    Minted for the same reason `ci.yml` mints one: a key literal in a tracked file is a key
    literal, whatever it opens.
    """
    from cryptography.fernet import Fernet

    return Fernet.generate_key().decode()


_A_VALID_KEY = _a_valid_key()


def _snapshot() -> SimpleNamespace:
    return SimpleNamespace(
        role_groups=2, budget=500, roster_size=30, min_roles=[2, 28], max_roles=[4, 28]
    )


class _FakeSession:
    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_exc: object) -> None:
        return None


def _fake_session(*_a: object, **_k: object) -> _FakeSession:
    """A session that opens and does nothing. The reads on top of it are patched."""
    return _FakeSession()
