from collections.abc import Iterator

from fastapi import APIRouter

from ..db import Database
from ..security import Access
from . import models
from .repository import SourceRepository
from .router import build_router
from .schemas import SourceSummary

__all__ = ["SourceSummary", "registered_table", "router"]

registered_table = models.Source.__table__


def router(db: Database, access: Access) -> APIRouter:
    def repository() -> Iterator[SourceRepository]:
        with db.sessions.begin() as session:
            yield SourceRepository(session)

    return build_router(access, repository)
