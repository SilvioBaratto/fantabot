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
