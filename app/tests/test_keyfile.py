"""The auto-provisioned encryption key: generate once, persist, load transparently.

No sockets, no real home: ``paths.home`` is redirected at a tmp dir and a fresh dict
stands in for the environment, so each case is isolated.
"""

from __future__ import annotations

import pytest
from cryptography.fernet import Fernet

from fantabot_app import keyfile, paths


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "home", lambda: tmp_path)
    return tmp_path


def test_creates_a_valid_fernet_key_and_persists_it(_home) -> None:
    env: dict[str, str] = {}
    key = keyfile.load_or_create_key(create=True, environ=env)

    assert key
    Fernet(key.encode())  # a real Fernet key — round-trips, does not raise
    assert keyfile.key_path().exists()
    assert keyfile.key_path().read_text(encoding="utf-8").strip() == key
    assert env["FANTABOT_ENCRYPTION_KEY"] == key  # exported so fantabot picks it up


def test_is_idempotent_returning_the_same_key(_home) -> None:
    first = keyfile.load_or_create_key(create=True, environ={})
    second = keyfile.load_or_create_key(create=True, environ={})  # fresh env, reads file
    assert first == second  # not regenerated on the second setup / launch


def test_a_key_already_in_the_environment_wins_and_is_not_overwritten(_home) -> None:
    env = {"FANTABOT_ENCRYPTION_KEY": "user-provided"}
    key = keyfile.load_or_create_key(create=True, environ=env)

    assert key == "user-provided"
    assert not keyfile.key_path().exists()  # never clobber a user's own key


def test_load_without_create_returns_none_when_absent(_home) -> None:
    env: dict[str, str] = {}
    assert keyfile.load_or_create_key(create=False, environ=env) is None
    assert "FANTABOT_ENCRYPTION_KEY" not in env
    assert not keyfile.key_path().exists()


def test_load_without_create_reads_an_existing_key(_home) -> None:
    created = keyfile.load_or_create_key(create=True, environ={})
    env: dict[str, str] = {}
    loaded = keyfile.load_or_create_key(create=False, environ=env)  # no create, but present
    assert loaded == created
    assert env["FANTABOT_ENCRYPTION_KEY"] == created
