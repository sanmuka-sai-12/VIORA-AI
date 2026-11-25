from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .authorization.public import AuthorizationService
from .authorization.public import register_models as register_authorization
from .config import Settings
from .contracts import ErrorResponse
from .db import Database
from .health import health_router
from .http import Correlation, configure_errors
from .identity.public import auth_router, build_auth_service, membership_router
from .identity.public import register_models as register_identity
from .institution.public import register_models as register_institution
from .institution.public import router as institution_router
from .security import Access
from .sources import public as sources


def create_app(
    settings: Settings | None = None,
    database: Database | None = None,
    access: Access | None = None,
) -> FastAPI:
    register_authorization()
    register_institution()
    register_identity()
    config = settings or Settings.load()
    db = database or Database(config)
    if config.environment == "production" and config.auth is None:
        raise ValueError("AUTH_SIGNING_KEY is required in production")
    auth_service = build_auth_service(db, config) if config.auth is not None else None
    effective_access = access or AuthorizationService(db, auth_service)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        try:
            if auth_service is not None and isinstance(
                effective_access, AuthorizationService
            ):
                effective_access.validate_database()
            yield
        finally:
            db.close()

    app = FastAPI(title="VIORA-AI", version="1.0.0", lifespan=lifespan)
    configure_errors(app)
    app.add_middleware(Correlation)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(config.cors_origins),
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH"],
        allow_headers=["Authorization", "Content-Type", "X-Request-ID", "X-Viora-CSRF"],
        expose_headers=["X-Request-ID"],
    )
    api = APIRouter(
        prefix="/api/v1",
        responses={
            code: {"model": ErrorResponse}
            for code in (401, 403, 404, 409, 422, 500, 503)
        },
    )

    api.include_router(health_router(db))
    api.include_router(auth_router(config, db, auth_service))
    if isinstance(effective_access, AuthorizationService):
        api.include_router(membership_router(effective_access))
        api.include_router(institution_router(effective_access))
    api.include_router(sources.router(db, effective_access))
    app.include_router(api)
    return app
