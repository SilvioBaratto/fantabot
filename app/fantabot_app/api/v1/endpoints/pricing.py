"""Target prices — the 2026/27 target-price research model (QI fade + team discount).

**The GET does not write.** It called `pricing.run`, which upserts `target_price`, so
opening the Prices page mutated the database. That is not a slow GET; it is a page whose
refresh is an action, on a route a browser is free to prefetch, retry, or render twice. The
upsert is idempotent, which is exactly why nobody noticed.

So the fit is split: `pricing.fit` reads and reports, `pricing.run` reads, reports and
stores. The GET calls the first and reports `stored=0` honestly; `POST /asta/target-prices`
calls the second, which is what `db price` has always done.

Degrades to a named outcome — see `api/outcomes.TARGET_PRICES_OUTCOMES`.
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


def _report(system: str, top_n: int, *, store: bool) -> TargetPricesReport:
    """The fit, and the two routes' shared refusals. `store` is the only difference."""
    from fantabot.application import pricing

    from fantabot_app.api.outcomes import because

    try:
        report = pricing.run(system=system, top_n=top_n) if store else pricing.fit(
            system=system, top_n=top_n
        )
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


@router.get("/asta/target-prices", response_model=TargetPricesReport, tags=["asta"])
def target_prices(system: str = "classic", top_n: int = 15) -> TargetPricesReport:
    """Read the report. `stored` is 0 because nothing was stored — see the module docstring."""
    return _report(system, top_n, store=False)


@router.post("/asta/target-prices", response_model=TargetPricesReport, tags=["asta"])
def store_target_prices(system: str = "classic", top_n: int = 15) -> TargetPricesReport:
    """Fit and **store**, the way `db price` does. The one route here that writes.

    A POST beside the GET rather than a flag on it: the method is the contract a browser,
    a proxy and an operator all read, and "GET with `?store=1`" is a write nobody can see
    in a request log without knowing this file.
    """
    return _report(system, top_n, store=True)
