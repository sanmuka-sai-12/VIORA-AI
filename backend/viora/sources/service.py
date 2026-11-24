from uuid import UUID

from ..contracts import ResourceRef
from ..security import Access, AccessError
from .ports import Sources
from .schemas import SourceSummary


def read_source(
    access: Access,
    sources: Sources,
    token: str | None,
    tenant_id: UUID,
    source_id: UUID,
) -> SourceSummary:
    resource = ResourceRef(tenant_id=tenant_id, kind="source", resource_id=source_id)
    context = access.require(token, "source.read", resource)
    result = sources.find(context.tenant_id, source_id)
    if result is None:
        raise AccessError(404, "source_missing", "Source unavailable")
    return result
