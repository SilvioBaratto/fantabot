"""Re-export shim. The parsers live in ``fantabot.parsing`` now.

This module dies with the rest of ``db/importers/`` once the CSV seed is
retired; it exists only so the five importer modules and
``db/importers/__init__.py`` keep resolving until then.

The redundant-alias form is deliberate. ``[tool.mypy] strict = true`` implies
``no_implicit_reexport``, so a bare ``from fantabot.parsing import
italian_decimal`` would make every module reading it *through* this shim fail
with *"Module ... does not explicitly export attribute"*, and ruff would flag the
name as ``F401`` here. Measured against all three forms before choosing.
"""

from fantabot.parsing import (
    italian_decimal as italian_decimal,
)
from fantabot.parsing import (
    plain_decimal as plain_decimal,
)
from fantabot.parsing import (
    split_codes as split_codes,
)
from fantabot.parsing import (
    split_flags as split_flags,
)
