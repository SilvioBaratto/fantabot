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
    def test_the_import_time_singleton_is_not_what_is_read(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The inverse of `TestTheAmbientLockIsReadFromDisk`, and the mutation that catches it.

        This test used to *set* `settings.fantabot_auto_act` and assert `decide_arming`
        agreed — which is the attribute the old implementation read, so it passed whether or
        not anything was re-read, and its docstring's "`.env` is edited between requests"
        never happened. Reading the singleton is now the defect, so the singleton is the
        thing this pins: turned to `True` while the world says otherwise, it must not arm.

        `settings` is built once at first import (`config.py`'s `settings = Settings()`).
        That is fine for the CLI, one process per invocation. It is what left the app server
        armed after the operator edited `.env` to disarm.
        """
        from fantabot import config

        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("FANTABOT_AUTO_ACT", raising=False)
        config.note_dotenv_injection(tmp_path / ".env", set())
        monkeypatch.setattr(config.settings, "fantabot_auto_act", True, raising=False)

        assert decide_arming(arm=True).armed is False
        assert decide_arming(arm=True).closed == (AUTO_ACT,)

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


class TestTheAmbientLockIsReadFromDisk:
    """The ambient lock, against a real `.env` on a real filesystem.

    `test_the_env_is_read_now_and_not_at_import` above claims to cover this and cannot:
    it monkeypatches `settings.fantabot_auto_act` — the attribute `decide_arming` reads —
    so it passes whether or not anything is re-read, and its docstring's "`.env` is edited
    between requests" never happens. These tests edit the file.

    Measured before the fix, in the app's own boot shape: `.env` flipped to false on disk,
    `os.environ` flipped to false, and `decide_arming(arm=True).armed` stayed `True` through
    both. `arming.py`'s module docstring promised the opposite in as many words.
    """

    def test_editing_the_dotenv_disarms_a_running_process(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The 21:47 property, and the reason the lock is ambient at all.

        A long-lived app server is the surface this is about: the CLI is one process per
        invocation, so per-process and per-request are the same thing there and the defect
        is invisible. The operator disarms by editing `.env` and does not restart.
        """
        from fantabot import config

        env = tmp_path / ".env"
        env.write_text("FANTABOT_AUTO_ACT=true\n")
        monkeypatch.chdir(tmp_path)
        # What `load_configuration` does at app import: copy the file into `os.environ`
        # with `override=False`. This is what made the file invisible afterwards.
        monkeypatch.setenv("FANTABOT_AUTO_ACT", "true")
        config.note_dotenv_injection(env, {"FANTABOT_AUTO_ACT"})

        assert decide_arming(arm=True).armed is True

        env.write_text("FANTABOT_AUTO_ACT=false\n")

        assert decide_arming(arm=True).armed is False
        assert decide_arming(arm=True).closed == (AUTO_ACT,)

    def test_an_exported_variable_still_wins_over_the_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Root `CLAUDE.md`, on `FANTABOT_HARVEST_DIR`: *"An exported variable still wins."*

        The re-read must not invert that. A variable genuinely exported into the process —
        as opposed to one a launcher copied out of `.env` — is the operator speaking later
        than the file, so it outranks it.
        """
        from fantabot import config

        env = tmp_path / ".env"
        env.write_text("FANTABOT_AUTO_ACT=false\n")
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("FANTABOT_AUTO_ACT", "true")
        config.note_dotenv_injection(env, set())  # exported before boot: not injected

        assert decide_arming(arm=True).armed is True

    def test_an_exported_variable_is_re_read_too(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Not only the file. Whatever the source, the answer is current."""
        from fantabot import config

        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("FANTABOT_AUTO_ACT", "true")
        config.note_dotenv_injection(tmp_path / ".env", set())

        assert decide_arming(arm=True).armed is True

        monkeypatch.setenv("FANTABOT_AUTO_ACT", "false")

        assert decide_arming(arm=True).armed is False

    def test_nothing_anywhere_is_closed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """No file, no variable: the default is `false` and stays `false`.

        Root `CLAUDE.md`: *"`FANTABOT_AUTO_ACT` defaults to `false` — deliberate. Don't flip
        the default."* A re-read that failed open would do exactly that.
        """
        from fantabot import config

        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("FANTABOT_AUTO_ACT", raising=False)
        config.note_dotenv_injection(tmp_path / ".env", set())

        assert decide_arming(arm=True).armed is False
        assert decide_arming(arm=True).closed == (AUTO_ACT,)

    def test_an_unreadable_dotenv_fails_closed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A file that cannot be read is not permission to act.

        The whole point of the ambient lock is that it is the *conservative* one; a
        re-read that treated an I/O error as "carry on" would make the failure mode
        arming rather than refusing.
        """
        from fantabot import config

        env = tmp_path / ".env"
        env.write_text("FANTABOT_AUTO_ACT=true\n")
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("FANTABOT_AUTO_ACT", "true")
        config.note_dotenv_injection(env, {"FANTABOT_AUTO_ACT"})
        assert decide_arming(arm=True).armed is True

        env.unlink()

        assert decide_arming(arm=True).armed is False
