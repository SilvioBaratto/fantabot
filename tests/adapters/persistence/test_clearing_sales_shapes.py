"""The corpus shapes that are actually recorded, and the refusal for one that is not.

Marked `dbdata`: these assert against 614,163 rows that were really scraped, harvested and
synced, and there is no fixture for them. That is the point — the numbers below are the
evidence that a refusal is affordable. The corpus holds **twelve or more** shapes, and the
one our code defaulted to for months (8x500 mantra, 6,625 sales) is the *sixth* largest.
10x500 classic has 33,078.

Before 1.6, asking for a shape nobody recorded returned `[]`. That is a legal argument to
`mean_prices`, `DEFAULT_PRICE = 1` then applies to everybody, the budget constraint goes
vacuous, and nothing raises — measured 2026-09-05 as a 25-man Classic rosa bought for 25
credits of 500.
"""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from fantabot.adapters.persistence.repositories.aste import AsteRepository
from fantabot.domain.asta.prices import NoCorpus

pytestmark = [pytest.mark.db, pytest.mark.dbdata]


def test_our_own_shape_is_recorded_for_both_formats(db_session: Session) -> None:
    """The guard that makes the refusal below mean something: it is not refusing everything."""
    repo = AsteRepository(db_session)

    assert repo.clearing_sales(asta_type="mantra", budget=500, num_teams=8)
    assert repo.clearing_sales(asta_type="classic", budget=500, num_teams=8)


def test_an_unrecorded_shape_is_refused_rather_than_priced_at_one_credit(
    db_session: Session,
) -> None:
    with pytest.raises(NoCorpus) as caught:
        AsteRepository(db_session).clearing_sales(asta_type="mantra", budget=777, num_teams=3)

    assert "3x777 mantra" in str(caught.value)


def test_the_refusal_lists_shapes_that_really_do_price(db_session: Session) -> None:
    """A refusal naming a shape that then prices nothing would be worse than the refusal.

    So `recorded_shapes` counts sales exactly as `clearing_sales` filters them — buyer and
    player link both present — rather than approximating with a room count.
    """
    repo = AsteRepository(db_session)

    with pytest.raises(NoCorpus) as caught:
        repo.clearing_sales(asta_type="mantra", budget=777, num_teams=3)

    listed = caught.value.recorded
    assert listed, "the refusal listed nothing at all against a 614k-row corpus"

    teams, _, rest = listed[0].partition("x")
    credits, _, tail = rest.partition(" ")
    asta_type = tail.split(" ")[0]
    assert repo.clearing_sales(
        asta_type=asta_type, budget=int(credits), num_teams=int(teams)
    ), f"the refusal offered {listed[0]}, which prices nothing"


def test_the_shapes_are_ordered_by_how_much_evidence_they_carry(db_session: Session) -> None:
    """Most sales first: the operator's next move is to pick one, and the biggest corpus is
    the one they most likely meant. Our default 8x500 mantra is not it — it is sixth."""
    shapes = AsteRepository(db_session).recorded_shapes()
    counts = [int(row.rsplit("(", 1)[1].split(" ")[0]) for row in shapes]

    assert counts == sorted(counts, reverse=True)
    assert len(shapes) >= 6, f"the corpus holds only {len(shapes)} shapes"


def test_a_ten_by_one_thousand_sweep_prices_off_a_ten_by_one_thousand_corpus(
    db_session: Session,
) -> None:
    """1.6's acceptance, and the defect it closes: `asta calibrate` forwarded `--teams` and
    `--credits` to the replay corpus and called `read_plan_inputs` with no shape at all, so
    it graded a 10x1000 corpus against prices averaged from 8x500 rooms."""
    from fantabot.application.asta_planner import read_plan_inputs

    ours = read_plan_inputs(
        db_session, season="2026/27", sentiment=None, as_of=None, tilt_k=0.25,
        listone="mantra", num_teams=8, num_credits=500,
    )
    theirs = read_plan_inputs(
        db_session, season="2026/27", sentiment=None, as_of=None, tilt_k=0.25,
        listone="mantra", num_teams=10, num_credits=1000,
    )

    assert ours.prices != theirs.prices, (
        "two different league shapes produced identical prices — the shape is not reaching "
        "the corpus read"
    )
