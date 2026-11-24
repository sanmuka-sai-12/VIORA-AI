from collections.abc import Callable
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from ..security import Access
from .ports import Sources
from .schemas import SourceSummary
from .service import read_source


def build_router(access: Access, repository: Callable[..., object]) -> APIRouter:
    router = APIRouter(tags=["sources"])

    @router.get("/tenants/{tenant_id}/sources/{source_id}", operation_id="readSource")
    def get_source(
        tenant_id: UUID,
        source_id: UUID,
        sources: Annotated[Sources, Depends(repository)],
        bearer: Annotated[
            HTTPAuthorizationCredentials | None, Security(HTTPBearer(auto_error=False))
        ],
    ) -> SourceSummary:
        return read_source(
            access,
            sources,
            bearer.credentials if bearer else None,
            tenant_id,
            source_id,
        )

    return router
