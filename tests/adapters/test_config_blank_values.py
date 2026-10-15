"""A blank line in `.env` means "unset", for every setting that is not a string.

`settings = Settings()` runs at import, so a value pydantic refuses stops every command at
the import line — `config-check` included, the one screen meant to say what is wrong.
`.env.example` ships `FANTABOT_LEAGUE_ID=` blank, and copying it unchanged did exactly that:
`""` is not an `int`. A blank `FANTABOT_AUTO_ACT` was the same crash, and a blank path did
not crash at all, which is worse: `Path("")` is the working directory.

A blank reads as the default whether it comes from `.env` or the environment, so an exported
`FANTABOT_LEAGUE_ID=` still overrides the file, as an exported value always has. The string
settings are untouched and keep a blank as `""`. `env_ignore_empty` was the other way to read
a blank as unset, and it would have made an exported blank fall through to the file instead.
"""

from __future__ import annotations

from pathlib import Path
from types import UnionType
from typing import get_args

import pytest
from pydantic import ValidationError

from fantabot.config import Settings

REPO = Path(__file__).resolve().parents[2]


def _is_text(annotation: object) -> bool:
    if annotation is str:
        return True
    return isinstance(annotation, UnionType) and set(get_args(annotation)) <= {str, type(None)}


#: Every setting whose type is not text — the ones a blank can break.
NOT_TEXT = sorted(
    name for name, field in Settings.model_fields.items() if not _is_text(field.annotation)
)


@pytest.fixture(autouse=True)
def _no_ambient_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """This machine exports `FANTABOT_HARVEST_DIR`; any exported setting would decide these."""
    for name in Settings.model_fields:
        monkeypatch.delenv(name.upper(), raising=False)


def test_the_shipped_template_loads_unchanged(tmp_path: Path) -> None:
    """The regression itself: a fresh `.env` copied from the template must not crash."""
    env = tmp_path / ".env"
    env.write_text((REPO / ".env.example").read_text(encoding="utf-8"), encoding="utf-8")

    loaded = Settings(_env_file=env)

    assert loaded.fantabot_league_id == 0
    assert loaded.fantabot_auto_act is False


def test_the_non_text_settings_are_the_ones_this_file_expects() -> None:
    """A new `int`, `bool` or `Path` setting must be added to the validator, not discovered
    the day someone leaves it blank."""
    assert NOT_TEXT == [
        "fantabot_auto_act",
        "fantabot_data_dir",
        "fantabot_harvest_dir",
        "fantabot_league_id",
        "fantabot_storage_state",
    ]


@pytest.mark.parametrize("source", ["dotenv", "environ"])
@pytest.mark.parametrize("blank", ["", "   "])
@pytest.mark.parametrize("name", NOT_TEXT)
def test_a_blank_non_text_setting_is_its_default(
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
