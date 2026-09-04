"""The app's encryption key — generated once, stored in ``~/.fantabot``, loaded silently.

fantabot encrypts each lega's bearer token at rest with ``FANTABOT_ENCRYPTION_KEY`` (a
Fernet key); without it ``connect account`` cannot store a token. The developer CLI asks
you to generate one by hand, but the app must be zero-config: ``setup`` mints one if none
exists and every launch loads it into the environment, so the UI never asks the user to
configure anything.

The key is a local secret in the user's home, written ``0600`` where the OS allows it —
the same local-appliance threat model fantabot already documents (the key sits beside the
database password; it does not defend against someone who can read the user's home). It is
never logged, echoed, returned in a response, or placed on argv. A key already provided by
the environment (or a ``.env``) always wins and is never overwritten.
"""

from __future__ import annotations

import os
import stat
from collections.abc import MutableMapping
from pathlib import Path

from fantabot_app import paths

ENV_ENCRYPTION_KEY = "FANTABOT_ENCRYPTION_KEY"


def key_path() -> Path:
    """The key file: ``~/.fantabot/encryption.key``."""
    return paths.home() / "encryption.key"


def load_or_create_key(
    *, create: bool, environ: MutableMapping[str, str] = os.environ
) -> str | None:
    """Ensure ``FANTABOT_ENCRYPTION_KEY`` is available, minting one on first setup.

    Resolution order: a key already in ``environ`` (user override) wins and is left
    untouched; else the stored key file is read; else, when ``create`` is set, a fresh
    Fernet key is generated and persisted. The resolved key is exported into ``environ``
    so fantabot's settings pick it up, and returned. Returns ``None`` only when no key
    exists and ``create`` is False.
    """
    existing = environ.get(ENV_ENCRYPTION_KEY)
    if existing:
        return existing

    path = key_path()
    if path.exists():
        stored = path.read_text(encoding="utf-8").strip()
        if stored:
            environ[ENV_ENCRYPTION_KEY] = stored
            return stored

    if not create:
        return None

    from cryptography.fernet import Fernet

    key = Fernet.generate_key().decode()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(key, encoding="utf-8")
    try:  # best-effort 0600; on Windows chmod is largely a no-op, so don't fail on it
        path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass
    environ[ENV_ENCRYPTION_KEY] = key
    return key
