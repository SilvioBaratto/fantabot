"""Each surface reads the clock in exactly one place, and that place has a name.

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
seam means one target, and this test is what keeps it one.

**The scan covers surfaces, not one package, and that is 1.3's whole point.** It used to
read `domain/asta/*.py` plus `interface/asta.py` and nothing else. Lifting asta decisions
into `application/` would move them *out* of its coverage — a rule that quietly stops
applying to the code it was written for. So `application/` is swept by glob (a module that
does not exist yet is covered on the day it is created, which is the only version of this
that survives 1.4), the lineup path is a second surface with its own seam, and the app's
asta endpoints are a third.

The parity tier freezes these seams. A surface with two of them is a surface the tier
cannot freeze, which is what makes this a precondition of `-m parity` rather than a tidy.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

import pytest
from _paths import PACKAGE, REPO, module_file, pkg

APP = REPO / "app" / "fantabot_app"


@dataclass(frozen=True)
class Surface:
    """One decision path, the files it spans, and the single function allowed to look.

    `seams` is the count expected *today*, compared for equality rather than as a ceiling.
    A surface that gains a second read fails, and so does one whose read is removed
    without this number moving — the ratchet discipline `test_layers.py` uses, for the
    same reason: a bound that only ever loosens stops meaning anything.
    """

    name: str
    seam: str
    seams: int
    files: tuple[Path, ...]


def _asta_files() -> tuple[Path, ...]:
    """The decision modules, the orchestration, and the command that drives them.

    `application/` is a **glob**, deliberately. 1.4 adds `plan_request.py` and 3.6a adds
    `asta_session.py`; a hand-written list would have to be edited in the same commit that
    creates them, and the failure of forgetting is silent — the module is simply not
    scanned. The command is named as a module rather than globbed because the seam lives
    in it and a glob that resolved to nothing would make the count assertion pass by
    finding no seam at all.
    """
    return (
        *sorted(pkg("asta_engine").glob("*.py")),
        *sorted((PACKAGE / "application").glob("asta_*.py")),
        *sorted((PACKAGE / "application").glob("plan_*.py")),
        module_file("fantabot.interface.asta"),
    )


def _lineup_files() -> tuple[Path, ...]:
    """The second seam, and it was entirely unscanned until 1.3.

    `interface/lineup.py::_now()` reads `datetime.now()` for the submission deadline. It
    is brought in rather than exempted: the parity tier compares `lineup plan` against
    `/lineup/plan`, and a matchday deadline that moves between the two runs is the same
    coin flip the asta seam exists to prevent. Its seam is `_now`, not `_today`, because
    it needs a time of day and not a date — which is why the seam name is per surface.
    """
    return (
        *sorted((PACKAGE / "domain" / "lineup").glob("*.py")),
        *sorted((PACKAGE / "application").glob("lineup_*.py")),
        module_file("fantabot.interface.lineup"),
    )


def _app_lineup_files() -> tuple[Path, ...]:
    """The app's lineup surface, which gained a clock in 3.3.

    `POST /lineup/submit` reads the calendar for the past-kickoff warning, so the app now has
    a second seam — and it was in **no** surface until this was added, which is the exact
    gap 1.3 closed for asta. A rule that silently stops covering new code is worse than one
    that never covered it.
    """
    return (APP / "api" / "v1" / "endpoints" / "lineup.py",)


def _app_asta_files() -> tuple[Path, ...]:
    """The app's asta surfaces. Three files, and the scope is deliberately not `app/`.

    `endpoints/actions.py:67` reads `date.today()` for `news fetch`'s resume key and
    `endpoints/auth.py:152` reads `datetime.now(UTC)` to age a token. Both are real reads
    on paths that are not this rule's subject — the `as_of` the sentiment decay consumes —
    and sweeping the whole package would have to exempt them one by one, which is how a
    rule comes to describe its exemptions instead of its subject.
    """
    endpoints = APP / "api" / "v1" / "endpoints"
    return (endpoints / "asta.py", endpoints / "pricing.py", endpoints / "room.py")


SURFACES = (
    Surface("asta", seam="_today", seams=1, files=_asta_files()),
    Surface("lineup", seam="_now", seams=1, files=_lineup_files()),
    # One since 1.5. It was zero, and that was the defect rather than the design:
    # `GET /asta/plan` passed `as_of=None`, so the page showed the sentiment model's
    # **ablation control** — plain `fvm`, which on the 2026-08-28 data chases a player with
    # a metatarsal fracture to 62 credits. The seam is `endpoints/asta.py::_today` and the
    # parity tier freezes it; before it did, the two sides read the calendar six days
    # apart and disagreed about what the same rosa was worth.
    Surface("app.asta", seam="_today", seams=1, files=_app_asta_files()),
    # One since 3.3: `POST /lineup/submit` warns when `mstr` looks past kickoff, and the
    # parity tier freezes it alongside the CLI's.
    Surface("app.lineup", seam="_now", seams=1, files=_app_lineup_files()),
)


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


def _scan(surface: Surface) -> tuple[list[str], list[str]]:
    """`(offenders, seams)` — where the calendar is read, and where it is allowed to be."""
    offenders: list[str] = []
    seams: list[str] = []
    for path in surface.files:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for call in _calendar_reads(tree):
            where = f"{path.relative_to(REPO) if path.is_relative_to(REPO) else path}:{call.lineno}"
            (seams if _enclosing_function(tree, call) == surface.seam else offenders).append(where)
    return offenders, seams


@pytest.mark.parametrize("surface", SURFACES, ids=lambda s: s.name)
def test_a_surface_reads_the_calendar_only_through_its_seam(surface: Surface) -> None:
    """One seam per surface, so the parity tier has one patch target per surface."""
    offenders, _ = _scan(surface)

    assert not offenders, (
        f"{surface.name} reads the calendar outside {surface.seam}(): {offenders}. "
        "A second read is a second thing the golden harness and the parity tier must "
        "freeze, and the one they miss is the one that expires the gate."
    )


@pytest.mark.parametrize("surface", SURFACES, ids=lambda s: s.name)
def test_the_seam_count_is_what_it_is_recorded_as(surface: Surface) -> None:
    """Equality, not a ceiling: a removed seam must move this number in the same commit."""
    _, seams = _scan(surface)

    assert len(seams) == surface.seams, (
        f"{surface.name} has {len(seams)} calendar seams and is recorded as "
        f"{surface.seams}: {seams}"
    )


@pytest.mark.parametrize("surface", SURFACES, ids=lambda s: s.name)
def test_every_file_a_surface_names_exists(surface: Surface) -> None:
    """A scan over a missing file examines nothing and passes — `_paths.pkgs`'s reason,
    and the exact shape of the gate that judged fixtures for a week."""
    missing = [str(p) for p in surface.files if not p.is_file()]

    assert not missing, f"{surface.name} names files that are not there: {missing}"
    assert surface.files, f"{surface.name} scans nothing at all"


def test_the_asta_scan_reaches_the_application_layer_by_glob() -> None:
    """The lift is what this test exists for: `application/` is swept, not listed."""
    scanned = {p.name for p in _asta_files()}

    assert "asta_planner.py" in scanned
    assert "plan_inputs.py" in scanned
    assert "asta_room.py" in scanned


def test_the_seams_are_reachable_by_name() -> None:
    """The harness and the parity tier patch them by name; a rename must break here."""
    from fantabot.interface import asta as asta_cli
    from fantabot.interface import lineup as lineup_cli

    assert callable(getattr(asta_cli, "_today", None))
    assert callable(getattr(lineup_cli, "_now", None))
