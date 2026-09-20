"""API v1 router - imports and includes all route modules.

Empty for now: the demo routers (auth/items/users/chatbot/test) were removed
during cleanup. Add fantabot endpoint modules under ``app/api/v1/endpoints/``
and register them here with ``api_router.include_router(...)``.
"""

from fastapi import APIRouter

from fantabot_app.api.v1.endpoints import (
    actions,
    asta,
    auth,
    db,
    dump,
    exclusions,
    harvest,
    jobs,
    lega,
    legality,
    lineup,
    news,
    pricing,
    room,
    room_bid,
    scrape,
    system,
    teams,
)

# Create the main API router
api_router = APIRouter()
api_router.include_router(jobs.router)
api_router.include_router(db.router)
api_router.include_router(exclusions.router)
api_router.include_router(auth.router)
api_router.include_router(news.router)
api_router.include_router(lega.router)
api_router.include_router(asta.router)
api_router.include_router(harvest.router)
api_router.include_router(room.router)
api_router.include_router(room_bid.router)
api_router.include_router(pricing.router)
api_router.include_router(legality.router)
api_router.include_router(lineup.router)
api_router.include_router(actions.router)
api_router.include_router(system.router)
api_router.include_router(teams.router)
api_router.include_router(scrape.router)
api_router.include_router(dump.router)
