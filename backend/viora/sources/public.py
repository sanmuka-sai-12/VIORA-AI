from collections.abc import Iterator
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from ..authorization.public import AuthorizationService
from ..contracts import ResourceRef
from ..db import Database
from ..security import Access, AccessError
from . import models
from .repository import SourceRepository
from .router import build_router
from .schemas import SourceSummary

__all__ = ["SourceSummary", "registered_table", "router"]

registered_table = models.Source.__table__


def router(db: Database, access: Access | AuthorizationService) -> APIRouter:
    if isinstance(access, AuthorizationService):
        protected = APIRouter(tags=["sources"])

        @protected.get(
            "/tenants/{tenant_id}/sources/{source_id}", operation_id="readSource"
        )
        def get_source(
            tenant_id: UUID,
            source_id: UUID,
            bearer: Annotated[
                HTTPAuthorizationCredentials | None,
                Security(HTTPBearer(auto_error=False)),
            ],
        ) -> SourceSummary:
            resource = ResourceRef(
                tenant_id=tenant_id, kind="source", resource_id=source_id
            )
            with access.transaction(
                bearer.credentials if bearer else None, "source.read", resource
            ) as scope:
                result = SourceRepository(scope.session).find(tenant_id, source_id)
                if result is None:
                    raise AccessError(404, "source_missing", "Source unavailable")
                return result

        return protected

    def repository() -> Iterator[SourceRepository]:
        with db.sessions.begin() as session:
            yield SourceRepository(session)

    return build_router(access, repository)
