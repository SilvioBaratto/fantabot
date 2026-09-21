"""The app's encryption key — generated once, stored in ``~/.fantabot``, loaded silently.

fantabot encrypts each lega's bearer token at rest with ``FANTABOT_ENCRYPTION_KEY`` (a
Fernet key); without it ``connect account`` cannot store a token. The developer CLI asks
you to generate one by hand, but the app must be zero-config: ``setup`` mints one if none
exists and every launch loads it into the environment, so the UI never asks the user to
configure anything.

The key is a local secret in the user's home, written ``0600`` where the OS allows it —
the same local-appliance threat model fantabot already documents (the key sits beside the
database password; it does not defend against someone who can read the user's home). It is
never logged, echoed, returned in a response, or placed on argv — only its fingerprint is.
A key already provided by the environment or the project ``.env`` always wins and is never
overwritten.

**That last sentence was a promise the code did not keep until 2026-09-21.** Only
``os.environ`` was consulted, and ``python-dotenv`` never puts a ``.env`` there on its own:
the API's ``load_configuration`` does, with ``override=False``, but ``up`` calls this
*first*. So the key file was exported, the ``.env`` key lost to it, and an operator with
both ran the CLI (and the scheduled lineup) on one key and the app on another — the lega
tokens read ``KEY MISMATCH`` on the Accounts page while the terminal said they were fine.
"""

from __future__ import annotations

import os
import stat
from collections.abc import Callable, Mapping, MutableMapping
from dataclasses import dataclass
from pathlib import Path

from fantabot_app import paths

ENV_ENCRYPTION_KEY = "FANTABOT_ENCRYPTION_KEY"


def key_path() -> Path:
    """The key file: ``~/.fantabot/encryption.key``."""
    return paths.home() / "encryption.key"


def dotenv_key() -> str | None:
    """The key the project ``.env`` declares, or ``None``.

    Found by the same ``find_dotenv`` walk ``load_configuration`` uses — up from the working
    directory, then up from the installed package — so this is the file the API process
    would inject anyway, and the launcher and the server cannot disagree about which one.
    """
    from dotenv import dotenv_values

    from fantabot_app.api.infrastructure.config import find_dotenv

    found = find_dotenv()
    if found is None:
        return None
    value = dotenv_values(found).get(ENV_ENCRYPTION_KEY)
    return value.strip() if value and value.strip() else None


def _stored_key() -> str | None:
    path = key_path()
    if not path.exists():
        return None
    stored = path.read_text(encoding="utf-8").strip()
    return stored or None


def load_or_create_key(
    *,
    create: bool,
    environ: MutableMapping[str, str] = os.environ,
    dotenv: Callable[[], str | None] | None = None,
) -> str | None:
    """Ensure ``FANTABOT_ENCRYPTION_KEY`` is available, minting one on first setup.

    Resolution order, the first that answers wins and nothing later is touched:

    1. a key already in ``environ`` — the operator exported it;
    2. the project ``.env`` — the key the CLI and the scheduled lineup job read;
    3. the stored key file;
    4. when ``create`` is set, a fresh Fernet key, persisted to the key file.

    The resolved key is exported into ``environ`` so fantabot's settings pick it up, and
    returned. Returns ``None`` only when no key exists and ``create`` is False. A key file
    is never minted while (1) or (2) supplies one: that is the second key this replaced.
    """
    existing = environ.get(ENV_ENCRYPTION_KEY)
    if existing:
        return existing

    declared = (dotenv or dotenv_key)()
    if declared:
        environ[ENV_ENCRYPTION_KEY] = declared
        return declared

    stored = _stored_key()
    if stored:
        environ[ENV_ENCRYPTION_KEY] = stored
        return stored

    if not create:
        return None

    from cryptography.fernet import Fernet

    key = Fernet.generate_key().decode()
    path = key_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(key, encoding="utf-8")
    try:  # best-effort 0600; on Windows chmod is largely a no-op, so don't fail on it
        path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass
    environ[ENV_ENCRYPTION_KEY] = key
    return key


@dataclass(frozen=True)
class KeySplit:
    """Two keys where there should be one — fingerprints only, never a key."""

    in_use: str
    key_file: str


def key_file_split(
    *,
    environ: Mapping[str, str] = os.environ,
    dotenv: Callable[[], str | None] | None = None,
) -> KeySplit | None:
    """The key file holds a different key from the one in use, or ``None``.

    ``None`` when they agree, when there is no key file, or when the key file *is* the key
    in use. A split is survivable — whatever the key file's key encrypted is simply
    unreadable — but it is invisible: every page renders, and only the rows it wrote say
    KEY MISMATCH. So ``up``, ``setup`` and ``doctor`` all say it out loud.
    """
    from fantabot.domain.tokens.crypto import fingerprint_of

    stored = _stored_key()
    if stored is None:
        return None
    in_use = environ.get(ENV_ENCRYPTION_KEY) or (dotenv or dotenv_key)()
    if not in_use or in_use == stored:
        return None
    return KeySplit(in_use=fingerprint_of(in_use), key_file=fingerprint_of(stored))
