"""FastAPI application factory for FastAPI Template"""

# instantiate Settings (see the comment on the call below), so those imports
# intentionally sit after a statement. This is the documented app-factory order,
# not a lint slip.

import logging
from contextlib import asynccontextmanager
from typing import Any

# Load configuration into os.environ FIRST, before importing anything that
# instantiates Settings. load_configuration() walks up to the project .env
# (CWD-independent, so `cd api && uvicorn` finds the root-level .env); in Docker
# it is a no-op because the vars are already real env vars injected by compose
# env_file. Settings then read os.environ only.
from fantabot_app.api.infrastructure.config import load_configuration

load_configuration()

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.docs import get_redoc_html, get_swagger_ui_html

from fantabot_app.api.infrastructure.settings import settings
from fantabot_app.api.v1.router import api_router

# Configure structured logging
logging.basicConfig(
    level=getattr(logging, settings.log_level.upper()),
    format=(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
        if settings.log_format == "text"
        else '{"timestamp": "%(asctime)s", "name": "%(name)s", "level": "%(levelname)s", "message": "%(message)s"}'
    ),
)
logger = logging.getLogger(__name__)


# OpenAPI metadata — per-tag descriptions render as grouped sections in Swagger /
# ReDoc. Populated as fantabot endpoints are added under app/api/v1/endpoints/.
TAGS_METADATA: list[dict] = []

# Project identity constants — set once at scaffold time, not per-deployment, so
# they live here rather than in Settings (env vars). Replace with your own.
CONTACT_INFO = {"name": "API Support", "email": "support@example.com"}
LICENSE_INFO = {"name": "MIT", "identifier": "MIT"}


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup/shutdown. The schema is fantabot's (managed by alembic, provisioned by the
    launcher), so there is nothing to create here — sessions come from fantabot's lazy
    ``database_manager`` on first request."""
    logger.info(f"Starting {settings.project_name} (environment={settings.environment})...")
    yield
    logger.info(f"Shutting down {settings.project_name}...")
    from fantabot.adapters.persistence import database_manager

    database_manager.dispose()


def create_application() -> FastAPI:
    """Create and configure the FastAPI application"""

    # Create FastAPI application with correct OpenAPI configuration
    app = FastAPI(
        title=settings.project_name,
        version=settings.version,
        summary="Modern API scaffold with layered architecture.",
        description="Modern API",
        openapi_tags=TAGS_METADATA,
        contact=CONTACT_INFO,
        license_info=LICENSE_INFO,
        docs_url=None,  # Disable default docs - we'll set up custom ones
        redoc_url=None,  # Disable default redoc - we'll set up custom ones
        openapi_url=("/openapi.json" if settings.debug else None),
        debug=settings.debug,
        lifespan=lifespan,
    )

    # CORS middleware
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins_list,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"],
        allow_headers=[
            "Authorization",
            "Content-Type",
            "Accept",
            "Origin",
            "User-Agent",
            "X-Requested-With",
            "X-Client-Info",
            "X-Dev-User",
        ],
        expose_headers=["X-Total-Count", "X-Rate-Limit-Remaining"],
        max_age=3600,  # Cache preflight requests for 1 hour
    )

    # Add API routes
    app.include_router(api_router, prefix=settings.api_v1_str)

    # Add health check endpoints
    setup_health_endpoints(app)

    # Setup documentation endpoints based on environment
    setup_documentation_endpoints(app)

    return app


def setup_health_endpoints(app: FastAPI) -> None:
    """Setup health check and monitoring endpoints"""

    @app.get("/")
    async def read_root() -> dict[str, Any]:
        """Root endpoint with API information"""
        return {
            "message": f"Welcome to the {settings.project_name}!",
            "version": settings.version,
            "status": "operational",
            "environment": settings.environment,
            "api_version": "v1",
            "docs_url": "/docs" if settings.debug else None,
            "redoc_url": "/redoc" if settings.debug else None,
        }

    @app.get("/health")
    async def health_check() -> dict[str, str]:
        """Simple health check endpoint - just return OK"""
        return {"status": "ok"}


def setup_documentation_endpoints(app: FastAPI) -> None:
    """Setup documentation endpoints - always accessible in local development"""

    logger.info("Setting up open documentation endpoints for local development")

    @app.get("/docs", include_in_schema=False)
    def swagger_ui():
        """Swagger UI - open access for local development"""
        return get_swagger_ui_html(
            openapi_url="/openapi.json",
            title=f"{app.title} – API Documentation",
            swagger_js_url="https://unpkg.com/swagger-ui-dist@5.11.0/swagger-ui-bundle.js",
            swagger_css_url="https://unpkg.com/swagger-ui-dist@5.11.0/swagger-ui.css",
        )

    @app.get("/redoc", include_in_schema=False)
    def redoc_ui():
        """ReDoc UI - open access for local development"""
        return get_redoc_html(
            openapi_url="/openapi.json",
            title=f"{app.title} – API Documentation",
            redoc_js_url="https://unpkg.com/redoc@2.1.0/bundles/redoc.standalone.js",
        )

    @app.get("/openapi.json", include_in_schema=False)
    def get_openapi_json():
        """OpenAPI JSON schema.

        Delegate to ``app.openapi()`` so the served schema reflects ALL metadata
        set on the app (summary/description/openapi_tags/contact/license_info).
        The 3-arg ``get_openapi(title, version, routes)`` form silently dropped
        them. This route is the sole responder only when ``openapi_url`` is None
        (production); otherwise FastAPI's built-in route serves the same schema.
        """
        return app.openapi()


# Create the application instance
app = create_application()

# For development server compatibility
if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "fantabot_app.api.main:app",
        host="0.0.0.0",
        port=8000,
        reload=settings.debug,
        log_level=settings.log_level.lower(),
    )
