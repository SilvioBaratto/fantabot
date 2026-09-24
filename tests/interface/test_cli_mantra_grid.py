"""`fantabot mantra-grid`, the Typer body. **Zero sockets, zero agent calls.**

The collector itself is tested in `tests/application/`; what is pinned here is the wiring
around it, which nothing executed before — measured 2026-09-24 at **0 of 33 body
statements**, one of five commands whose body no test entered at all.

Four of the decisions in that body are only visible from this side:

* **Every failure exits non-zero, and they are not the same code.** An unresolved model is
  `2` (the operator passed something wrong) and a collector or gate failure is `1` (the run
  did not produce a usable answer). A cron wrapper reads that number.
* **`strip_dangerous_env` runs before `collect`.** It is the whole protection between the
  agent subprocess and this shell's environment, and an ordering is exactly what a reader
  cannot check by looking at two adjacent lines that both "happen".
* **A failed gate writes nothing.** `result.ok` False must not reach `write_json`, because
  the two files it would overwrite are the matcher's input.
* **The output goes into the package**, `data_dir()`, not the working directory — the
  property `domain/shared/resources.py` exists for.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from typer.testing import CliRunner

from fantabot.interface.app import app

runner = CliRunner()


class _Model:
    """A stand-in for `SchemaGrid`/`CompatMatrix`: `len(.schemi)` and `model_dump()`."""

    def __init__(self, tag: str, schemi: int = 11) -> None:
        self.tag = tag
        self.schemi = tuple(range(schemi))

    def model_dump(self) -> dict[str, str]:
        return {"tag": self.tag}


def _result(*, ok: bool = True, problems: list[str] | None = None) -> SimpleNamespace:
    found = problems or []
    return SimpleNamespace(
        ok=ok and not found,
        grid=_Model("grid"),
        matrix=_Model("matrix"),
        problems=found,
    )


@pytest.fixture
def wired(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> SimpleNamespace:
    """Every seam the body reaches, recording rather than doing.

    The imports in the body are function-local, so they read these module attributes when
    the command runs — patching the source modules is what reaches them.
    """
    from fantabot.adapters.agent import env as agent_env
    from fantabot.adapters.files import mantra_writer
    from fantabot.application import mantra_collector
    from fantabot.config import settings
    from fantabot.domain.shared import resources

    calls: list[str] = []
    written: list[tuple[Path, Any]] = []
    state = SimpleNamespace(result=_result(), raises=None, model="resolved-model")

    # `Settings` is a pydantic model and refuses an instance attribute, so the method is
    # replaced on the class; monkeypatch puts it back.
    monkeypatch.setattr(
        type(settings), "resolve_agent_model", lambda _self, _override="": state.model
    )
    monkeypatch.setattr(agent_env, "strip_dangerous_env", lambda: calls.append("strip"))

    async def _collect(model: str) -> Any:
        calls.append(f"collect:{model}")
        if state.raises is not None:
            raise state.raises
        return state.result

    monkeypatch.setattr(mantra_collector, "collect", _collect)
    monkeypatch.setattr(
        mantra_writer, "write_json", lambda path, model: written.append((path, model))
    )
    monkeypatch.setattr(resources, "data_dir", lambda: tmp_path)
    return SimpleNamespace(calls=calls, written=written, state=state, out=tmp_path)


def test_unresolved_model_exits_2_and_never_collects(
    wired: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A model that cannot be resolved is the operator's input, so it is exit 2 — and it
    refuses *before* the environment is stripped or the agent is called."""
    from fantabot.config import settings

    def _boom(_self: object, _override: str = "") -> str:
        raise RuntimeError("no model: set FANTABOT_AGENT_MODEL or pass --model")

    monkeypatch.setattr(type(settings), "resolve_agent_model", _boom)

    result = runner.invoke(app, ["mantra-grid"])

    assert result.exit_code == 2
    assert "no model" in result.output
    assert wired.calls == []


def test_the_environment_is_stripped_before_the_agent_is_called(
    wired: SimpleNamespace,
) -> None:
    """Ordering, not presence. `strip_dangerous_env` is the only thing between this
    shell's environment and the agent subprocess, so running it after `collect` would
    read as protection and be none."""
    runner.invoke(app, ["mantra-grid"])

    assert wired.calls == ["strip", "collect:resolved-model"]


def test_collect_error_exits_1_and_writes_nothing(wired: SimpleNamespace) -> None:
    from fantabot.application.mantra_collector import CollectError

    wired.state.raises = CollectError("the agent returned no parseable grid")

    result = runner.invoke(app, ["mantra-grid", "--write"])

    assert result.exit_code == 1
    assert "no parseable grid" in result.output
    assert wired.written == []


def test_a_failed_gate_writes_nothing_and_names_every_problem(
    wired: SimpleNamespace,
) -> None:
    """`--write` is given and still nothing is written: the gate is what decides, and the
    two files it would overwrite are the matcher's own input."""
    wired.state.result = _result(problems=["4-3-1-2 slot 9 has one role", "matrix short"])

    result = runner.invoke(app, ["mantra-grid", "--write"])

    assert result.exit_code == 1
    assert "2 gate failures" in result.output
    assert "4-3-1-2 slot 9 has one role" in result.output
    assert "matrix short" in result.output
    assert wired.written == []


def test_without_write_it_prints_and_saves_nothing(wired: SimpleNamespace) -> None:
    result = runner.invoke(app, ["mantra-grid"])

    assert result.exit_code == 0
    assert "All gates passed: 11 schemas." in result.output
    assert "nothing saved" in result.output
    assert wired.written == []


def test_write_lands_both_files_in_the_package_data_dir(wired: SimpleNamespace) -> None:
    """Not the working directory. `asta legality` reads these through `data_dir()`, and
    writing them anywhere else means reader and writer agree only when the process
    happened to start in the repository root."""
    from fantabot.domain.shared.resources import COMPAT_FILENAME, SCHEMI_FILENAME

    result = runner.invoke(app, ["mantra-grid", "--write"])

    assert result.exit_code == 0
    assert [path for path, _ in wired.written] == [
        wired.out / SCHEMI_FILENAME,
        wired.out / COMPAT_FILENAME,
    ]
    assert [model.tag for _, model in wired.written] == ["grid", "matrix"]


def test_a_written_run_says_the_files_need_verifying_by_hand(
    wired: SimpleNamespace,
) -> None:
    """Requested only for its seams, not read: the collector is an LLM and the output is the matcher's input; the one thing the
    command can do about that is refuse to let it pass silently."""
    result = runner.invoke(app, ["mantra-grid", "--write"])

    assert "Verify both by hand" in result.output
    assert wired.written
