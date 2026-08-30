"""Harvesting live FantaLab auctions: the pure half.

Reducing an SSE frame to a state, folding states into a ladder, reconstructing an
evening, comparing two collectors. One record in, a value out -- no socket, no clock, no
database.

The rest of the harvester is in the layers that own its edges: `adapters/http/harvest/`
subscribes, `adapters/files/landing.py` appends to the landing zone,
`application/harvest_loader.py` folds it into Postgres, and `interface/harvest.py` is the
command.

The database is not on the collection path, and that is enforced rather than intended --
`tests/test_aste_outage.py` walks the imports of every capture module and fails if any
can reach it. An outage must cost catch-up time and never a record.
"""
