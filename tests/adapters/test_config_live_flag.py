"""One parse for the opt-in lineup flags, and one list of the lineup settings.

The news gate and the opponent adjustment each carried their own copy of "an explicit yes,
case and whitespace aside", and the four settings were named by hand in `conftest.py` and in
the default-path guard as well as in `config.py`. A copy is free to drift: a fifth setting
added in `config.py` alone is read from the operator's own `.env` by every test, and a flag
whose parse was edited in one place reads `onn` differently from its sibling.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import pytest

import fantabot.config as config
from fantabot.application.lineup_projection import matchup_enabled
from fantabot.application.lineup_refresh import LiveRefreshSources

#: A name no operator sets, so a stray value in the environment or the repository's `.env`
#: cannot answer for it.
FLAG = "FANTABOT_TEST_ONLY_FLAG"

RAW = [
    None, "", " ", "0", "false", "no", "off", "garbage", "2", "onn", "1 1", "y", "t", "enabled",
    "1", "true", "yes", "on", " ON ", "True", "YES",
]
ON = {"1", "true", "yes", "on"}


def _expected(raw: str | None) -> bool:
    return raw is not None and raw.strip().lower() in ON


@pytest.fixture
def put(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Any:
    """`live_setting` under the test's control through exactly one of its two layers.

    The environment answers when the name is there and the working directory's `.env` is
    re-read otherwise, so the working directory is an empty `tmp_path` and the injection
    registry is cleared: the repository's own file never answers.
    """
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(config, "_DOTENV_INJECTED", {})

    def setter(name: str, raw: str | None, source: str) -> None:
        """Set `name` to `raw` in one layer, or in neither when `raw` is `None`."""
        monkeypatch.delenv(name, raising=False)
        if raw is None:
            return
        if source == "environment":
            monkeypatch.setenv(name, raw)
        else:
            with (tmp_path / ".env").open("a", encoding="utf-8") as dotenv:
                dotenv.write(f"{name}={raw}\n")

    return setter


class TestLiveFlag:
    """`config.live_flag`, and the two gates that are answered by it."""

    @pytest.mark.parametrize("source", ["environment", "dotenv"])
    @pytest.mark.parametrize("raw", RAW)
    def test_only_an_explicit_yes_is_on(self, put: Any, source: str, raw: str | None) -> None:
        """Through each layer `live_setting` reads, since the flag cannot know which answered."""
        put(FLAG, raw, source)
        assert config.live_flag(FLAG) is _expected(raw)

    def test_it_is_read_every_call(self, put: Any) -> None:
        """An operator flips it between two hourly runs; nothing is remembered."""
        put(FLAG, "off", "environment")
        assert config.live_flag(FLAG) is False
        put(FLAG, "on", "environment")
        assert config.live_flag(FLAG) is True

    @pytest.mark.parametrize("source", ["environment", "dotenv"])
    @pytest.mark.parametrize("raw", RAW)
    def test_the_two_opt_in_gates_read_every_value_alike(
        self, put: Any, source: str, raw: str | None
    ) -> None:
        """The news gate and the opponent adjustment are both answered by `live_flag`, so a
        mistyped value turns neither on and a correct one turns both."""
        gates = LiveRefreshSources(reporter=object())  # type: ignore[arg-type]
        put(config.LINEUP_NEWS_VAR, raw, source)
        put(config.LINEUP_OPPONENT_VAR, raw, source)
        assert gates._news_on() is matchup_enabled() is _expected(raw)


class TestTheSettingList:
    """`config.LINEUP_SETTING_VARS`, from which the test side names the settings."""

    def test_it_holds_every_lineup_setting_the_module_declares(self) -> None:
        """A fifth `LINEUP_*_VAR` left out of the tuple fails here, not in the operator's `.env`."""
        declared = {
            value
            for name, value in vars(config).items()
            if re.fullmatch(r"LINEUP_[A-Z_]+_VAR", name)
        }
        assert declared, "the control: the pattern finds the constants it is meant to"
        assert set(config.LINEUP_SETTING_VARS) == declared
        assert len(set(config.LINEUP_SETTING_VARS)) == len(config.LINEUP_SETTING_VARS)

    def test_each_one_is_a_settings_field_so_the_dotenv_model_knows_it(self) -> None:
        """`Settings` ignores what it does not declare, so a setting added as a constant alone
        is invisible to the model that reads the same `.env`."""
        fields = config.Settings.model_fields
        for name in config.LINEUP_SETTING_VARS:
            assert name.lower() in fields, name

    def test_every_one_reads_empty_in_a_test_whatever_the_operator_has_set(self) -> None:
        """The autouse fixture in `conftest.py` is built from the same list."""
        for name in config.LINEUP_SETTING_VARS:
            assert os.environ[name] == "", name
