"""The resolved settings, assembled once for both surfaces that show them.

`config-check` is the screen an operator reads when the CLI and the app disagree about
which database they are on, and that disagreement is not hypothetical here: `.env`
pointing the CLI at a compose Postgres while the app had provisioned its own is what let
a week of Classic auction collection read as lost (root `CLAUDE.md`, "One database, and
it is the app's"). A screen that answers *that* question is the last one that may exist
twice — two copies of "which fields are secret" drift, and the copy that drifts is the
one that prints a credential into a cron log.

So the two decisions live here and the Typer body and the endpoint are printers:

* **What is secret**, and what is merely routing. `fantabot_agent_base_url` is printed in
  full on purpose — it is the fastest explanation for a run that went somewhere other
  than the subscription — while `fantabot_agent_auth_token` beside it is masked even
  though on Ollama its documented value is the placeholder `"ollama"`. Nothing here can
  tell that placeholder from a real gateway bearer token, so neither prints.
* **How the DSN renders.** Three defects, all in the same line, all T28:

  1. **It was percent-encoded.** `config.bundled_database_url` is explicit that the
     derived DSN never is, because alembic's config is a `ConfigParser`: it interpolates
     `%` and rejects a `%2F`-encoded socket path with `ValueError: invalid interpolation
     syntax`. SQLAlchemy's `render_as_string` encodes the query string, so the one screen
     that tells an operator which database they are on printed a DSN that fails the
     moment they act on it.
  2. **An empty password was masked as `***`.** The bundled server is trust-authenticated
     and its DSN carries an empty password; `render_as_string(hide_password=True)` masks
     `""` exactly like a real secret, so the screen invented a credential that does not
     exist and sent the reader after the wrong problem. Masking protects a secret; it
     must not manufacture one.
  3. **Rich hard-wrapped it.** Not this module's to fix — see `interface/app.py` — but it
     is the third reason the printed line could not be copied, and the three are only
     worth anything together.

* **What a DSN that cannot be used is called.** `database_target` is the one answer for the
  screens that report a database they could not reach. Rendering the DSN from inside the
  handler for that failure raises `InvalidDsn` a second time for a blank one, and the
  operator gets a traceback over the screen meant to say what is wrong.

`make_url` rather than `urllib.parse`, deliberately: this must report the DSN as the
thing that *connects* parses it, and a second parser is a second opinion.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

#: What stands in for a password. Never the password's length — that is a hint.
MASK = "***"

#: Every `Settings` field excluded from the dump because its value is a credential.
#:
#: `Field(repr=False)` does **not** suppress `model_dump`, re-verified 2026-08-26 with a
#: canary, so this set is the only thing between the Fernet key and every cron log.
#: `fantabot_database_url` is here because it embeds one, and is reported separately and
#: masked rather than simply withheld.
SECRET_FIELDS = frozenset(
    {
        "stats_source_api_key",
        "lega_password",
        "fantabot_database_url",
        "fantabot_encryption_key",
        "fantabot_agent_auth_token",
    }
)

#: Settings fields that hold a DSN but are not secret as a whole: rendered through
#: `safe_dsn` instead of dumped, so a password in one cannot reach the screen.
MASKED_DSN_FIELDS = ("fantabot_test_database_url",)

#: Query keys libpq reads a credential from.
_SECRET_QUERY_KEYS = frozenset({"password", "sslpassword"})

#: The secrets reported as a bare "set / not set". Masked is not the same as invisible:
#: the operator ran this to find out whether the key is there at all.
#:
#: Derived from `SECRET_FIELDS` rather than listed again, so a secret added to one is not
#: silently missing from the other. The DSN is the single exclusion and it has its own
#: line — a `fantabot_database_url set: True` would say nothing, since it always is.
REPORTED_SECRETS = tuple(sorted(SECRET_FIELDS - {"fantabot_database_url"}))


class InvalidDsn(ValueError):
    """The configured DSN does not parse. Named so each surface can say so its own way.

    Worth its own type rather than a bare `ArgumentError`: the remedy is editing `.env`
    or unsetting `FANTABOT_DATABASE_URL`, and this is exactly the command an operator
    runs to find that out. Failing at the first connect instead would report it as a
    database being down.
    """


class BlankDsn(InvalidDsn):
    """The DSN is empty or whitespace: no database was named at all.

    Kept apart from a DSN that is present and wrong so each screen can say which it is. A
    blank one is most often a `$(fantabot-app db url ...)` that came back empty, and libpq
    reads an empty connection string as "use the defaults" — so it must be refused before it
    reaches anything that would.
    """


@dataclass(frozen=True)
class ConfigReport:
    """What both surfaces render. No Rich, no Pydantic response model, no `Console`."""

    #: The non-secret settings, resolved — `model_dump` minus `SECRET_FIELDS`.
    settings: Mapping[str, object]
    #: Secret field name -> whether it holds anything. Never the value, never its length.
    secrets_set: Mapping[str, bool]
    #: The DSN, masked and paste-safe. Empty when `database_url_error` is set.
    database_url: str
    #: Why the DSN could not be rendered, or `None`. **The report is produced either
    #: way**: one unparseable field must not cost the operator the whole screen, which is
    #: the same argument `lega_sync` makes for failing per-read.
    database_url_error: str | None = None
    #: The same verdict for `fantabot_test_database_url`, or `None`. Only a value that is
    #: *present* and unusable is a problem: unset is the bundled `fantabot_test`, which is
    #: what the report has always shown. Worded by `dsn_problem`, so it never repeats the
    #: value — what failed to parse may still hold a password.
    test_database_url_error: str | None = None


_BLANK = "the value is blank"
_UNPARSEABLE = "the value does not parse as a database URL"

#: What to do about a DSN that cannot be used. It says where the setting comes from because
#: an exported variable outranks `.env`, so a blank in either decides, and that the cure is to
#: *unset* it: a blank is not the same as absent, which is the whole reason it fails.
_UNUSABLE_REMEDY = (
    "FANTABOT_DATABASE_URL is read from the environment, then from .env. Unset it to use "
    "the bundled server, or set a full postgresql+psycopg2:// URL; `fantabot config-check` "
    "shows what was read."
)


def safe_dsn(dsn: str) -> str:
    """`dsn` with any password replaced by `MASK`, and nothing else changed. Pure.

    Reassembled from the parsed components rather than rendered by SQLAlchemy, because
    `render_as_string` percent-encodes the query string and masks an empty password. The
    three cases are kept apart on purpose:

    * no password at all (`postgres@host`) -> no `:` is invented;
    * an empty password (`postgres:@host`, the bundled server's own shape) -> the `:`
      survives and nothing follows it, so the line is byte-identical to what
      `fantabot-app db url` prints and to what `config.bundled_database_url` derives;
    * a real password -> `:***@`.

    Raises `InvalidDsn` rather than returning a placeholder: a DSN that does not parse is
    a misconfiguration to report, not a value to render.
    """
    from sqlalchemy.engine import make_url
    from sqlalchemy.exc import ArgumentError

    if not dsn.strip():
        raise BlankDsn(_BLANK)

    try:
        url = make_url(dsn)
    except (ArgumentError, ValueError) as exc:
        raise InvalidDsn(str(exc)) from exc

    userinfo = url.username or ""
    if url.password is not None:
        # `is not None`, not truthiness: `""` and "no password" are different facts and
        # the screen exists to tell them apart.
        userinfo = f"{userinfo}:{MASK if url.password else ''}"

    hostport = url.host or ""
    if url.port:
        hostport = f"{hostport}:{url.port}"

    netloc = f"{userinfo}@{hostport}" if userinfo else hostport
    rendered = f"{url.drivername}://{netloc}/{url.database or ''}"

    if url.query:
        # Unencoded, which is the point. A value may be a tuple when a key repeats.
        pairs = [
            f"{key}={MASK if key.lower() in _SECRET_QUERY_KEYS else item}"
            for key, value in url.query.items()
            for item in (value if isinstance(value, tuple) else (value,))
        ]
        rendered = f"{rendered}?{'&'.join(pairs)}"
    return rendered


def dsn_problem(dsn: str) -> str | None:
    """Why `dsn` cannot be used, or `None` when it can. Pure.

    Worded without the DSN: a value that fails to parse may still be a URL with a password
    in it, and `str(InvalidDsn)` is SQLAlchemy's text, which older versions echo back.
    """
    try:
        safe_dsn(dsn)
    except BlankDsn:
        return _BLANK
    except InvalidDsn:
        return _UNPARSEABLE
    return None


@dataclass(frozen=True)
class DatabaseTarget:
    """The configured database as a screen that could not use it names it."""

    #: Whether the DSN parses. When `False` the server is not what is wrong.
    usable: bool
    #: The first line to print: the masked DSN that could not be reached, or why there is none.
    headline: str
    #: What to do next. Never "start the server" for a DSN that names no server.
    remedy: str

    def detail(self, exc: BaseException) -> str:
        """The line naming the failure: its type and the first line of its message.

        The type alone when the DSN is not usable. The driver's message for a URL it could
        not parse can be the URL, and an unparseable one is exactly what `headline` declined
        to repeat.
        """
        first = (str(exc).splitlines() or [""])[0]
        if self.usable and first:
            return f"{type(exc).__name__}: {first}"
        return type(exc).__name__


def database_target(dsn: str) -> DatabaseTarget:
    """Name `dsn` for a screen reporting a database it could not use. Never raises.

    The handler for an unreachable database is the last place a second exception is
    acceptable, and a blank `FANTABOT_DATABASE_URL` is a way to reach that handler: the
    engine refuses the value, then the handler renders it.
    """
    problem = dsn_problem(dsn)
    if problem is not None:
        return DatabaseTarget(
            usable=False,
            headline=f"Cannot use the database URL: {problem}.",
            remedy=_UNUSABLE_REMEDY,
        )
    return DatabaseTarget(
        usable=True,
        headline=f"Cannot reach the database at {safe_dsn(dsn)}",
        remedy="Start it with: fantabot-app db start",
    )


def build_report() -> ConfigReport:
    """Read `settings` and assemble the report. The only I/O here is reading the object.

    Never raises on a bad DSN — see `ConfigReport.database_url_error`.
    """
    from fantabot.config import settings

    try:
        database_url, error = safe_dsn(settings.fantabot_database_url), None
    except InvalidDsn as exc:
        database_url, error = "", str(exc)

    dumped = settings.model_dump(mode="json", exclude=set(SECRET_FIELDS))
    for name in MASKED_DSN_FIELDS:
        # A blank value has nothing to withhold; masking it would show `***` for a line that
        # holds nothing, and `test_database_url_error` is what says it is wrong.
        if str(dumped.get(name) or "").strip():
            try:
                dumped[name] = safe_dsn(dumped[name])
            except InvalidDsn:
                # An unparseable value cannot be masked reliably, so it is withheld whole.
                dumped[name] = MASK

    return ConfigReport(
        # `mode="json"` so a `Path` field comes back as its string. Two reasons, and
        # neither is the API's serialiser: `PosixPath('data/storage_state.json')` is not
        # a path an operator can copy, and a report both surfaces render may not carry a
        # type only one of them can show.
        settings=dumped,
        secrets_set={name: bool(getattr(settings, name, "")) for name in REPORTED_SECRETS},
        database_url=database_url,
        database_url_error=error,
        test_database_url_error=dsn_problem(settings.fantabot_test_database_url),
    )
