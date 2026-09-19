"""The resolved settings — `config-check`, on the System page.

**One report, two surfaces.** `application/config_report.build_report` decides which
fields are secret and how the DSN renders; this is a printer, exactly as the Typer body
is. That is not tidiness: this screen exists to answer "which database am I on?" when the
CLI and the app disagree, and an answer assembled twice can disagree in the same way.

**No outcome tuple, deliberately.** `api/outcomes.py`'s rule is *degrade open on a status
read, fail closed on a decision*, and this is as far toward "status read" as a route gets
— it opens no session, makes no request, and reads a `Settings` object already in memory.
The one thing that can go wrong, a DSN that does not parse, is a **field** on the report
rather than a failure of it: one unparseable setting must not cost the operator every
other setting, which is the same argument `lega_sync` makes for failing per-read.

**What this puts on the network.** The app serves its API without authentication, so
whatever reaches the port reads this — and `server.py` binds `127.0.0.1` while
`main.py`'s dev entrypoint binds `0.0.0.0`. No credential is in the response:
`SECRET_FIELDS` is excluded from the dump, the DSN's password is masked, and the secrets
are reported as bare booleans. What does travel is configuration — the operator's
`lega_email`, absolute filesystem paths, the database's name and host. That is the same
material the CLI writes into every cron log, and less than `/lega` and `/auth/status`
already serve; it is recorded here so the next person to widen the bind address knows
what they are widening.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter()


class SystemConfig(BaseModel):
    """The response. Named apart from `config_report.ConfigReport` on purpose — one is
    the decision, one is its wire format, and collapsing them is how a response model
    starts deciding things."""

    #: The non-secret settings, resolved. JSON-safe: a `Path` arrives as its string.
    settings: dict[str, Any]
    #: Secret field name -> whether it holds anything. Never the value, never its length.
    secrets_set: dict[str, bool]
    #: The DSN, masked and paste-safe. Empty when `database_url_error` is set.
    database_url: str
    #: Why the DSN could not be rendered, or `null`. The rest of the report still arrives.
    database_url_error: str | None = None


def build_config(report: Any) -> SystemConfig:
    """Map a `ConfigReport` to the response (pure; unit-testable without a Settings)."""
    return SystemConfig(
        settings=dict(report.settings),
        secrets_set=dict(report.secrets_set),
        database_url=report.database_url,
        database_url_error=report.database_url_error,
    )


@router.get("/system/config", response_model=SystemConfig, tags=["system"])
def system_config() -> SystemConfig:
    """What `fantabot config-check` prints, for the System page."""
    from fantabot.application.config_report import build_report

    return build_config(build_report())
