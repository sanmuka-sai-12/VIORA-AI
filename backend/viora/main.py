from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import Settings
from .contracts import ErrorResponse
from .db import Database
from .health import health_router
from .http import Correlation, configure_errors
from .security import Access
from .sources import public as sources


def create_app(
    settings: Settings | None = None,
    database: Database | None = None,
    access: Access | None = None,
) -> FastAPI:
    config = settings or Settings.load()
    db = database or Database(config)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        try:
            yield
        finally:
            db.close()

    app = FastAPI(title="VIORA-AI", version="1.0.0", lifespan=lifespan)
    configure_errors(app)
    app.add_middleware(Correlation)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(config.cors_origins),
        allow_methods=["GET"],
        allow_headers=["Authorization", "X-Request-ID"],
        expose_headers=["X-Request-ID"],
    )
    api = APIRouter(
        prefix="/api/v1",
        responses={
            code: {"model": ErrorResponse} for code in (401, 403, 404, 422, 500, 503)
        },
    )

    api.include_router(health_router(db))
    api.include_router(sources.router(db, access or Access()))
    app.include_router(api)
    return app
