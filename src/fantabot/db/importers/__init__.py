"""CSV importers: the one-time seed that turns the flat files into tables.

Every importer is registered here, and the registry's iteration order encodes
dimensions-before-facts permanently — ``players`` and ``teams`` have no outbound
foreign keys and must load before anything that points at them.
"""

from fantabot.db.importers._csv import italian_decimal, plain_decimal, split_codes

__all__ = [
    "italian_decimal",
    "plain_decimal",
    "split_codes",
]
