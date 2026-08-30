"""The reads a news run needs. The I/O edge of this package, and all of it.

Everything else under `news/` is pure — the join, the prompt, the row flattening, the
fan-out — and that is not incidental. `pipeline.fetch_all` returns rows rather than
persisting them so the whole fan-out (concurrency cap, backoff, failure isolation,
ordering) is testable with fakes and no database, and a test enforces it by refusing to
let the string `fantabot.db` appear in that module at all.

`load_pool` used to live in `pool.py`, with its repository import inside the function
body. Two modules import `PoolPlayer` from there for the dataclass alone, so that one
import pulled `prompt.py` and `store.py` into the database's import graph as well. It
was tried in `pipeline.py` next, which is what the never-writes check is for -- it is a
read, not a write, but the check is deliberately blunt and weakening it to admit one
read is how it stops meaning anything.

So the query gets its own module. One function is a small file; the alternative was
putting a `quotazioni` read inside the module that flattens sentiment rows, which costs
a reader more than a file does.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fantabot.news.pool import PoolPlayer, build_pool

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


def load_pool(session: Session, season: str) -> list[PoolPlayer]:
    """Fetch both listoni for one season and join them."""
    from fantabot.db.repositories.reference import ReferenceRepository

    repo = ReferenceRepository(session)
    return build_pool(
        repo.quotazioni(season, "classic"), repo.quotazioni(season, "mantra"), season
    )
