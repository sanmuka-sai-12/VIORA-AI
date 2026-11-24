from typing import Literal

from fastapi import APIRouter

from .db import Database
from .security import AccessError


def health_router(db: Database) -> APIRouter:
    api = APIRouter(tags=["health"])

    @api.get("/health/live", operation_id="liveness")
    def live() -> dict[str, Literal["live"]]:
        return {"status": "live"}

    @api.get("/health/ready", operation_id="readiness")
    def ready() -> dict[str, Literal["ready"]]:
        if not db.ready():
            raise AccessError(503, "database_unavailable", "Database unavailable")
        return {"status": "ready"}

    return api
