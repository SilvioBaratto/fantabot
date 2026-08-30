import os

import pytest

from fantabot.adapters.agent.env import (
    DANGEROUS_VARS,
    AuthLeakError,
    assert_auth,
    assert_byo_backend,
    assert_subscription_auth,
    strip_dangerous_env,
)
from fantabot.adapters.agent.options import agent_env

OLLAMA = "http://localhost:11434"


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in DANGEROUS_VARS:
        monkeypatch.delenv(name, raising=False)


def test_dangerous_vars_covers_every_backend_credential() -> None:
    # Each of these routes the SDK somewhere other than the OAuth subscription.
    # A var missing here is a var that silently bills the wrong account.
    assert set(DANGEROUS_VARS) >= {
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_AUTH_TOKEN",
        "ANTHROPIC_BEDROCK_API_KEY",
        "ANTHROPIC_VERTEX_PROJECT_ID",
        "CLAUDE_CODE_USE_BEDROCK",
        "CLAUDE_CODE_USE_VERTEX",
    }


def test_strip_dangerous_env_clears_every_dangerous_var(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in DANGEROUS_VARS:
        monkeypatch.setenv(name, "leaked")

    strip_dangerous_env()

    assert [name for name in DANGEROUS_VARS if name in os.environ] == []


def test_strip_dangerous_env_leaves_unrelated_vars_alone(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LEGA_EMAIL", "someone@example.com")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "leaked")

    strip_dangerous_env()

    assert os.environ["LEGA_EMAIL"] == "someone@example.com"


def test_strip_dangerous_env_is_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    strip_dangerous_env()
    strip_dangerous_env()  # a second call on an already-clean env must not raise

    assert "ANTHROPIC_API_KEY" not in os.environ


def test_assert_subscription_auth_passes_on_a_clean_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in DANGEROUS_VARS:
        monkeypatch.delenv(name, raising=False)

    assert_subscription_auth({})  # must not raise


def test_assert_subscription_auth_rejects_a_credential_in_os_environ(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in DANGEROUS_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-leaked")

    with pytest.raises(AuthLeakError) as excinfo:
        assert_subscription_auth({})

    assert "ANTHROPIC_API_KEY" in str(excinfo.value)
    assert "os.environ" in str(excinfo.value)


def test_assert_subscription_auth_rejects_a_credential_in_options_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The second leak vector: claude_agent_sdk/_internal/session_resume.py:356 reads
    # opt_env.get("ANTHROPIC_API_KEY") or os.environ.get(...), so a clean os.environ
    # proves nothing on its own.
    for name in DANGEROUS_VARS:
        monkeypatch.delenv(name, raising=False)

    with pytest.raises(AuthLeakError) as excinfo:
        assert_subscription_auth({"ANTHROPIC_API_KEY": "sk-leaked"})

    assert "ANTHROPIC_API_KEY" in str(excinfo.value)
    assert "options.env" in str(excinfo.value)


def test_assert_subscription_auth_rejects_a_backend_toggle_not_just_a_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # CLAUDE_CODE_USE_BEDROCK carries no secret but reroutes the whole run to a
    # per-token backend. Exported in the operator's shell, it would bill silently.
    for name in DANGEROUS_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("CLAUDE_CODE_USE_BEDROCK", "1")

    with pytest.raises(AuthLeakError):
        assert_subscription_auth({})


def test_assert_subscription_auth_ignores_an_empty_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # An empty string is how a caller explicitly neutralizes a var; it is not a
    # credential and must not be treated as a leak.
    for name in DANGEROUS_VARS:
        monkeypatch.delenv(name, raising=False)

    assert_subscription_auth({"ANTHROPIC_API_KEY": ""})  # must not raise


# --- the bring-your-own-backend path ----------------------------------------
#
# The subscription tests above prove "no credential anywhere". These prove the
# mirror image: when an operator has deliberately pointed fantabot at an
# Anthropic-compatible shim, the shim is actually reached rather than silently
# ignored. Both proofs matter — a half-configured shim looks exactly like a
# working one until the subscription's rate limit says otherwise.


def test_assert_byo_backend_accepts_a_fully_configured_shim(clean_env: None) -> None:
    assert_byo_backend(
        {"ANTHROPIC_BASE_URL": OLLAMA, "ANTHROPIC_AUTH_TOKEN": "ollama", "ANTHROPIC_API_KEY": ""}
    )  # must not raise


def test_assert_byo_backend_rejects_a_missing_base_url(clean_env: None) -> None:
    with pytest.raises(AuthLeakError, match="ANTHROPIC_BASE_URL"):
        assert_byo_backend({"ANTHROPIC_AUTH_TOKEN": "ollama"})


def test_assert_byo_backend_rejects_a_missing_token(clean_env: None) -> None:
    # The CLI would fall back to the OAuth subscription and ignore the base URL,
    # which is the failure this whole function exists to make loud.
    with pytest.raises(AuthLeakError, match="ANTHROPIC_AUTH_TOKEN"):
        assert_byo_backend({"ANTHROPIC_BASE_URL": OLLAMA})


def test_assert_byo_backend_rejects_an_ambient_credential(monkeypatch: pytest.MonkeyPatch) -> None:
    # os.environ still wins races against options.env inside the SDK, so a shim
    # run is no excuse to skip strip_dangerous_env().
    for name in DANGEROUS_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "leaked")

    with pytest.raises(AuthLeakError, match=r"os\.environ"):
        assert_byo_backend({"ANTHROPIC_BASE_URL": OLLAMA, "ANTHROPIC_AUTH_TOKEN": "ollama"})


def test_assert_auth_defaults_to_the_subscription_proof(
    clean_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    from fantabot import config

    monkeypatch.setattr(config.settings, "fantabot_agent_base_url", "")

    assert_auth({})  # the subscription path: empty env is the proof
    with pytest.raises(AuthLeakError):
        assert_auth({"ANTHROPIC_AUTH_TOKEN": "ollama"})


def test_assert_auth_switches_to_the_backend_proof_when_a_base_url_is_set(
    clean_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    from fantabot import config

    monkeypatch.setattr(config.settings, "fantabot_agent_base_url", OLLAMA)

    # The exact env that would have raised on the subscription path now passes...
    assert_auth({"ANTHROPIC_BASE_URL": OLLAMA, "ANTHROPIC_AUTH_TOKEN": "ollama"})
    # ...and the empty env that passes there now fails here.
    with pytest.raises(AuthLeakError):
        assert_auth({})


def test_agent_env_is_empty_unless_a_backend_is_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The default must stay the subscription. This is the test that fails if
    # someone gives fantabot_agent_base_url a non-empty default.
    from fantabot import config

    monkeypatch.setattr(config.settings, "fantabot_agent_base_url", "")

    assert agent_env() == {}


def test_agent_env_neutralizes_the_api_key_rather_than_omitting_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Ollama's own docs set ANTHROPIC_API_KEY to "" rather than leaving it out,
    # and assert_subscription_auth treats "" as neutralized, not as a leak.
    from fantabot import config

    monkeypatch.setattr(config.settings, "fantabot_agent_base_url", OLLAMA)
    monkeypatch.setattr(config.settings, "fantabot_agent_auth_token", "ollama")

    assert agent_env() == {
        "ANTHROPIC_BASE_URL": OLLAMA,
        "ANTHROPIC_AUTH_TOKEN": "ollama",
        "ANTHROPIC_API_KEY": "",
    }
