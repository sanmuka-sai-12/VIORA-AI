from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy.orm import Session

from ..contracts import ResourceRef, TenantContext
from .models import SecurityEvent


class SqlAudit:
    def __init__(self, session: Session) -> None:
        self.session = session

    def record(
        self, context: TenantContext, action: str, resource: ResourceRef, allowed: bool
    ) -> None:
        self._append(context, action, resource, allowed, "decision")

    def change(
        self, context: TenantContext, action: str, resource: ResourceRef
    ) -> None:
        """Call after a security mutation in the same transaction; rollback removes both."""
        self._append(context, action, resource, True, "change")

    def _append(
        self,
        context: TenantContext,
        action: str,
        resource: ResourceRef,
        allowed: bool,
        kind: str,
    ) -> None:
        if context.tenant_id != resource.tenant_id:
            raise ValueError("Audit tenant mismatch")
        self.session.add(
            SecurityEvent(
                event_id=uuid4(),
                tenant_id=resource.tenant_id,
                actor_id=context.actor_id,
                resource_id=resource.resource_id,
                resource_kind=resource.kind,
                action=action,
                event_kind=kind,
                allowed=allowed,
                occurred_at=datetime.now(UTC),
            )
        )
