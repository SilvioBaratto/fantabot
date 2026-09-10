"""An acting job must be stoppable, or the disarm is a lie.

`JobRegistry.stop` returns `False` for a thread job — a daemon thread cannot be interrupted
from outside — and `POST /jobs/{id}/stop` turns that into a 409. A disarm control that
answers 409 is a lie, and it is a lie told at the one moment it matters. So an acting job
has to be a `ProcessJob`, over 0.5's stop flag.

The *contract* (two locks, both named) lives in `fantabot.application.arming`, because both
surfaces share it. What lives here is the half only the app has: job kinds.

`ACTING_KINDS` is empty until 3.3 registers the first one. The guard exists first on
purpose — the rule is in place before there is anything to break it.
"""

from __future__ import annotations

import ast
from pathlib import Path

ENDPOINTS = Path(__file__).resolve().parent.parent / "v1" / "endpoints"

#: Job kinds that can act. Empty until 3.3.
ACTING_KINDS: frozenset[str] = frozenset()


def _registered_kinds() -> list[tuple[str, bool]]:
    """`(kind, has a stop)` for every `registry.start(kind=...)` in the endpoints."""
    found: list[tuple[str, bool]] = []
    for path in ENDPOINTS.glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
                continue
            if node.func.attr != "start":
                continue
            kind = next(
                (
                    kw.value.value
                    for kw in node.keywords
                    if kw.arg == "kind" and isinstance(kw.value, ast.Constant)
                ),
                None,
            )
            if kind is not None:
                found.append((str(kind), any(kw.arg == "stop" for kw in node.keywords)))
    return found


def test_every_acting_kind_is_started_with_a_stop() -> None:
    starts = _registered_kinds()

    assert starts, "no registry.start(kind=...) call found — this scan reads nothing"
    unstoppable = [kind for kind, has_stop in starts if kind in ACTING_KINDS and not has_stop]
    assert not unstoppable, (
        f"these acting jobs are registered with no `stop=`: {unstoppable}. `registry.stop` "
        "answers False for them and the route 409s — a disarm that cannot disarm."
    )


def test_the_scan_finds_the_kinds_it_claims_to() -> None:
    """A guard over an empty set passes trivially; this proves the walk reads real code."""
    kinds = {kind for kind, _ in _registered_kinds()}

    assert {"lega-sync", "harvest-collect", "auth-login"} <= kinds


def test_and_would_notice_an_unstoppable_one() -> None:
    planted = ast.parse('registry.start(job, kind="acting-thing")')
    found = [
        (kw.value.value, any(k.arg == "stop" for k in node.keywords))
        for node in ast.walk(planted)
        if isinstance(node, ast.Call)
        for kw in node.keywords
        if kw.arg == "kind" and isinstance(kw.value, ast.Constant)
    ]

    assert found == [("acting-thing", False)]
