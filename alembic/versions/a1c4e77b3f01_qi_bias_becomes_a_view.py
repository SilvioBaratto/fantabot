"""qi_bias becomes a view over quotazioni

Every one of its 5,356 rows is derivable, and the proof is in ``tasks/w4-proofs.out``
§1: all 5,356 join ``quotazioni`` on ``(stagione, player_id, listone)``, all agree on
``qi``, ``qa``, ``fvm``, ``squadra`` and ``ruoli_codice``, and ``delta`` is exactly
``qa - qi`` on every row. Storing it was a denormalisation for three analysis scripts,
and all three were deleted on 2026-08-30.

Two things the derivation needs, both measured rather than assumed:

**Half-even rounding.** ``pct_delta`` reproduces on 5,355 of 5,356 under Postgres's
``round()``, which rounds half away from zero. The exception is 2024/25 player 6675:
``-9/32`` is ``-0.28125`` exactly, stored ``-28.12`` where ``round()`` gives ``-28.13``
— Python's banker's rounding, because that is what wrote the CSV. The view emulates it,
and the ``EXCEPT`` in the acceptance is what proves it.

**A season predicate, which encodes an artefact.** ``quotazioni`` holds 6,452 rows
across five seasons; this table held 5,356 across four, missing all 1,096 of 2026/27.
That gap is not a property of the data — 182 of those rows already carry drift. It is
the season list of ``scripts/analyze_qi_bias.py``, which produced the table and has
been deleted. The predicate reproduces the table exactly, which is what makes this
migration a provable no-op for every reader.

    ⚠ SOMEONE MUST WIDEN THIS. When 2026/27 closes, add it to SEASONS below. The
      view is not "the four seasons"; it is "the seasons the retired script happened
      to fetch", and nothing else in the schema knows that.

``created_at``/``updated_at`` are not carried: they are storage metadata about rows
that no longer exist, and no reader selects them (``db/scraping.py::load_bias_rows``
takes stagione, player_id, nome, squadra, ruoli_codice, qi, delta, pct_delta).

Revision ID: a1c4e77b3f01
Revises: 1942efd6a2dc
Create Date: 2026-08-30
"""

from collections.abc import Sequence

from alembic import op

revision: str = "a1c4e77b3f01"
down_revision: str | Sequence[str] | None = "1942efd6a2dc"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: The seasons the deleted producer fetched. See the warning above.
SEASONS = ("2022/23", "2023/24", "2024/25", "2025/26")

_VIEW = """
CREATE VIEW qi_bias AS
SELECT q.stagione,
       q.player_id,
       q.listone,
       q.squadra,
       q.ruoli_codice,
       q.qi,
       q.qa,
       q.fvm,
       (q.qa - q.qi)::smallint AS delta,
       -- Banker's rounding to 2dp, matching the Python that produced the table.
       -- Postgres round() is half-away-from-zero and disagrees on exact halves.
       CASE
           WHEN abs(((q.qa - q.qi)::numeric * 100 / q.qi) * 100
                    - trunc(((q.qa - q.qi)::numeric * 100 / q.qi) * 100)) = 0.5
           THEN (CASE
                     WHEN trunc(((q.qa - q.qi)::numeric * 100 / q.qi) * 100)::bigint % 2 = 0
                     THEN trunc(((q.qa - q.qi)::numeric * 100 / q.qi) * 100)
                     ELSE trunc(((q.qa - q.qi)::numeric * 100 / q.qi) * 100)
                          + sign((q.qa - q.qi)::numeric * 100 / q.qi)
                 END) / 100
           ELSE round((q.qa - q.qi)::numeric * 100 / q.qi, 2)
       END::numeric(8, 2) AS pct_delta
FROM quotazioni q
WHERE q.stagione IN ('2022/23', '2023/24', '2024/25', '2025/26')
"""

_TABLE = """
CREATE TABLE qi_bias (
    stagione     VARCHAR(7)  NOT NULL,
    player_id    BIGINT      NOT NULL,
    listone      VARCHAR(7)  NOT NULL,
    squadra      VARCHAR(3)  NOT NULL,
    ruoli_codice TEXT[]      NOT NULL,
    qi           SMALLINT    NOT NULL,
    qa           SMALLINT    NOT NULL,
    fvm          SMALLINT    NOT NULL,
    delta        SMALLINT    NOT NULL,
    pct_delta    NUMERIC(8,2) NOT NULL,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT pk_qi_bias PRIMARY KEY (stagione, player_id, listone),
    CONSTRAINT ck_qi_bias_delta_is_derived CHECK (delta = qa - qi),
    CONSTRAINT ck_qi_bias_listone CHECK (listone IN ('classic', 'mantra')),
    CONSTRAINT fk_qi_bias_player_id_players FOREIGN KEY (player_id) REFERENCES players (id),
    CONSTRAINT fk_qi_bias_stagione_teams FOREIGN KEY (stagione, squadra)
        REFERENCES teams (stagione, codice)
)
"""


def upgrade() -> None:
    """Drop the table, create the view. The rows survive as a derivation."""
    op.execute("DROP TABLE qi_bias")
    op.execute(_VIEW)


def downgrade() -> None:
    """Recreate the table and refill it from the view's own definition.

    Reversible, and not merely in shape: the rows come back with the same values,
    because they were always a function of ``quotazioni``. The timestamps do not —
    they described when a deleted importer wrote a row, and nothing reads them.
    """
    op.execute("DROP VIEW qi_bias")
    op.execute(_TABLE)
    op.execute(
        "INSERT INTO qi_bias (stagione, player_id, listone, squadra, ruoli_codice, "
        "qi, qa, fvm, delta, pct_delta) "
        + _VIEW.split("CREATE VIEW qi_bias AS", 1)[1]
    )
