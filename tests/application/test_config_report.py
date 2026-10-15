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


class TestEveryDsnTheReportCarriesIsMasked:
    """A password has two places to hide in a DSN, and a second DSN field to hide in."""

    def test_a_password_in_the_query_string_is_masked(self) -> None:
        dsn = "postgresql+psycopg2://postgres@db/fantabot?password=hunter2&sslmode=require"
        rendered = safe_dsn(dsn)
        assert "hunter2" not in rendered
        assert "sslmode=require" in rendered

    def test_the_test_database_dsn_reaches_the_report_masked(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from fantabot.application.config_report import build_report
        from fantabot.config import settings

        monkeypatch.setattr(
            settings, "fantabot_test_database_url", "postgresql://u:hunter2@db/fantabot_test"
        )
        shown = build_report().settings["fantabot_test_database_url"]
        assert "hunter2" not in str(shown)
        assert str(shown) == "postgresql://u:***@db/fantabot_test"


class TestABlankDsnIsNamedNotRendered:
    """`safe_dsn` and `database_target` are the one place a DSN that cannot be used is described.

    Five surfaces tell an operator the database cannot be used — `db dump`, `db check`,
    `auth status` and the two login preflights — and four of them sit in the handler for a
    failure, where calling `safe_dsn` on a blank DSN raises `InvalidDsn` again: a traceback
    over the one screen meant to say what is wrong.
    """

    @pytest.mark.parametrize("blank", ["", "   ", "\t"])
    def test_a_blank_dsn_says_blank_rather_than_parsing_nothing(self, blank: str) -> None:
        with pytest.raises(InvalidDsn, match="blank"):
            safe_dsn(blank)

    def test_a_usable_dsn_is_the_one_the_screen_has_always_printed(self) -> None:
        from fantabot.application.config_report import database_target

        target = database_target(SOCKET)

        assert target.usable
        assert target.headline == f"Cannot reach the database at {safe_dsn(SOCKET)}"
        assert target.remedy == "Start it with: fantabot-app db start"

    @pytest.mark.parametrize("blank", ["", "   ", "\t"])
    def test_a_blank_one_names_the_setting_and_does_not_send_anyone_to_a_server(
        self, blank: str
    ) -> None:
        from fantabot.application.config_report import database_target

        target = database_target(blank)

        assert not target.usable
        assert "blank" in target.headline
        assert "FANTABOT_DATABASE_URL" in target.remedy
        assert ".env" in target.remedy
        assert "environment" in target.remedy
        assert "Unset" in target.remedy, "a blank is not absent, which is why it fails"
        assert "db start" not in target.headline + target.remedy, (
            "the server is not what is wrong, and the line would send the operator to it"
        )

    def test_the_failure_line_is_the_drivers_only_when_the_dsn_is_usable(self) -> None:
        """For a URL the driver could not parse, its message can be the URL — older SQLAlchemy
        quotes the string it was given — so the type alone is printed."""
        from sqlalchemy.exc import ArgumentError, OperationalError

        from fantabot.application.config_report import database_target

        unusable = database_target("S3cr3tCanary not a url").detail(
            ArgumentError("Could not parse SQLAlchemy URL from string 'S3cr3tCanary not a url'")
        )
        usable = database_target(SOCKET).detail(
            OperationalError("SELECT 1", {}, Exception("connection refused"))
        )

        assert unusable == "ArgumentError"
        assert usable.startswith("OperationalError: ")
        assert "connection refused" in usable

    def test_an_unparseable_one_says_so_and_never_repeats_it(self) -> None:
        """What fails to parse may still hold a password, and it cannot be masked reliably."""
        from fantabot.application.config_report import database_target

        target = database_target("S3cr3tCanary is not a url")

        assert not target.usable
        assert "does not parse" in target.headline
        assert "S3cr3tCanary" not in target.headline + target.remedy


class TestTheTestDatabaseUrlIsJudgedLikeTheMainOne:
    """`config-check` judges both DSN settings: a blank test URL would otherwise print `''` and
    exit 0 over a value that fails the `db` tier on its first test."""

    @staticmethod
    def _report(monkeypatch: pytest.MonkeyPatch, **given: str):  # type: ignore[no-untyped-def]
        from fantabot import config
        from fantabot.application.config_report import build_report

        for name in ("FANTABOT_DATABASE_URL", "FANTABOT_TEST_DATABASE_URL"):
            monkeypatch.delenv(name, raising=False)
        fresh = config.Settings(_env_file=None, **given)
        monkeypatch.setattr(config, "settings", fresh)
        return build_report()

    @pytest.mark.parametrize("blank", ["", "   ", "\t"])
    def test_a_blank_one_is_reported(self, monkeypatch: pytest.MonkeyPatch, blank: str) -> None:
        report = self._report(monkeypatch, fantabot_test_database_url=blank)

        assert report.test_database_url_error is not None
        assert "blank" in report.test_database_url_error
        assert report.database_url_error is None, "the main URL is fine and stays so"
        assert report.settings["fantabot_test_database_url"] == blank, (
            "a blank holds nothing to withhold; `***` would read as a hidden value"
        )

    def test_an_unparseable_one_is_reported_and_withheld(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        report = self._report(monkeypatch, fantabot_test_database_url="S3cr3tCanary not a url")

        assert report.test_database_url_error is not None
        assert "S3cr3tCanary" not in report.test_database_url_error
        assert report.settings["fantabot_test_database_url"] == MASK

    def test_an_unset_one_is_not_a_problem(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Unset means the bundled `fantabot_test`, which is what the report has always
        shown. Only a *present* blank is new."""
        report = self._report(monkeypatch)

        assert report.test_database_url_error is None
        assert "fantabot_test" in str(report.settings["fantabot_test_database_url"])

    def test_a_usable_one_is_not_a_problem_and_stays_masked(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        report = self._report(
            monkeypatch, fantabot_test_database_url="postgresql://u:hunter2@db/fantabot_test"
        )

        assert report.test_database_url_error is None
        masked = report.settings["fantabot_test_database_url"]
        assert masked == "postgresql://u:***@db/fantabot_test"

    def test_a_blank_main_url_and_a_blank_test_url_are_reported_separately(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        report = self._report(
            monkeypatch, fantabot_database_url="", fantabot_test_database_url="  "
        )

        assert report.database_url_error is not None
        assert report.test_database_url_error is not None
