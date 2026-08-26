"""Re-export shim. The machinery moved out of the seed package.

``upsert_two_passes`` and its helpers live in ``fantabot.db.upserts``; the two
date parsers live in ``fantabot.parsing``. This module dies with the rest of
``db/importers/`` once the CSV seed is retired, and exists only so
``importers/voti.py`` and ``importers/bonus_malus.py`` keep resolving until then.

Redundant-alias form for the same reason as ``_csv.py``: ``strict = true``
implies ``no_implicit_reexport``.
"""

from fantabot.db.upserts import CHUNK as CHUNK
from fantabot.db.upserts import chunked as chunked
from fantabot.db.upserts import table_for as table_for
from fantabot.db.upserts import upsert_two_passes as upsert_two_passes
from fantabot.parsing import parse_date as parse_date
from fantabot.parsing import parse_time as parse_time
