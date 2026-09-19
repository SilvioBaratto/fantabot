"""T28 — `GET /system/config`, the System page's half of `config-check`.

Two surfaces render one report, and the reason that matters is the incident behind this
whole phase: `.env` pointing the CLI at a compose Postgres while the app had provisioned
its own is what let a week of Classic auction collection read as lost. This is the screen
an operator opens to find that out, so what it must never do is answer a *different*
question from the command — which is what a second copy of "which fields are secret"
eventually does.

These tests are about the wire format and about what may not cross it. The masking rules
themselves are `tests/application/test_config_report.py`'s, in the tree that owns them,
and the two surfaces are compared directly by the parity tier.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest
from fastapi.testclient import TestClient

from fantabot_app.api.main import app
from fantabot_app.api.v1.endpoints.system import build_config


@dataclass(frozen=True)
class FakeReport:
    settings: dict[str, Any]
    secrets_set: dict[str, bool]
    database_url: str
    database_url_error: str | None = None


def test_build_config_carries_every_part_of_the_report() -> None:
    """Pure, and the mapping is where a field silently stops being sent."""
    out = build_config(
        FakeReport(
            settings={"fantabot_data_dir": "data", "api_v1_str": "/api/v1"},
            secrets_set={"fantabot_encryption_key": True, "lega_password": False},
            database_url="postgresql+psycopg2://postgres:@/fantabot?host=/tmp/pg",
        )
    )

    assert out.settings == {"fantabot_data_dir": "data", "api_v1_str": "/api/v1"}
    assert out.secrets_set == {"fantabot_encryption_key": True, "lega_password": False}
    assert out.database_url == "postgresql+psycopg2://postgres:@/fantabot?host=/tmp/pg"
    assert out.database_url_error is None


def test_an_unparseable_dsn_does_not_cost_the_rest_of_the_screen() -> None:
    """The report is produced either way — `outcomes.py`'s rule is *degrade open on a
    status read*, and every other setting is still the answer to a real question."""
    out = build_config(
        FakeReport(
            settings={"fantabot_data_dir": "data"},
            secrets_set={"lega_password": False},
            database_url="",
            database_url_error="Could not parse SQLAlchemy URL from string '::nope::'",
        )
    )

    assert out.database_url == ""
    assert out.database_url_error is not None
    assert out.settings == {"fantabot_data_dir": "data"}


def test_the_route_answers_and_names_the_database() -> None:
    body = TestClient(app).get("/api/v1/system/config").json()

    assert body["database_url"].startswith("postgresql")
    assert body["database_url_error"] is None
    assert body["settings"], "the settings dump is empty"
    assert body["secrets_set"], "no secret was reported as set or unset"


def test_no_secret_field_appears_in_the_settings_dump() -> None:
    """The exclusion is `config_report`'s; this is the assertion that it survived the
    wire. `Field(repr=False)` does not suppress `model_dump`, so the exclude set is the
    only thing between the Fernet key and this response."""
    from fantabot.application.config_report import SECRET_FIELDS

    body = TestClient(app).get("/api/v1/system/config").json()

    assert SECRET_FIELDS.isdisjoint(body["settings"]), sorted(
        SECRET_FIELDS & set(body["settings"])
    )


@pytest.mark.parametrize(
    "canary_field",
    ["fantabot_encryption_key", "lega_password", "stats_source_api_key",
     "fantabot_agent_auth_token"],
)
def test_a_canary_value_never_reaches_the_response(
    monkeypatch: pytest.MonkeyPatch, canary_field: str
) -> None:
    """Asserted on the value, not on the field name.

    A name-only check passes on an empty machine — which is every CI machine — and this
    response is served over the network without authentication. The canary is set first
    so the assertion is about a secret that actually exists.
    """
    from fantabot import config

    monkeypatch.setattr(config.settings, canary_field, "CanaryZZZ777")
    response = TestClient(app).get("/api/v1/system/config")

    # The raw body, not the parsed one: a secret nested somewhere unexpected is still a
    # secret on the wire, and `in body["settings"]` would not see it.
    assert "CanaryZZZ777" not in response.text
    assert response.json()["secrets_set"].get(canary_field) is True, (
        f"{canary_field} is set but not reported as set — masked is not the same as "
        "invisible, and the operator opened this to find out"
    )


def test_the_dsn_password_never_reaches_the_response(monkeypatch: pytest.MonkeyPatch) -> None:
    """The DSN is excluded from the dump *and* masked on its own line — two mechanisms,
    because withholding it entirely would hide the answer the page exists to give."""
    from fantabot import config

    monkeypatch.setattr(
        config.settings,
        "fantabot_database_url",
        "postgresql+psycopg2://bot:DsnCanary777@db.example.test:6543/otherdb",
    )
    raw = TestClient(app).get("/api/v1/system/config").text

    assert "DsnCanary777" not in raw
    assert "db.example.test" in raw, "masking hid the host, which is the answer"
    assert "otherdb" in raw
