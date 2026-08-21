import os

import pytest

from fantabot.agentkit.env import (
    DANGEROUS_VARS,
    AuthLeakError,
    assert_subscription_auth,
    strip_dangerous_env,
)


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
