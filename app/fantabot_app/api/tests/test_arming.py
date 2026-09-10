"""The arming contract: two locks, both named, neither remembered.

`CLAUDE.md` records why there are two: `FANTABOT_AUTO_ACT` is process-wide `.env` state, so
flipping it arms every invocation at once, and *"the operator who edits it in the morning is
not the one at the keyboard at 21:47"*. `--arm` is the positive, per-invocation half.

A browser makes the second half harder than a terminal does. A page can be reloaded,
restored by a session manager, or left open overnight, and none of those may carry an arming
decision forward — so `arm` is a body field with **no default**, and a request that does not
say is a 422 rather than a dry run.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from fantabot_app.api.arming import (
    ARM_NOT_REQUESTED,
    AUTO_ACT_OFF,
    Arming,
    decide_arming,
)

API = Path(__file__).resolve().parent.parent


class TestBothLocks:
    def test_both_open_arms(self) -> None:
        assert decide_arming(arm=True, auto_act=True) == Arming(armed=True, closed=())

    @pytest.mark.parametrize(
        ("arm", "auto_act", "expected"),
        [
            (False, True, (ARM_NOT_REQUESTED,)),
            (True, False, (AUTO_ACT_OFF,)),
            (False, False, (AUTO_ACT_OFF, ARM_NOT_REQUESTED)),
        ],
        ids=["arm withheld", "env off", "both shut"],
    )
    def test_every_shut_lock_is_named(
        self, arm: bool, auto_act: bool, expected: tuple[str, ...]
    ) -> None:
        """**Both**, not the first. The CLI reports one — a ternary over two causes — so an
        operator with both shut fixes one, retries, and is told about the other. Ten minutes
        go into editing the wrong file."""
        decision = decide_arming(arm=arm, auto_act=auto_act)

        assert decision.armed is False
        assert decision.closed == expected

    def test_the_reason_names_them_all_in_one_line(self) -> None:
        decision = decide_arming(arm=False, auto_act=False)

        assert AUTO_ACT_OFF in decision.reason
        assert ARM_NOT_REQUESTED in decision.reason

    def test_an_armed_decision_has_nothing_to_explain(self) -> None:
        assert decide_arming(arm=True, auto_act=True).reason == ""

    def test_the_ambient_lock_is_named_first(self) -> None:
        """It outlives the request, so it is the one to fix first — and the one an operator
        is least likely to suspect, because nothing in the browser shows it."""
        assert decide_arming(arm=False, auto_act=False).closed[0] == AUTO_ACT_OFF

    def test_the_two_messages_are_distinguishable(self) -> None:
        """One message for two causes is the defect. Neither may contain the other."""
        assert AUTO_ACT_OFF != ARM_NOT_REQUESTED
        assert AUTO_ACT_OFF not in ARM_NOT_REQUESTED
        assert ARM_NOT_REQUESTED not in AUTO_ACT_OFF

    def test_the_app_does_not_tell_a_browser_about_a_flag_it_cannot_pass(self) -> None:
        """The CLI says "--arm not given". There is no flag in an HTTP request, and a
        message naming one sends the reader to a terminal they are not using."""
        assert "--arm" not in ARM_NOT_REQUESTED


class TestItIsReadPerRequest:
    def test_the_env_is_read_now_and_not_at_import(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`.env` is edited between requests. A value captured at import would keep
        answering with the state of the world when the process started."""
        from fantabot.config import settings

        monkeypatch.setattr(settings, "fantabot_auto_act", False, raising=False)
        assert decide_arming(arm=True).armed is False

        monkeypatch.setattr(settings, "fantabot_auto_act", True, raising=False)
        assert decide_arming(arm=True).armed is True

    def test_nothing_captures_it_at_module_scope(self) -> None:
        """A module-level `AUTO_ACT = settings.fantabot_auto_act` would freeze it, and read
        exactly like the per-request version at every call site."""
        source = (API / "arming.py").read_text(encoding="utf-8")
        module = ast.parse(source)
        frozen = [
            node.lineno
            for node in module.body
            if isinstance(node, ast.Assign | ast.AnnAssign)
            and "fantabot_auto_act" in ast.dump(node)
        ]

        assert not frozen, f"the ambient lock is captured at import at {frozen}"


