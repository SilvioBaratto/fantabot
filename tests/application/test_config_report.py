"""T28: `safe_dsn` masks a password and changes nothing else.

The DSN line of `config-check` is the one an operator copies — into `alembic.ini`, into
a `psql` invocation, into a `FANTABOT_DATABASE_URL=` export. Two properties have to hold
at once and they pull against each other: the password must never appear, and everything
that is *not* the password must come back byte for byte. SQLAlchemy's own
`render_as_string(hide_password=True)` satisfies the first and fails the second in two
ways, both of which reached the screen.
"""

from __future__ import annotations

import pytest

from fantabot.application.config_report import (
    MASK,
    REPORTED_SECRETS,
    SECRET_FIELDS,
    InvalidDsn,
    safe_dsn,
)

#: The bundled server's own shape: a unix socket path in the query, an empty password.
SOCKET = "postgresql+psycopg2://postgres:@/fantabot?host=/Users/me/.fantabot/pgdata"


class TestTheSocketDsnSurvivesIntact:
    """The shape `config.bundled_database_url` derives, which is the common case."""

    def test_it_comes_back_unchanged(self) -> None:
        """There is no password, so there is nothing to mask and nothing to alter."""
        assert safe_dsn(SOCKET) == SOCKET

    def test_the_socket_path_is_not_percent_encoded(self) -> None:
        """`%2F` is what makes the copied line fail.

        alembic's config is a `ConfigParser`: it interpolates `%` and rejects a
        `%2F`-encoded socket path with `ValueError: invalid interpolation syntax`.
        `config.bundled_database_url` says so in as many words, and this is the other
        half of that promise.
        """
        assert "%2F" not in safe_dsn(SOCKET)
        assert "host=/Users/me/.fantabot/pgdata" in safe_dsn(SOCKET)

    def test_a_path_with_a_space_is_not_escaped_either(self) -> None:
        """This repository lives on `/Volumes/External SSD`. The space is not a defect."""
        dsn = "postgresql+psycopg2://postgres:@/fantabot?host=/Volumes/External SSD/pg"
        assert safe_dsn(dsn) == dsn


class TestThePasswordIsTheOnlyThingReplaced:
    def test_a_real_password_becomes_the_mask(self) -> None:
        out = safe_dsn("postgresql+psycopg2://bot:hunter2@db.example.test:6543/otherdb")

        assert out == f"postgresql+psycopg2://bot:{MASK}@db.example.test:6543/otherdb"
        assert "hunter2" not in out

    def test_the_mask_does_not_leak_the_length(self) -> None:
        """A mask whose width tracks the secret is a hint, not a mask."""
        short = safe_dsn("postgresql://u:a@h/d")
        long = safe_dsn("postgresql://u:aaaaaaaaaaaaaaaaaaaaaaaaaaaa@h/d")

        assert short == long

    def test_an_empty_password_keeps_its_colon_and_gains_no_mask(self) -> None:
        """`postgres:***@` where there is no password is a lie in the safe direction.

        The bundled server is trust-authenticated. `render_as_string(hide_password=True)`
        masks `""` exactly like a real secret, so the screen invented a credential and
        sent the reader after the wrong problem. Masking protects a secret; it must not
        manufacture one.
        """
        out = safe_dsn(SOCKET)

        assert f"postgres:{MASK}@" not in out
        assert "postgres:@" in out

    def test_no_password_at_all_gains_no_colon(self) -> None:
        """The third case, and the reason the check is `is not None` and not truthiness.

        `postgres@host` and `postgres:@host` are different facts about the configuration,
        and this is the screen that exists to tell them apart.
        """
        dsn = "postgresql+psycopg2://postgres@127.0.0.1:5432/fantabot"
        assert safe_dsn(dsn) == dsn

    def test_a_password_that_also_spells_the_database_name_masks_only_the_password(
        self,
    ) -> None:
        """Why this reassembles from components rather than doing a string replace.

        `dsn.replace(password, MASK)` is the obvious spelling and it blanks every other
        occurrence too — here the database name, leaving a line that names no database.
        """
        out = safe_dsn("postgresql+psycopg2://u:fantabot@localhost:5432/fantabot")

        assert out == f"postgresql+psycopg2://u:{MASK}@localhost:5432/fantabot"
        assert out.endswith("/fantabot")


class TestTheShapesThatAreNotOurs:
    """`fantabot_database_url` is operator-set, so it is not always the bundled shape."""

    def test_a_driverless_sqlite_url_round_trips(self) -> None:
        assert safe_dsn("sqlite:///:memory:") == "sqlite:///:memory:"

    def test_a_repeated_query_key_keeps_both_values(self) -> None:
        """SQLAlchemy folds a repeated key into a tuple; dropping one would silently
        change what the DSN connects to."""
        out = safe_dsn("postgresql://h/d?opt=a&opt=b")

        assert out.count("opt=") == 2
        assert "opt=a" in out and "opt=b" in out

    def test_an_unparseable_dsn_is_refused_by_name(self) -> None:
        """Reported here, where the remedy is editing `.env`, rather than at the first
        connect — where it reads as a database being down."""
        with pytest.raises(InvalidDsn):
            safe_dsn("::not a dsn::")


class TestTheSecretSetsCannotDisagree:
    def test_the_reported_secrets_are_derived_from_the_excluded_ones(self) -> None:
        """Two hand-written lists is how a secret comes to be excluded from the dump but
        missing from the "set:" lines, or worse, the other way round."""
        assert set(REPORTED_SECRETS) == SECRET_FIELDS - {"fantabot_database_url"}

    def test_the_dsn_is_excluded_from_the_dump_but_not_from_the_screen(self) -> None:
        """It embeds a credential, so it cannot be dumped; it is the answer the operator
        came for, so it cannot be withheld. It gets its own masked line instead."""
        assert "fantabot_database_url" in SECRET_FIELDS
        assert "fantabot_database_url" not in REPORTED_SECRETS

    def test_every_named_secret_is_a_real_settings_field(self) -> None:
        """A typo in this set masks nothing and raises nothing — `model_dump(exclude=...)`
        ignores a name it does not know, so the field it was meant to hide is printed."""
        from fantabot.config import Settings

        assert set(Settings.model_fields) >= SECRET_FIELDS
