from uuid import UUID

from ..contracts import Contract


class SourceSummary(Contract):
    source_id: UUID
    title: str