class TestArmIsNeverRemembered:
    """The property a browser makes hard: the operator who armed it is the one watching."""

    def test_the_decision_is_frozen(self) -> None:
        with pytest.raises((AttributeError, TypeError)):
            decide_arming(arm=True, auto_act=True).armed = False  # type: ignore[misc]

    def test_no_module_state_holds_it(self) -> None:
        """Nothing here may accumulate an arming decision between calls.

        Mutable *literals* only. `__all__` is a list and is a declaration, and
        `ACTING_KINDS = frozenset()` is a call whose result cannot accumulate — a guard that
        flagged either would be describing its exemptions rather than its subject.
        """
        module = ast.parse((API / "arming.py").read_text(encoding="utf-8"))
        mutable = [
            node.lineno
            for node in module.body
            if isinstance(node, ast.Assign)
            and isinstance(node.value, ast.Dict | ast.List | ast.Set)
            and not all(
                isinstance(t, ast.Name) and t.id.startswith("__") for t in node.targets
            )
        ]

        assert not mutable, f"module-level mutable state at {mutable} could remember an arm"

    def test_that_guard_would_catch_a_cache(self) -> None:
        """A ban that cannot fire reads as compliance."""
        planted = ast.parse("_armed_by_session = {}\n__all__ = ['x']\n")
        flagged = [
            node.lineno
            for node in planted.body
            if isinstance(node, ast.Assign)
            and isinstance(node.value, ast.Dict | ast.List | ast.Set)
            and not all(
                isinstance(t, ast.Name) and t.id.startswith("__") for t in node.targets
            )
        ]

        assert flagged == [1], "the guard misses a cache, or flags __all__"

    def test_two_calls_do_not_influence_each_other(self) -> None:
        assert decide_arming(arm=True, auto_act=True).armed is True
        assert decide_arming(arm=False, auto_act=True).armed is False
        assert decide_arming(arm=True, auto_act=True).armed is True


class TestAnActingJobMustBeStoppable:
    """`JobRegistry.stop` returns `False` for a thread job — a daemon thread cannot be
    interrupted from outside — and the route turns that into a 409.

    A disarm control that answers 409 is a lie, and it is a lie told at the one moment it
    matters. So an acting job has to be a `ProcessJob`, over 0.5's stop flag.

    `ACTING_KINDS` is empty until 3.3 registers the first one. The guard exists first on
    purpose: the rule is in place before there is anything to break it.
    """

    def test_every_acting_kind_is_started_with_a_stop(self) -> None:
        from fantabot_app.api.arming import ACTING_KINDS

        starts: list[tuple[str, bool]] = []
        for path in (API / "v1" / "endpoints").glob("*.py"):
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
                    continue
                if node.func.attr != "start":
                    continue
                keywords = {kw.arg for kw in node.keywords}
                kind = next(
                    (
                        kw.value.value
                        for kw in node.keywords
                        if kw.arg == "kind" and isinstance(kw.value, ast.Constant)
                    ),
                    None,
                )
                if kind is not None:
                    starts.append((str(kind), "stop" in keywords))

        assert starts, "no registry.start(kind=...) call found — this scan reads nothing"
        unstoppable = [kind for kind, has_stop in starts if kind in ACTING_KINDS and not has_stop]
        assert not unstoppable, (
            f"these acting jobs are registered with no `stop=`: {unstoppable}. "
            "`registry.stop` answers False for them and the route 409s — a disarm that "
            "cannot disarm."
        )

    def test_the_scan_would_catch_one(self) -> None:
        """A guard over an empty set passes trivially; this proves the walk finds a kind."""
        planted = ast.parse('registry.start(job, kind="acting-thing")')
        kinds = [
            kw.value.value
            for node in ast.walk(planted)
            if isinstance(node, ast.Call)
            for kw in node.keywords
            if kw.arg == "kind" and isinstance(kw.value, ast.Constant)
        ]

        assert kinds == ["acting-thing"]
