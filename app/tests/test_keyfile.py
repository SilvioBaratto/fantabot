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
    # No project `.env` unless a test says so. Without this, a run from the repository finds
    # the operator's real `.env` by walking up, and every case below reads their key.
    monkeypatch.setattr(keyfile, "dotenv_key", lambda: None)
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


# -- the `.env` key and the key file ---------------------------------------------------
#
# The defect, measured 2026-09-21: `up` exported the key file before the API loaded `.env`
# with `override=False`, so the `.env` key never won. The CLI and the hourly lineup job ran
# on one key (`ef341176`), the app on another (`aa695c77`); the lega tokens read KEY
# MISMATCH on Accounts while the FantaLab session the app had saved was unreadable by the CLI.


def _write_key_file(value: str) -> None:
    keyfile.key_path().parent.mkdir(parents=True, exist_ok=True)
    keyfile.key_path().write_text(value, encoding="utf-8")


def test_the_dotenv_key_beats_the_key_file() -> None:
    _write_key_file("key-file-key")
    env: dict[str, str] = {}

    key = keyfile.load_or_create_key(create=True, environ=env, dotenv=lambda: "dotenv-key")

    assert key == "dotenv-key"
    assert env["FANTABOT_ENCRYPTION_KEY"] == "dotenv-key"
    # Read, never rewritten: the file may be the only thing that can open a saved session.
    assert keyfile.key_path().read_text(encoding="utf-8") == "key-file-key"


def test_an_exported_key_beats_the_dotenv_key() -> None:
    env = {"FANTABOT_ENCRYPTION_KEY": "exported"}

    assert keyfile.load_or_create_key(create=True, environ=env, dotenv=lambda: "dotenv") == (
        "exported"
    )


def test_no_key_file_is_minted_while_the_dotenv_supplies_one() -> None:
    keyfile.load_or_create_key(create=True, environ={}, dotenv=lambda: "dotenv-key")

    assert not keyfile.key_path().exists()  # a minted file would be the second key again


def test_the_default_dotenv_lookup_is_read_at_call_time(monkeypatch) -> None:
    """`doctor` and the launcher call with no seam; the module's lookup must be the one used."""
    monkeypatch.setattr(keyfile, "dotenv_key", lambda: "from-module")

    assert keyfile.load_or_create_key(create=False, environ={}) == "from-module"


def test_no_split_without_a_key_file() -> None:
    assert keyfile.key_file_split(environ={}, dotenv=lambda: "dotenv-key") is None


def test_no_split_when_the_key_file_is_the_key_in_use() -> None:
    _write_key_file("same-key")

    assert keyfile.key_file_split(environ={}, dotenv=lambda: "same-key") is None
    assert keyfile.key_file_split(environ={}, dotenv=lambda: None) is None  # file is in use


def test_a_split_names_both_fingerprints_and_neither_key() -> None:
    from fantabot.domain.tokens.crypto import fingerprint_of

    _write_key_file("key-file-key")

    split = keyfile.key_file_split(environ={}, dotenv=lambda: "dotenv-key")

    assert split == keyfile.KeySplit(
        in_use=fingerprint_of("dotenv-key"), key_file=fingerprint_of("key-file-key")
    )
    assert "key" not in (split.in_use + split.key_file)  # hex fingerprints, not the keys


def test_the_exported_key_is_the_one_a_split_compares() -> None:
    from fantabot.domain.tokens.crypto import fingerprint_of

    _write_key_file("key-file-key")

    split = keyfile.key_file_split(
        environ={"FANTABOT_ENCRYPTION_KEY": "exported"}, dotenv=lambda: "key-file-key"
    )

    assert split is not None and split.in_use == fingerprint_of("exported")
