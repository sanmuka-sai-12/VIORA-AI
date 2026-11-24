from typing import Protocol
from uuid import UUID

from .schemas import SourceSummary


class Sources(Protocol):
    def find(self, tenant_id: UUID, source_id: UUID) -> SourceSummary | None: ...
