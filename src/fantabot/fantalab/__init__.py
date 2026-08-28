"""Own-room live connection and the write (bid) path for `app.fantalab.it`.

Where `aste/` *harvests* public auctions read-only, this package connects to our **own** room:
the unauthenticated REST bootstrap (`rest`), the RTDB read/write transport (later), and the bid
loop (later). The protocol is documented and verified in
`docs/fantalab/06-asta-write-path.md` — reads and participant bids need no token; only settling
as admin does.

Like `aste/`, the capture path never imports `fantabot.db`: an outage must cost catch-up time,
never a bid.
"""
