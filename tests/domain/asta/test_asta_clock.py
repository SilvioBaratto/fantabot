"""The asta path reads the clock in exactly one place.

`sentiment.py`'s docstring already states the rule for the pure layer — *"no clock —
`as_of` is a parameter, because a pure module that reads the clock has tests that are a
coin flip"*. This module extends the same rule to the shell, for a different reason.

Three commands passed `as_of=date.today()` independently. That is not a purity problem;
it is a **reproducibility** problem, and it was measured: `sentiment.py:153` decays
confidence on a 7-day half-life against `as_of`, and every row in the table shares one
`data_run`, so a single day of drift rescales every reading. The same inputs printed
`obj 2273.1` today, `2209.1` tomorrow and `1936.5` in a week — with roster *membership*
changing, not just a printed number.

The golden harness pins bytes. Three clock reads means three patch targets that must be
frozen in lockstep, and any one of them missed makes the gate expire within a day. One
seam means one target, and this test is what keeps it one — a later refactor that
reintroduces a second `date.today()` fails here rather than in a golden diff nobody can
explain a week later.
"""

from __future__ import annotations

import ast
from pathlib import Path

from _paths import PACKAGE, module_file, pkg

#: The asta feature: its decision modules and the command that drives them. The command
#: is named as a module rather than swept up by a directory glob, because the seam this
#: file is about lives in it and W6 puts it in a different layer -- a glob over
#: `domain/asta/` alone finds no seam at all and the count assertion below fails open in
#: the one direction that reads as "nothing to see".
ASTA_CLI = "fantabot.interface.asta"


def _asta_sources() -> list[Path]:
    """Every file the rule covers. Raises if the CLI module cannot be resolved."""
    return [*sorted(pkg("asta_engine").glob("*.py")), module_file(ASTA_CLI)]

#: The single function allowed to read the calendar.
SEAM = "_today"


def _calendar_reads(tree: ast.AST) -> list[ast.Call]:
    """Every `date.today()` / `datetime.now()` call in a parsed module.

    `time.time()` is deliberately **not** matched. The one site that uses it
    (`asta_bid`'s `now=lambda: int(time.time() * 1000)`) is the composition root
    wiring `room.run_bid_loop`'s injected `now: Callable[[], int]` parameter — an
    existing seam, not a hidden read, and already fake-able in tests. Folding it into
    this rule would not make anything more deterministic; it would only make the rule
    describe something other than what it is for, which is the `as_of` the sentiment
    decay reads.
    """
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in {"today", "now"}
    ]


def _enclosing_function(tree: ast.AST, target: ast.Call) -> str | None:
    """The name of the function whose body contains `target`, if any."""
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and any(
            child is target for child in ast.walk(node)
        ):
            return node.name
    return None


def test_the_asta_package_reads_the_calendar_in_one_place() -> None:
    """One seam, so the golden harness has one patch target rather than three."""
    offenders: list[str] = []
    seams: list[str] = []

    for path in _asta_sources():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for call in _calendar_reads(tree):
            where = f"{path.relative_to(PACKAGE)}:{call.lineno}"
            if _enclosing_function(tree, call) == SEAM:
                seams.append(where)
            else:
                offenders.append(where)

    assert not offenders, (
        f"the calendar is read outside {SEAM}(): {offenders}. "
        "A second read is a second thing the golden harness must freeze, and the one "
        "it misses is the one that expires the gate."
    )
    assert len(seams) == 1, f"expected exactly one calendar seam, found {seams}"


def test_the_seam_is_reachable_by_name() -> None:
    """The harness patches it by name; a rename must break here, not in a golden diff."""
    from fantabot.interface import asta as cli

    assert callable(getattr(cli, SEAM, None)), (
        f"fantabot.interface.asta.{SEAM} is the harness's patch target"
    )
