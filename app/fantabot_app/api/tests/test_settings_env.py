"""Settings must not bind to generic ambient shell variables."""

from __future__ import annotations

import pytest

from fantabot_app.api.infrastructure.settings import Settings


@pytest.mark.parametrize(
    ("bare", "value"),
    [("DEBUG", "app:*"), ("VERSION", "9.9.9"), ("LOG_LEVEL", "TRACE"), ("ENVIRONMENT", "ci")],
)
def test_ambient_generic_variables_are_ignored(
    monkeypatch: pytest.MonkeyPatch, bare: str, value: str
) -> None:
    monkeypatch.setenv(bare, value)
    s = Settings()
    assert (s.debug, s.version, s.log_level, s.environment) == (
        True,
        "0.1.0",
        "INFO",
        "development",
    )


def test_prefixed_variables_are_honoured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FANTABOT_APP_DEBUG", "false")
    monkeypatch.setenv("FANTABOT_APP_LOG_LEVEL", "warning")
    monkeypatch.setenv("FANTABOT_APP_ENVIRONMENT", "production")
    s = Settings()
    assert (s.debug, s.log_level, s.environment) == (False, "warning", "production")


def _env_name(name: str) -> str:
    alias = Settings.model_fields[name].validation_alias
    return name.upper() if alias is None else str(alias.choices[0])


#: Every setting but the one whose blank already means something of its own: an empty
#: `CORS_ORIGINS` is the three-origin local list in `cors_origins_list`, not the default.
BLANK_IS_DEFAULT = sorted(name for name in Settings.model_fields if name != "cors_origins")


@pytest.fixture
def no_app_env(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    for name in Settings.model_fields:
        monkeypatch.delenv(_env_name(name), raising=False)
    return monkeypatch


@pytest.mark.parametrize("blank", ["", "   "])
@pytest.mark.parametrize("name", BLANK_IS_DEFAULT)
def test_a_blank_setting_is_its_default(
    no_app_env: pytest.MonkeyPatch, name: str, blank: str
) -> None:
    """`settings = Settings()` runs at import and `load_configuration` copies `.env` into the
    environment, so a blank `FANTABOT_APP_DEBUG=` used to stop the app before it started,
    and a blank log level or API prefix broke it a moment later."""
    default = getattr(Settings(), name)
    no_app_env.setenv(_env_name(name), blank)

    assert getattr(Settings(), name) == default


def test_a_blank_cors_list_keeps_its_own_meaning(no_app_env: pytest.MonkeyPatch) -> None:
    no_app_env.setenv("CORS_ORIGINS", "")

    assert "http://127.0.0.1:4200" in Settings().cors_origins_list


def test_a_wrong_debug_value_still_fails(no_app_env: pytest.MonkeyPatch) -> None:
    from pydantic import ValidationError

    no_app_env.setenv("FANTABOT_APP_DEBUG", "maybe")

    with pytest.raises(ValidationError, match="FANTABOT_APP_DEBUG"):
        Settings()
