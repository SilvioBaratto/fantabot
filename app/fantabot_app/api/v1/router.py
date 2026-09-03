"""API v1 router - imports and includes all route modules.

Empty for now: the demo routers (auth/items/users/chatbot/test) were removed
during cleanup. Add fantabot endpoint modules under ``app/api/v1/endpoints/``
and register them here with ``api_router.include_router(...)``.
"""

from fastapi import APIRouter

from fantabot_app.api.v1.endpoints import jobs

# Create the main API router
api_router = APIRouter()
api_router.include_router(jobs.router)
