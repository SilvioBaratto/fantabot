"""Target prices — the 2026/27 target-price research model (QI fade + team discount).

Wraps fantabot.application.pricing.run, which fits the model, upserts the target_price
cache (an idempotent refresh — the one non-read here) and returns a pure PricingReport.
Opens its own sessions. Degrades open (found=false on no data / error).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter()


class TargetPrice(BaseModel):
    id: str
    nome: str
    squadra: str
    role: str
    macro_role: str
    qi: int
    prior_media_fantavoto: float | None = None
    predicted_pct_delta: float | None = None
    team_factor: float
    target_price: int
    flags: str


class Fade(BaseModel):
    role: str
    observations: int


class TargetPricesReport(BaseModel):
    found: bool
    #: One of `api/outcomes.TARGET_PRICES_OUTCOMES`. `no_data` is a real answer here rather
    #: than a failure — the fit needs training seasons of `statistiche`, and a fresh
    #: install has none, which is not the same as a database that will not open.
    outcome: str = "priced"
    reason: str | None = None
    system: str = ""
    stored: int = 0
    fades: list[Fade] = []
    biggest_bumps: list[TargetPrice] = []
    biggest_cuts: list[TargetPrice] = []
    flag_counts: dict[str, int] = {}


def _target_price(row: Any) -> TargetPrice:
    return TargetPrice(
        id=row.id,
        nome=row.nome,
        squadra=row.squadra,
        role=row.role,
        macro_role=row.macro_role,
        qi=row.qi,
        prior_media_fantavoto=row.prior_media_fantavoto,
        predicted_pct_delta=row.predicted_pct_delta,
        team_factor=row.team_factor,
        target_price=row.target_price,
        flags=row.flags,
    )


def build_report(report: Any) -> TargetPricesReport:
    """Map a PricingReport to the response (pure)."""
    return TargetPricesReport(
        found=True,
        outcome="priced",
        system=report.system,
        stored=report.stored,
        fades=[Fade(role=f.role, observations=f.observations) for f in report.fades],
        biggest_bumps=[_target_price(r) for r in report.biggest_bumps],
        biggest_cuts=[_target_price(r) for r in report.biggest_cuts],
        flag_counts=dict(report.flag_counts),
    )


@router.get("/asta/target-prices", response_model=TargetPricesReport, tags=["asta"])
def target_prices(system: str = "classic", top_n: int = 15) -> TargetPricesReport:
    from fantabot.application import pricing

    from fantabot_app.api.outcomes import because

    try:
        report = pricing.run(system=system, top_n=top_n)
    except (LookupError, ValueError) as exc:
        # Nothing to fit on. `LookupError` covers `NoCorpus`; `ValueError` covers an
        # unrecognised system, which the fit refuses rather than treating as empty.
        return TargetPricesReport(found=False, outcome="no_data", reason=str(exc))
    except Exception as exc:  # noqa: BLE001 — the last named outcome, not a catch-all
        return TargetPricesReport(found=False, outcome="unreachable", reason=because(exc))

    if not report.fades and not report.biggest_bumps and not report.biggest_cuts:
        # A report with nothing in it. It used to render as an empty table under a
        # confident heading, which reads as "the model says nothing moved".
        return TargetPricesReport(
            found=False,
            outcome="no_data",
            reason=(
                f"no {system} training data — the fit needs `statistiche` for the training "
                "seasons. Run `fantabot db scrape statistiche`."
            ),
        )
    return build_report(report)
