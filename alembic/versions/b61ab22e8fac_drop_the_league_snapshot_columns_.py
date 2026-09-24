"""drop the sixteen league-snapshot columns nothing reads

Sixteen columns across four `league_*` tables, written by every `fantabot lega sync`
since 2026-09-02 and read by nothing: no Python attribute, no `text()` SQL, no app route
or response model, no frontend field. Measured over the whole tree on 2026-09-24, one
column at a time, before a single one was dropped.

`league_custom_role`'s `nome`, `club`, `original_role` and `role` measure unread too, and
are **deliberately kept**. Dropping them would leave that table stating that a player had
*an* override without saying what it was, and `models/league.py` records that the override
is stored on purpose, unwired from L1, against the day a Classic seam wants it. Four
unread columns that answer a question are not the same thing as four that answer none.

* **`league_player_pool`** loses `quotazione`, `fvm_classic`, `fvm_mantra` and
  `ruoli_codice` — every non-key column it had. What is left is the *membership* of the
  lega's pool on a date, which nothing else records; the per-player numbers are what
  `quotazioni` and the listone already hold per season rather than per sync.
* **`league_competition`** loses `nome`, `tipo`, `start_day`, `end_day` and `team_ids`.
  `deleted` stays and is read — `ShadowRepository.calculated_scores` filters on it.
* **`league_snapshot`** loses `season_id`, `matchday_start`, `active`, `stopped` and
  `captain_slots`. The band the planner actually reads (`role_groups`, `min_roles`,
  `max_roles`, `modules`, `roster_size`, `bench_size`, `matchday`, `budget`) stays.
* **`league_team_snapshot`** loses `division` and `user_id`. `division` held only `'A'`
  and `NULL` over every capture; it was the one column that could have contradicted
  `apileague.DEFAULT_DIVISION`, and a note recording that now sits beside that constant.

**The data is gone, both ways.** `downgrade()` restores the *shape* and nothing else:
every column comes back empty, and the three that were `NOT NULL` come back **nullable**,
because a `NOT NULL` column cannot be added to a table that already has rows and these
tables are append-only archives with hundreds of thousands of them. A downgrade followed
by a sync refills them going forward; the captures taken before this revision do not come
back. Written down here rather than left to be discovered: a downgrade that looks
reversible and is not is worse than one that says so.

Revision ID: b61ab22e8fac
Revises: e35dbc3294a7
Create Date: 2026-09-24
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "b61ab22e8fac"
down_revision: str | Sequence[str] | None = "e35dbc3294a7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: `table -> (column, type)`, in the order the tables are declared in
#: `models/league.py`. One list drives both directions, so a column dropped here and not
#: restored there is not expressible.
DROPPED: tuple[tuple[str, tuple[tuple[str, sa.types.TypeEngine[object]], ...]], ...] = (
    (
        "league_snapshot",
        (
            ("season_id", sa.SmallInteger()),
            ("matchday_start", postgresql.TIMESTAMP(timezone=True)),
            ("active", sa.Boolean()),
            ("stopped", sa.Boolean()),
            ("captain_slots", sa.SmallInteger()),
        ),
    ),
    (
        "league_team_snapshot",
        (
            ("user_id", sa.BigInteger()),
            ("division", sa.Text()),
        ),
    ),
    (
        "league_player_pool",
        (
            ("quotazione", sa.SmallInteger()),
            ("fvm_classic", sa.Integer()),
            ("fvm_mantra", sa.Integer()),
            # Was NOT NULL. Comes back nullable — see the module docstring.
            ("ruoli_codice", postgresql.ARRAY(sa.Text())),
        ),
    ),
    (
        "league_competition",
        (
            # `nome` and `team_ids` were NOT NULL. Both come back nullable.
            ("nome", sa.Text()),
            ("tipo", sa.SmallInteger()),
            ("start_day", sa.SmallInteger()),
            ("end_day", sa.SmallInteger()),
            ("team_ids", postgresql.ARRAY(sa.BigInteger())),
        ),
    ),
)


def upgrade() -> None:
    for table, columns in DROPPED:
        for name, _type in columns:
            op.drop_column(table, name)


def downgrade() -> None:
    """Re-add the columns, **empty and nullable**. It restores the shape, not the data."""
    for table, columns in DROPPED:
        for name, type_ in columns:
            op.add_column(table, sa.Column(name, type_, nullable=True))
