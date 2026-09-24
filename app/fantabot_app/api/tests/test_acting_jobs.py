"""An acting job must be stoppable, or the disarm is a lie.

`JobRegistry.stop` returns `False` for a thread job — a daemon thread cannot be interrupted
from outside — and `POST /jobs/{id}/stop` turns that into a 409. A disarm control that
answers 409 is a lie, and it is a lie told at the one moment it matters. So an acting job
has to be a `ProcessJob`, over 0.5's stop flag.

The *contract* (two locks, both named) lives in `fantabot.application.arming`, because both
surfaces share it. What lives here is the half only the app has: job kinds.

**`ACTING_KINDS` was empty from 0.5 until 2026-09-24, and 3.3 shipped in between.** The
docstring said "empty until 3.3 registers the first one" and stayed there after
`room_bid.py` registered `kind=BID_KIND` (`"asta-bid"`) — the one job in this app that
spends real credits. A guard whose input set is empty is a guard that cannot fire, and this
one could not for the whole life of the thing it was written for.

**And populated, it would still have missed it.** The scan read a `kind=` argument only
through `isinstance(kw.value, ast.Constant)`. Eleven of the twelve `registry.start` calls in
`endpoints/` pass a string literal; the twelfth — the acting one — spells it as a module
constant, which is an `ast.Name`. So the one call the guard exists to police was the one
call it silently skipped. It now resolves module-level constants, and a `kind=` it *cannot*
read is a failure rather than a skip: a scan that cannot read a call has to say so, because
"nothing found" and "nothing looked at" render identically.

Three things are asserted, and the third is what keeps `ACTING_KINDS` from rotting again:

* every kind in `ACTING_KINDS` is started with a `stop=`;
* every `registry.start` call's `kind=` was actually resolved, and every name in
  `ACTING_KINDS` is a kind some endpoint really registers;
* every job started with `armed=` — the structural mark of a job that can act — is in
  `ACTING_KINDS`. That one is derived rather than listed, so a second acting job cannot
  arrive without either joining the set or failing this file.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

ENDPOINTS = Path(__file__).resolve().parent.parent / "v1" / "endpoints"

#: Job kinds that can act — that spend credits, submit a lineup, or otherwise write to a
#: live platform. A ratchet, one entry at a time, each named here because the rule below
#: (`stop=` is mandatory) is a promise about that specific job.
#:
#: * ``asta-bid`` — `endpoints/room_bid.py`'s supervised child, the only job in this app
#:   that can spend credits. Registered 2026-09-20 by 3.3 (`c6875e4`); named here
#:   2026-09-24, four days and one audit later.
#:
#: Removing a kind from the endpoints without deleting it here fails
#: `test_every_acting_kind_is_a_kind_something_registers`, so a stale entry cannot sit
#: quietly the way an empty set did.
ACTING_KINDS: frozenset[str] = frozenset({"asta-bid"})

#: **Every** kind the endpoints register — acting or not. `ACTING_KINDS` is a judgement and
#: this is a census, and the census is what stops the judgement being made over a set that
#: quietly shrank. Compared with `==`, so a registration the walk stops seeing is a failure
#: rather than one fewer thing checked.
PINNED_KINDS: frozenset[str] = frozenset(
    {
        "asta-bid",
        "asta-watch",
        "auth-login",
        "db-dump",
        "db-scrape",
        "fantalab-login",
        "harvest-backfill",
        "harvest-collect",
        "harvest-load",
        "harvest-scan",
        "lega-sync",
        "news-fetch",
    }
)

#: The name `JobRegistry` is imported under in every endpoint module. A `<x>.start(...)`
#: call on any other receiver is only treated as a job registration when it passes `kind=`,
#: which keeps an unrelated `thread.start()` from reading as an unresolvable registration.
REGISTRY = "registry"

#: `JobRegistry.start`'s own default for `kind`, so a registration that omits it is read as
#: the kind it actually gets rather than as an unreadable call.
DEFAULT_KIND = "job"


@dataclass(frozen=True)
class Start:
    """One `registry.start(...)` call, as the scan read it.

    `kind` is `None` when the `kind=` argument could not be resolved — which is a failure,
    not an omission. `spelling` is the source text of that argument, so the message names
    what defeated the scan instead of only saying that something did.
    """

    module: str
    lineno: int
    kind: str | None
    spelling: str
    has_stop: bool
    is_armed: bool


def _module_constants(tree: ast.Module) -> dict[str, str]:
    """Module-level `NAME = "literal"` bindings — how `BID_KIND` is spelled."""
    constants: dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            targets: list[ast.expr] = list(node.targets)
            value = node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets = [node.target]
            value = node.value
        else:
            continue
        if not (isinstance(value, ast.Constant) and isinstance(value.value, str)):
            continue
        for target in targets:
            if isinstance(target, ast.Name):
                constants[target.id] = value.value
    return constants


def _receiver_tail(receiver: ast.expr) -> str | None:
    """The last name in `<receiver>.start(...)` — `registry` in both `registry.start` and
    `jobs.registry.start`. `None` for anything with no name at the end, like `f().start`.
    """
    if isinstance(receiver, ast.Name):
        return receiver.id
    if isinstance(receiver, ast.Attribute):
        return receiver.attr
    return None


def _registry_names(tree: ast.Module) -> set[str]:
    """Every local name this module's `JobRegistry` answers to.

    `REGISTRY` alone assumed one spelling of one import. `from ...jobs import registry as
    job_registry` binds another, and `jobs.registry.start(job)` puts the name behind an
    attribute — and a registration the scan does not recognise as one is dropped *silently*
    unless it happens to pass `kind=`, which is the narrowing this whole file is about.

    A strict superset of the equality it replaces: `REGISTRY` is always in the result, so
    every `registry.start(...)` the old `node.func.value.id == REGISTRY` matched still
    matches. What it adds is the attribute form and the alias. `thread.start()` is still
    not a registration — its tail is `thread`, in no import of `registry`.
    """
    names = {REGISTRY}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            names.update(
                alias.asname or alias.name for alias in node.names if alias.name == REGISTRY
            )
    return names


def starts_in(module: str, tree: ast.Module) -> list[Start]:
    """Every job registration in one parsed module.

    Split out from the directory walk so the resolver can be exercised against a planted
    module below: a scan proved only against the tree it already passes on is a scan whose
    blind spots are exactly the ones nobody looked for.
    """
    constants = _module_constants(tree)
    receivers = _registry_names(tree)
    found: list[Start] = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        if node.func.attr != "start":
            continue
        keywords = {kw.arg: kw.value for kw in node.keywords if kw.arg is not None}
        splat = any(kw.arg is None for kw in node.keywords)
        on_registry = _receiver_tail(node.func.value) in receivers
        if not on_registry and "kind" not in keywords:
            # Not a job registration at all — some other object's `start()`.
            continue

        argument = keywords.get("kind")
        if argument is None:
            # `kind` is keyword-only with a default; a registration that omits it gets
            # that default. A `**kwargs` splat could carry one, and cannot be read.
            kind = None if splat else DEFAULT_KIND
            spelling = "**kwargs" if splat else f"(omitted — defaults to {DEFAULT_KIND!r})"
        elif isinstance(argument, ast.Constant) and isinstance(argument.value, str):
            kind, spelling = argument.value, ast.unparse(argument)
        elif isinstance(argument, ast.Name) and argument.id in constants:
            # The half that was missing: `kind=BID_KIND` is an `ast.Name`, and it is the
            # one acting kind in the repository.
            kind, spelling = constants[argument.id], ast.unparse(argument)
        else:
            kind, spelling = None, ast.unparse(argument)

        found.append(
            Start(
                module=module,
                lineno=node.lineno,
                kind=kind,
                spelling=spelling,
                has_stop="stop" in keywords,
                is_armed="armed" in keywords,
            )
        )
    return found


def _registered() -> list[Start]:
    """`(kind, has a stop)` for every `registry.start(kind=...)` in the endpoints."""
    found: list[Start] = []
    for path in sorted(ENDPOINTS.glob("*.py")):
        found.extend(starts_in(path.name, ast.parse(path.read_text(encoding="utf-8"))))
    return found


def test_every_acting_kind_is_started_with_a_stop() -> None:
    starts = _registered()

    assert starts, "no registry.start(kind=...) call found — this scan reads nothing"
    unstoppable = [
        f"{start.module}:{start.lineno} kind={start.kind!r}"
        for start in starts
        if start.kind in ACTING_KINDS and not start.has_stop
    ]
    assert not unstoppable, (
        f"these acting jobs are registered with no `stop=`: {unstoppable}. `registry.stop` "
        "answers False for them and the route 409s — a disarm that cannot disarm."
    )


def test_every_start_call_is_one_the_scan_could_read() -> None:
    """The half that made this file vacuous twice over: a call it cannot fold was skipped.

    `kind=BID_KIND` is an `ast.Name`, so the constant-only reader dropped the single acting
    registration and every assertion downstream stayed green over eleven harmless ones.
    Silence about a call is now a failure, because a guard that narrows its own input
    without saying so is indistinguishable from one that found nothing wrong.
    """
    unreadable = [
        f"{start.module}:{start.lineno} kind={start.spelling}"
        for start in _registered()
        if start.kind is None
    ]

    assert not unreadable, (
        f"the scan could not resolve the kind of: {unreadable}. Spell it as a string "
        "literal or as a module-level constant, or teach this resolver the new shape — "
        "a kind it cannot read is a job it cannot police."
    )


def test_every_acting_kind_is_a_kind_something_registers() -> None:
    """The other direction, so the set cannot go stale.

    An `ACTING_KINDS` entry naming a job no endpoint starts any more is the empty-set
    failure wearing a full set's clothes: every assertion passes and nothing is checked.
    """
    registered = {start.kind for start in _registered()}

    orphaned = sorted(ACTING_KINDS - registered)
    assert not orphaned, (
        f"{orphaned} is named as an acting kind and no endpoint registers it. Either the "
        "job moved and this set has to follow, or it is gone and the entry must go too."
    )


def test_the_scan_finds_exactly_the_kinds_it_claims_to() -> None:
    """Equality, because `<=` over three of twelve cannot see the scan narrow.

    The old form pinned `{"lega-sync", "harvest-collect", "auth-login"}` with `<=`. Those
    three are spelled the plainest way there is, so a change that blinded the walk to a
    *different* spelling — an aliased import, `jobs.registry.start(...)`, a registration
    that reaches the registry through anything but a bare name — would drop the other nine
    and leave this assertion, and every assertion built on `_registered()`, passing over
    a quarter of the file. That is the same narrowing `test_outcomes.py` was reopened for,
    in the same week, three directories away.

    `PINNED_KINDS` therefore names all twelve and this compares for equality. A new job
    fails here on the day it is registered, which is the moment to ask whether it acts.
    """
    kinds = {start.kind for start in _registered()}

    assert kinds == set(PINNED_KINDS), (
        f"the scan read {sorted(k for k in kinds if k)} and this file pins "
        f"{sorted(PINNED_KINDS)}. Missing means the walk stopped seeing a registration it "
        "used to read — find the spelling before deleting the name. Extra means a new job "
        "kind, which has to be named here and weighed against `ACTING_KINDS`."
    )


def test_the_one_acting_kind_is_the_one_room_bid_registers() -> None:
    """Pinned against the source of truth, not restated.

    `ACTING_KINDS` holds a literal on purpose — it is a ratchet, and a set derived from the
    endpoints would grow itself and assert nothing. But a literal that has drifted from the
    constant it names is worse than either, so the two are compared here.
    """
    from fantabot_app.api.v1.endpoints.room_bid import BID_KIND

    assert BID_KIND in ACTING_KINDS, (
        f"room_bid registers {BID_KIND!r} and ACTING_KINDS names {sorted(ACTING_KINDS)} — "
        "the one job that spends credits is outside the set that requires a `stop=`."
    )


def test_a_job_started_with_armed_is_an_acting_job() -> None:
    """Derived, so the hand-maintained set cannot silently miss the next one.

    `armed=` is what a registration passes when the run it starts may write to a live
    platform — `room_bid.py` is the only caller today. Deriving acting-ness from it means a
    second acting job fails this file on the day it is written, rather than waiting for
    somebody to remember `ACTING_KINDS`.
    """
    armed = [start for start in _registered() if start.is_armed]

    assert armed, "no job is registered with `armed=` — this assertion reads nothing"
    unnamed = sorted(
        f"{start.module}:{start.lineno} kind={start.kind!r}"
        for start in armed
        if start.kind not in ACTING_KINDS
    )
    assert not unnamed, (
        f"{unnamed} pass `armed=` and are not in ACTING_KINDS. A job that can be armed is "
        "a job that can act, and an acting job must be stoppable."
    )
    unstoppable = sorted(f"{s.module}:{s.lineno}" for s in armed if not s.has_stop)
    assert not unstoppable, f"{unstoppable} can be armed and cannot be stopped"


def test_and_would_notice_an_unstoppable_one() -> None:
    """The resolver, against a planted module holding both spellings of a kind.

    The constant one is the case this file was blind to for the whole life of the job it
    was written for, so it is the one planted first.
    """
    planted = ast.parse(
        "BID = 'acting-thing'\n"
        "registry.start(job, kind=BID)\n"
        "registry.start(job.run, kind='stoppable-thing', stop=job.stop, armed=True)\n"
        "thread.start()\n"
    )

    found = starts_in("planted.py", planted)

    assert [(s.kind, s.has_stop, s.is_armed) for s in found] == [
        ("acting-thing", False, False),
        ("stoppable-thing", True, True),
    ]


def test_the_scan_reads_every_spelling_of_the_registry() -> None:
    """The widening, enumerated — including what it must still refuse.

    `registry.start(...)` is the before-set and is row one. The next two are what the
    `isinstance(ast.Name) and id == REGISTRY` test dropped: a registry reached through an
    attribute, and one imported under another name. Both were *silently* skipped when they
    carried no `kind=`, which is the only shape where the skip is invisible.

    Row four is the widening's limit and the reason it is not "any `.start(`": a plain
    `thread.start()` is not a job registration, and reading it as one would report an
    unresolvable kind for every thread in the tree.
    """
    planted = ast.parse(
        "from fantabot_app.api.infrastructure.jobs import registry\n"
        "from fantabot_app.api.infrastructure.jobs import registry as job_registry\n"
        "registry.start(job, kind='plain')\n"
        "jobs.registry.start(job, kind='through-an-attribute')\n"
        "job_registry.start(job, kind='under-an-alias')\n"
        "thread.start()\n"
    )

    found = starts_in("planted.py", planted)

    assert [s.kind for s in found] == ["plain", "through-an-attribute", "under-an-alias"]


def test_a_registration_with_no_kind_is_read_rather_than_skipped() -> None:
    """The half the widening buys, and the only place it is observable.

    A registration that passes `kind=` is read whatever its receiver — the walk keeps any
    `.start(kind=...)` call precisely so an unreadable one cannot hide. So a *kinded* row
    cannot tell whether the receiver was recognised, and the row above that spelled the
    registry under an alias with a `kind=` proved nothing about the alias at all: deleting
    `_registry_names`' alias arm left the whole file green. Two guards on one question, and
    the first goes unobservable.

    An **omitted** `kind=` is the shape where recognising the receiver is the only thing
    that decides. `jobs.registry.start(job)` and `job_registry.start(job)` were both
    dropped in silence and `ACTING_KINDS` was policed over one job fewer; they now read as
    the default kind, which is the kind they really get. `thread.start()` is the limit —
    still not a registration, or every thread in the tree would report an unreadable kind.
    """
    planted = ast.parse(
        "from fantabot_app.api.infrastructure.jobs import registry\n"
        "from fantabot_app.api.infrastructure.jobs import registry as job_registry\n"
        "jobs.registry.start(job)\n"
        "job_registry.start(job)\n"
        "thread.start()\n"
    )

    found = starts_in("planted.py", planted)

    assert [(s.kind, s.has_stop) for s in found] == [
        (DEFAULT_KIND, False),
        (DEFAULT_KIND, False),
    ]


def test_and_would_notice_a_kind_it_cannot_read() -> None:
    """The new failure mode, planted: an unresolvable `kind=` is reported, never dropped."""
    planted = ast.parse("registry.start(job, kind=kinds['bid'])\n")

    found = starts_in("planted.py", planted)

    assert [(s.kind, s.spelling) for s in found] == [(None, "kinds['bid']")]
