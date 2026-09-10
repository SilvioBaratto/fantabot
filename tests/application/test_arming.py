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

import pytest
from _paths import module_file

from fantabot.application.arming import (
    ARM,
    AUTO_ACT,
    CLI_SENTENCES,
    Arming,
    decide_arming,
)

ARMING = module_file("fantabot.application.arming")


class TestBothLocks:
    def test_both_open_arms(self) -> None:
        assert decide_arming(arm=True, auto_act=True) == Arming(armed=True, closed=())

    @pytest.mark.parametrize(
        ("arm", "auto_act", "expected"),
        [
            (False, True, (ARM,)),
            (True, False, (AUTO_ACT,)),
            (False, False, (AUTO_ACT, ARM)),
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
        line = decide_arming(arm=False, auto_act=False).because(CLI_SENTENCES)

        assert CLI_SENTENCES[AUTO_ACT] in line
        assert CLI_SENTENCES[ARM] in line

    def test_an_armed_decision_has_nothing_to_explain(self) -> None:
        assert decide_arming(arm=True, auto_act=True).because(CLI_SENTENCES) == ""

    def test_the_ambient_lock_is_named_first(self) -> None:
        """It outlives the request, so it is the one to fix first — and the one an operator
        is least likely to suspect, because nothing in the browser shows it."""
        assert decide_arming(arm=False, auto_act=False).closed[0] == AUTO_ACT

    def test_the_two_messages_are_distinguishable(self) -> None:
        """One message for two causes is the defect. Neither may contain the other."""
        ambient, per_call = CLI_SENTENCES[AUTO_ACT], CLI_SENTENCES[ARM]

        assert ambient != per_call
        assert ambient not in per_call
        assert per_call not in ambient

    def test_the_locks_are_shared_by_name_and_worded_per_surface(self) -> None:
        """The ambient lock is the same fact everywhere; the per-invocation one is a
        `--arm` flag in a terminal and a body field in a request. A shared sentence would
        have to name one surface's control and send the other's reader somewhere they are
        not — so the *fact* is shared and the wording is local."""
        assert "--arm" in CLI_SENTENCES[ARM]
        assert "--arm" not in ARM, "the lock's name must not carry a surface's control"
        assert set(CLI_SENTENCES) == {AUTO_ACT, ARM}


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
        source = ARMING.read_text(encoding="utf-8")
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
        module = ast.parse(ARMING.read_text(encoding="utf-8"))
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
