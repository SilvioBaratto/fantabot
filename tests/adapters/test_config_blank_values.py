"""A blank line in `.env` means "unset", for every setting.

`settings = Settings()` runs at import, so a value pydantic refuses stops every command at
the import line — `config-check` included, the one screen meant to say what is wrong.
`.env.example` ships `FANTABOT_LEAGUE_ID=` blank, and copying it unchanged did exactly that:
`""` is not an `int`. A blank `FANTABOT_AUTO_ACT` was the same crash. Two kinds of blank did
not crash, which is worse: `Path("")` is the working directory, and a blank text setting
replaced a working default with `""` — the template's `FANTABOT_AGENT_AUTH_TOKEN=` sent an
empty token to a shim the CLI then refused, and a blank base URL or model breaks the same way.

So the rule is one rule, for every field: blank is the default. A field whose default is
already `""` or `None` reads exactly as before.

A blank reads as the default whether it comes from `.env` or the environment, so an exported
`FANTABOT_LEAGUE_ID=` still overrides the file, as an exported value always has.
`env_ignore_empty` was the other way to read a blank as unset, and it would have made an
exported blank fall through to the file instead.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from fantabot import config
from fantabot.adapters.agent.options import agent_env
from fantabot.config import Settings

REPO = Path(__file__).resolve().parents[2]
EVERY_SETTING = sorted(Settings.model_fields)


@pytest.fixture(autouse=True)
def _no_ambient_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """This machine exports `FANTABOT_HARVEST_DIR`; any exported setting would decide these."""
    for name in Settings.model_fields:
        monkeypatch.delenv(name.upper(), raising=False)


def _template(tmp_path: Path, extra: str = "") -> Path:
    env = tmp_path / ".env"
    env.write_text(
        (REPO / ".env.example").read_text(encoding="utf-8") + extra, encoding="utf-8"
    )
    return env


def test_the_shipped_template_loads_unchanged(tmp_path: Path) -> None:
    """The regression itself: a fresh `.env` copied from the template must not crash, and
    every line it leaves blank must read as that setting's default."""
    loaded = Settings(_env_file=_template(tmp_path))

    assert loaded.fantabot_league_id == 0
    assert loaded.fantabot_auto_act is False
    assert loaded.fantabot_agent_auth_token == Settings(_env_file=None).fantabot_agent_auth_token


def test_a_shim_configured_from_the_template_gets_a_token(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The symptom, end to end: the operator sets only the base URL, as the template's own
    Ollama example does, and the CLI must not be handed an empty `ANTHROPIC_AUTH_TOKEN`."""
    env = _template(tmp_path, "\nFANTABOT_AGENT_BASE_URL=http://localhost:11434\n")
    monkeypatch.setattr(config, "settings", Settings(_env_file=env))

    assert agent_env()["ANTHROPIC_AUTH_TOKEN"]


@pytest.mark.parametrize("source", ["dotenv", "environ"])
@pytest.mark.parametrize("blank", ["", "   "])
@pytest.mark.parametrize("name", EVERY_SETTING)
def test_a_blank_setting_is_its_default(
    name: str, blank: str, source: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Blank or whitespace, the setting is what it would be with the line absent.

    Both sources, because they differ: python-dotenv strips a `.env` value, so whitespace
    reaches the validator only from an exported variable.
    """
    default = getattr(Settings(_env_file=None), name)
    env = tmp_path / ".env"
    if source == "dotenv":
        env.write_text(f"{name.upper()}={blank}\n", encoding="utf-8")
    else:
        env.write_text("", encoding="utf-8")
        monkeypatch.setenv(name.upper(), blank)

    assert getattr(Settings(_env_file=env), name) == default


@pytest.mark.parametrize(
    ("name", "value", "expected"),
    [
        ("fantabot_league_id", "4103937", 4103937),
        ("fantabot_auto_act", "true", True),
        ("fantabot_data_dir", "/srv/fantabot", Path("/srv/fantabot")),
    ],
)
def test_a_real_value_is_still_read(
    name: str, value: str, expected: object, tmp_path: Path
) -> None:
    """The validator steps aside for anything that is not blank."""
    env = tmp_path / ".env"
    env.write_text(f"{name.upper()}={value}\n", encoding="utf-8")

    assert getattr(Settings(_env_file=env), name) == expected


@pytest.mark.parametrize(
    ("name", "value"), [("fantabot_league_id", "abc"), ("fantabot_auto_act", "maybe")]
)
def test_a_wrong_value_still_fails_loudly(name: str, value: str, tmp_path: Path) -> None:
    """Blank is the one value read as unset. A typo is not silently replaced by a default."""
    env = tmp_path / ".env"
    env.write_text(f"{name.upper()}={value}\n", encoding="utf-8")

    with pytest.raises(ValidationError, match=name):
        Settings(_env_file=env)


def test_an_exported_blank_still_overrides_the_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The environment outranks `.env`, and a blank there means the default, not the file."""
    env = tmp_path / ".env"
    env.write_text("FANTABOT_LEAGUE_ID=4103937\nLEGA_EMAIL=someone@example.com\n", encoding="utf-8")
    monkeypatch.setenv("FANTABOT_LEAGUE_ID", "")
    monkeypatch.setenv("LEGA_EMAIL", "")

    loaded = Settings(_env_file=env)

    assert loaded.fantabot_league_id == 0
    assert loaded.lega_email == ""
