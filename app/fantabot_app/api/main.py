"""FastAPI application factory for fantabot-app."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.docs import get_redoc_html, get_swagger_ui_html
from fastapi.responses import HTMLResponse

# Load configuration into os.environ FIRST, before importing anything that
# instantiates Settings. load_configuration() walks up to the project .env
# (CWD-independent, so `cd api && uvicorn` finds the root-level .env); where there is
# no .env — a wheel install, CI, an exported shell — it is a no-op and the real
# environment variables are the whole configuration. Settings then read os.environ only.
from fantabot_app.api.infrastructure.config import load_configuration
from fantabot_app.api.v1.router import api_router

load_configuration()

# The import below, and only it, sits after a statement deliberately: `settings` *is* a
# `Settings` instance, built at the bottom of the module that defines it, so importing it
# before `load_configuration()` has run reads the environment as the shell left it rather
# than as the `.env` does.
#
# Five imports stood here until 2026-09-24 and four of them had no such constraint. The
# three `fastapi` ones are third-party and touch nothing of ours. `api_router` was kept
# here on the stated grounds that it "pulls in the endpoint modules that read `settings`"
# — measured false: importing it brings in 101 `fantabot` modules and neither
# `fantabot_app.api.infrastructure.settings` nor `fantabot.config` is among them, because
# the endpoint modules import their settings inside function bodies. A justified exception
# whose justification is wrong is the shape this file's own `E402` selection exists to
# surface, so it moved up with the others.
#
# `E402` is selected rather than ignored precisely so the one import that still needs it
# has to name itself.
from fantabot_app.api.infrastructure.settings import settings  # noqa: E402 — see above

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


# Project identity constants — set once at scaffold time, not per-deployment, so
# they live here rather than in Settings (env vars). Replace with your own.
CONTACT_INFO = {"name": "API Support", "email": "support@example.com"}
LICENSE_INFO = {"name": "MIT", "identifier": "MIT"}


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
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

    @app.get("/api")
    async def read_root() -> dict[str, Any]:
        """API index. NOT mounted at `/` — the compiled SPA owns the root URL (see
        server.mount_spa). Keeping the welcome JSON here means `fantabot-app up` opens the
        browser on the app, not on this payload."""
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
    def swagger_ui() -> HTMLResponse:
        """Swagger UI - open access for local development"""
        return get_swagger_ui_html(
            openapi_url="/openapi.json",
            title=f"{app.title} - API Documentation",
            swagger_js_url="https://unpkg.com/swagger-ui-dist@5.11.0/swagger-ui-bundle.js",
            swagger_css_url="https://unpkg.com/swagger-ui-dist@5.11.0/swagger-ui.css",
        )

    @app.get("/redoc", include_in_schema=False)
    def redoc_ui() -> HTMLResponse:
        """ReDoc UI - open access for local development"""
        return get_redoc_html(
            openapi_url="/openapi.json",
            title=f"{app.title} - API Documentation",
            redoc_js_url="https://unpkg.com/redoc@2.1.0/bundles/redoc.standalone.js",
        )

    @app.get("/openapi.json", include_in_schema=False)
    def get_openapi_json() -> dict[str, Any]:
        """OpenAPI JSON schema.

        Delegate to ``app.openapi()`` so the served schema reflects ALL metadata
        set on the app (summary/description/contact/license_info).
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
