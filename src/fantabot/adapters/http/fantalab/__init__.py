"""Own-room live connection and the write (bid) path for `app.fantalab.it`.

Where `adapters/http/harvest/` *harvests* public auctions read-only (`aste/` when this was
written), this package connects to our **own** room: the unauthenticated REST bootstrap
(`rest`), the RTDB read/write transport (later), and the bid loop (later). The protocol is
documented and verified in `docs/fantalab/06-asta-write-path.md` — reads and participant
bids need no token; only settling as admin does.

Like the harvest collector, the capture path never imports
`fantabot.adapters.persistence`: an outage must cost catch-up time, never a bid.
"""
