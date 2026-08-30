"""`TokenCipher` — and, mostly, whether its failures are diagnosable.

Round-tripping is the easy half. The half worth testing is that a wrong key and
a corrupt row produce two *different* sentences, neither of which is
`InvalidToken` — because that string is what `cryptography` says for both, and
it sends the operator looking for data corruption that is not there.
"""

from __future__ import annotations

import _tokens
import pytest
from cryptography.fernet import Fernet

from fantabot.tokens.crypto import FINGERPRINT_LENGTH, TokenCipher
from fantabot.tokens.errors import KeyMalformed, KeyMissing, TokenUndecryptable

PLAINTEXT = _tokens.make_token(l_id=_tokens.LEGA_MANTRA, t_id=_tokens.TEAM_MANTRA)


def a_key() -> str:
    return Fernet.generate_key().decode()


# --- the happy path -------------------------------------------------------


def test_a_token_round_trips() -> None:
    cipher = TokenCipher(a_key())
    ciphertext = cipher.encrypt(PLAINTEXT)

    assert cipher.decrypt(ciphertext, stored_fingerprint=cipher.fingerprint) == PLAINTEXT


def test_the_ciphertext_is_not_the_plaintext() -> None:
    cipher = TokenCipher(a_key())

    assert cipher.encrypt(PLAINTEXT) != PLAINTEXT.encode()
    assert PLAINTEXT.encode() not in cipher.encrypt(PLAINTEXT)


def test_two_encryptions_of_one_plaintext_differ() -> None:
    """Fernet embeds a timestamp and a fresh IV, so ciphertext is not a fingerprint."""
    cipher = TokenCipher(a_key())

    assert cipher.encrypt(PLAINTEXT) != cipher.encrypt(PLAINTEXT)


# --- the key --------------------------------------------------------------


def test_an_empty_key_is_missing_not_malformed() -> None:
    """The guard ordering, asserted rather than assumed.

    `Fernet("")` raises ValueError exactly as `Fernet("not-a-key")` does, so
    without the `if not key` guard *before* the constructor, an unset key tells
    the operator to fix the shape of a key they never set.
    """
    with pytest.raises(KeyMissing):
        TokenCipher("")


@pytest.mark.parametrize(
    "bad", ["short", "x" * 44, "!" * 44, "not-a-key", "a" * 43], ids=repr
)
def test_a_malformed_key_names_the_shape_and_the_recipe(bad: str) -> None:
    with pytest.raises(KeyMalformed) as caught:
        TokenCipher(bad)

    message = str(caught.value)
    assert "44-character urlsafe-base64" in message
    assert "Fernet.generate_key()" in message


def test_the_fingerprint_is_eight_lowercase_hex_characters() -> None:
    fingerprint = TokenCipher(a_key()).fingerprint

    assert len(fingerprint) == FINGERPRINT_LENGTH
    assert all(c in "0123456789abcdef" for c in fingerprint)


def test_the_fingerprint_is_a_function_of_the_key_alone() -> None:
    key = a_key()
    cipher, same_key = TokenCipher(key), TokenCipher(key)

    cipher.encrypt("one")
    same_key.encrypt("something entirely different")

    assert cipher.fingerprint == same_key.fingerprint
    assert cipher.fingerprint != TokenCipher(a_key()).fingerprint


def test_the_fingerprint_is_not_a_prefix_of_the_key() -> None:
    """It is a hash of the key, so it leaks no material from it."""
    key = a_key()

    assert not key.startswith(TokenCipher(key).fingerprint)


def test_the_repr_does_not_leak_the_key() -> None:
    key = a_key()
    rendered = repr(TokenCipher(key))

    leaked = [key[i : i + 8] for i in range(len(key) - 7) if key[i : i + 8] in rendered]
    assert leaked == [], f"repr() exposes {leaked}"
    assert "fingerprint=" in rendered


# --- the two failures that must not read alike ----------------------------


def test_the_wrong_key_names_both_fingerprints() -> None:
    """SC 15. Which key encrypted this row, and which one is in .env."""
    original, current = TokenCipher(a_key()), TokenCipher(a_key())
    ciphertext = original.encrypt(PLAINTEXT)

    with pytest.raises(TokenUndecryptable) as caught:
        current.decrypt(ciphertext, stored_fingerprint=original.fingerprint)

    message = str(caught.value)
    assert original.fingerprint in message
    assert current.fingerprint in message
    assert "fantabot auth login" in message


def test_a_corrupt_row_under_the_right_key_says_something_different() -> None:
    cipher = TokenCipher(a_key())
    tampered = bytearray(cipher.encrypt(PLAINTEXT))
    tampered[-1] ^= 0xFF

    with pytest.raises(TokenUndecryptable) as caught:
        cipher.decrypt(bytes(tampered), stored_fingerprint=cipher.fingerprint)

    assert "corrupt" in str(caught.value)


def test_the_two_failures_are_not_the_same_sentence() -> None:
    original, current = TokenCipher(a_key()), TokenCipher(a_key())
    ciphertext = original.encrypt(PLAINTEXT)

    with pytest.raises(TokenUndecryptable) as mismatch:
        current.decrypt(ciphertext, stored_fingerprint=original.fingerprint)

    tampered = bytearray(current.encrypt(PLAINTEXT))
    tampered[-1] ^= 0xFF
    with pytest.raises(TokenUndecryptable) as corrupt:
        current.decrypt(bytes(tampered), stored_fingerprint=current.fingerprint)

    assert str(mismatch.value) != str(corrupt.value)


@pytest.mark.parametrize("case", ["mismatch", "corrupt"])
def test_neither_failure_says_invalidtoken(case: str) -> None:
    """`cryptography`'s own word for both, and it describes neither."""
    original, current = TokenCipher(a_key()), TokenCipher(a_key())

    if case == "mismatch":
        payload, fingerprint = original.encrypt(PLAINTEXT), original.fingerprint
    else:
        tampered = bytearray(current.encrypt(PLAINTEXT))
        tampered[-1] ^= 0xFF
        payload, fingerprint = bytes(tampered), current.fingerprint

    with pytest.raises(TokenUndecryptable) as caught:
        current.decrypt(payload, stored_fingerprint=fingerprint)

    assert "InvalidToken" not in str(caught.value)


def test_stored_fingerprint_cannot_be_passed_positionally() -> None:
    """Keyword-only, so no caller can drop the check by shifting an argument."""
    cipher = TokenCipher(a_key())
    ciphertext = cipher.encrypt(PLAINTEXT)

    with pytest.raises(TypeError):
        cipher.decrypt(ciphertext, cipher.fingerprint)  # type: ignore[misc]


def test_no_error_message_contains_the_plaintext_token() -> None:
    original, current = TokenCipher(a_key()), TokenCipher(a_key())

    with pytest.raises(TokenUndecryptable) as caught:
        current.decrypt(original.encrypt(PLAINTEXT), stored_fingerprint=original.fingerprint)

    message = str(caught.value)
    leaked = [
        PLAINTEXT[i : i + 8]
        for i in range(len(PLAINTEXT) - 7)
        if PLAINTEXT[i : i + 8] in message
    ]
    assert leaked == []
