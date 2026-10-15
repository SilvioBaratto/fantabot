"""A blank line in `.env` means "unset", for every setting.

`settings = Settings()` runs at import, so a value pydantic refuses stops every command at
the import line — `config-check` included, the one screen meant to say what is wrong.
`.env.example` ships `FANTABOT_LEAGUE_ID=` blank, and copying it unchanged did exactly that:
`""` is not an `int`. A blank `FANTABOT_AUTO_ACT` was the same crash. Two kinds of blank did
not crash, which is worse: `Path("")` is the working directory, and a blank text setting
replaced a working default with `""` — the template's `FANTABOT_AGENT_AUTH_TOKEN=` sent an
empty token to a shim the CLI then refused, and a blank base URL or model breaks the same way.

So the rule is one rule, for every field but the database URLs: blank is the default. Those
two keep a blank as `""`, which fails at the first use, because a blank DSN is most often a
command substitution that came back empty. For the main URL the default is the canonical
database, so reading the blank as unset would point a migration at `fantabot`; the test URL
is exempt too because a blank test DSN must not silently pick a database either.

A blank reads as the default whether it comes from `.env` or the environment, so an exported
`FANTABOT_LEAGUE_ID=` still overrides the file, as an exported value always has.
`env_ignore_empty` was the other way to read a blank as unset, and it would have made an
exported blank fall through to the file instead.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from fantabot import config
from fantabot.adapters.agent.options import agent_env
from fantabot.application.config_report import InvalidDsn, safe_dsn
from fantabot.config import BLANK_DSN_FIELDS, Settings

REPO = Path(__file__).resolve().parents[2]
BLANK_IS_DEFAULT = sorted(set(Settings.model_fields) - BLANK_DSN_FIELDS)


@pytest.fixture(autouse=True)
def _no_ambient_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """This machine exports `FANTABOT_HARVEST_DIR`; any exported setting would decide these.

    Every spelling of the name, because `Settings` is `case_sensitive=False` and reads a
    lower-case `fantabot_league_id` just as it reads the upper-case one.
    """
    names = set(Settings.model_fields)
    for key in list(os.environ):
        if key.lower() in names:
            monkeypatch.delenv(key)


def test_a_lower_case_ambient_setting_does_not_decide_these_tests() -> None:
    """The fixture above, asked: the variable has to exist before the fixture runs, so this
    runs one of the file's own tests in a child that inherits it."""
    done = subprocess.run(
        [
            sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-q",
            str(Path(__file__)), "-k", "shipped_template_loads",
        ],
        cwd=REPO,
        env={**os.environ, "fantabot_league_id": "7"},
        capture_output=True,
        text=True,
        check=False,
    )

    assert done.returncode == 0, done.stdout[-1500:]


def _template(tmp_path: Path, extra: str = "") -> Path:
    env = tmp_path / ".env"
    env.write_text(
        (REPO / ".env.example").read_text(encoding="utf-8") + extra, encoding="utf-8"
    )
    return env


def test_the_shipped_template_loads_unchanged(tmp_path: Path) -> None:
    """The regression itself: a fresh `.env` copied from the template must not crash, and
    every line it leaves blank must read as that setting's default."""
    template = (REPO / ".env.example").read_text(encoding="utf-8")
    assert "\nFANTABOT_AGENT_AUTH_TOKEN=\n" in template, "the premise of the token checks"
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


@pytest.mark.parametrize("source", ["dotenv", "environ", "init"])
@pytest.mark.parametrize("blank", ["", "   ", "\t"])
@pytest.mark.parametrize("name", BLANK_IS_DEFAULT)
def test_a_blank_setting_is_its_default(
    name: str, blank: str, source: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Blank or whitespace, the setting is what it would be with the line absent.

    Both sources, because they differ: python-dotenv strips a `.env` value, so whitespace
    reaches the validator only from an exported variable.
    """
    default = getattr(Settings(_env_file=None), name)
    assert getattr(_with(name, blank, source, monkeypatch, tmp_path), name) == default


def _with(
    name: str, value: str, source: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> Settings:
    env = tmp_path / ".env"
    env.write_text(f"{name.upper()}={value}\n" if source == "dotenv" else "", encoding="utf-8")
    if source == "environ":
        monkeypatch.setenv(name.upper(), value)
    if source == "init":
        return Settings(_env_file=env, **{name: value})
    return Settings(_env_file=env)


@pytest.mark.parametrize("source", ["dotenv", "environ", "init"])
@pytest.mark.parametrize("blank", ["", "   ", "\t"])
@pytest.mark.parametrize("name", sorted(BLANK_DSN_FIELDS))
def test_a_blank_database_url_fails_rather_than_picking_a_database(
    name: str, blank: str, source: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`FANTABOT_DATABASE_URL="$(fantabot-app db url --database fantabot_test)"` with the
    command missing exports a blank, and the default would send that migration to the
    canonical database. A blank stays blank, and the DSN check refuses it.

    Whitespace too: a `.env` loader strips it but an exported variable keeps it, and a
    validator that exempted only an exact `""` would read `"   "` as unset and pick the default.
    """
    loaded = _with(name, blank, source, monkeypatch, tmp_path)

    assert not getattr(loaded, name).strip()
    with pytest.raises(InvalidDsn):
        safe_dsn(getattr(loaded, name))


@pytest.mark.parametrize(
    ("name", "value", "expected"),
    [
        ("fantabot_league_id", "4103937", 4103937),
        ("fantabot_auto_act", "true", True),
        ("fantabot_data_dir", "/srv/fantabot", Path("/srv/fantabot")),
        ("fantabot_agent_model", "llama3:cloud", "llama3:cloud"),
        ("lega_password", "None", "None"),
        ("fantabot_lineup_model", "null", "null"),
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


def test_padding_around_a_real_text_value_is_kept(monkeypatch: pytest.MonkeyPatch) -> None:
    """Only an all-blank value is touched; a password may begin or end with a space."""
    monkeypatch.setenv("LEGA_PASSWORD", "  not blank  ")

    assert Settings(_env_file=None).lega_password == "  not blank  "
