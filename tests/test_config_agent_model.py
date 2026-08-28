"""The model id and the backend must agree, and the agreement is checked.

Moving `FANTABOT_AGENT_BASE_URL` without moving `FANTABOT_AGENT_MODEL` is the
mistake this configuration invites, and it is invisible: the run starts, the
first query 404s, and 522 more do the same. These tests pin the check that
turns it into an exit code before anything is queried.
"""

from __future__ import annotations

import pytest

from fantabot.config import Settings

OLLAMA = "http://localhost:11434"
DEEPSEEK = "deepseek-v4-flash:cloud"


@pytest.fixture(autouse=True)
def _no_ambient_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    """Isolate both channels pydantic-settings reads, not just the obvious one.

    `_env_file=None` below stops it reading the developer's `.env`; it does
    nothing about `os.environ`, so an exported FANTABOT_AGENT_MODEL still
    decides whether these pass. Verified by running the suite with the vars set
    three different ways — it failed here before this fixture existed. Same
    failure class as 1cf6c71: state outside the repo deciding a check.
    """
    for name in ("FANTABOT_AGENT_BASE_URL", "FANTABOT_AGENT_AUTH_TOKEN", "FANTABOT_AGENT_MODEL"):
        monkeypatch.delenv(name, raising=False)


def _settings(**overrides: str) -> Settings:
    """A Settings built from arguments only — no `.env`, no ambient environment."""
    return Settings(_env_file=None, **overrides)


def test_the_override_wins_over_the_setting() -> None:
    settings = _settings(fantabot_agent_base_url=OLLAMA, fantabot_agent_model="qwen3.5:cloud")
    assert settings.resolve_agent_model(DEEPSEEK) == DEEPSEEK


def test_an_empty_override_falls_back_to_the_setting() -> None:
    settings = _settings(fantabot_agent_base_url=OLLAMA, fantabot_agent_model=DEEPSEEK)
    assert settings.resolve_agent_model("") == DEEPSEEK


def test_a_claude_model_on_a_shim_is_refused() -> None:
    settings = _settings(fantabot_agent_base_url=OLLAMA, fantabot_agent_model="claude-sonnet-5")
    with pytest.raises(RuntimeError, match="Anthropic id"):
        settings.resolve_agent_model()


def test_a_shim_model_without_a_base_url_is_refused() -> None:
    # The reverse mistake, and the more expensive one: without the guard this
    # reaches the subscription, which has no such model.
    settings = _settings(fantabot_agent_base_url="", fantabot_agent_model=DEEPSEEK)
    with pytest.raises(RuntimeError, match="FANTABOT_AGENT_BASE_URL"):
        settings.resolve_agent_model()


def test_the_override_is_checked_too_not_just_the_setting() -> None:
    # --model is the likeliest way to get this wrong interactively.
    settings = _settings(fantabot_agent_base_url=OLLAMA, fantabot_agent_model=DEEPSEEK)
    with pytest.raises(RuntimeError):
        settings.resolve_agent_model("claude-sonnet-5")


def test_the_subscription_default_is_coherent_on_its_own() -> None:
    # Defaults must not need a .env to be valid: no base URL, claude-* model.
    settings = _settings()
    assert settings.resolve_agent_model() == "claude-sonnet-4-6-eaq-gf08h1"
