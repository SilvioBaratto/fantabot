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
            f"{key}={item}"
            for key, value in url.query.items()
            for item in (value if isinstance(value, tuple) else (value,))
        ]
        rendered = f"{rendered}?{'&'.join(pairs)}"
    return rendered


def build_report() -> ConfigReport:
    """Read `settings` and assemble the report. The only I/O here is reading the object.

    Never raises on a bad DSN — see `ConfigReport.database_url_error`.
    """
    from fantabot.config import settings

    try:
        database_url, error = safe_dsn(settings.fantabot_database_url), None
    except InvalidDsn as exc:
        database_url, error = "", str(exc)

    return ConfigReport(
        settings=settings.model_dump(exclude=set(SECRET_FIELDS)),
        secrets_set={name: bool(getattr(settings, name, "")) for name in REPORTED_SECRETS},
        database_url=database_url,
        database_url_error=error,
    )
