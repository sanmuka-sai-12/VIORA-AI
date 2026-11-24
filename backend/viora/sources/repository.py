from uuid import UUID

from sqlalchemy.orm import Session

from .models import Source
from .schemas import SourceSummary


class SourceRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def find(self, tenant_id: UUID, source_id: UUID) -> SourceSummary | None:
        row = self.session.get(Source, (tenant_id, source_id))
        return SourceSummary(source_id=row.source_id, title=row.title) if row else None
