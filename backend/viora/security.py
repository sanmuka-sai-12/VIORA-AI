from typing import Protocol
from uuid import UUID

from .contracts import Actor, ResourceRef, TenantContext


class Authentication(Protocol):
    def verify(self, bearer: str) -> Actor | None: ...


class Membership(Protocol):
    def active(self, actor: Actor, tenant_id: UUID) -> TenantContext | None: ...


class Authorization(Protocol):
    def permits(
        self, context: TenantContext, action: str, resource: ResourceRef
    ) -> bool:
        """Require a permitted role and a current assignment to the resource."""
        ...


class Audit(Protocol):
    def record(
        self, context: TenantContext, action: str, resource: ResourceRef, allowed: bool
    ) -> None: ...


class ChangeAudit(Audit, Protocol):
    def change(
        self, context: TenantContext, action: str, resource: ResourceRef
    ) -> None:
        """Append a security change in the same transaction as the mutation."""
        ...


class WorkerAuthorization(Protocol):
    def reauthorize(self, job_id: UUID, action: str, resource: ResourceRef) -> bool:
        """Reload the initiating actor, membership and assignment before each protected effect."""
        ...


class AccessError(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code


class Access:
    def __init__(
        self,
        auth: Authentication | None = None,
        membership: Membership | None = None,
        policy: Authorization | None = None,
    ) -> None:
        self.auth = auth
        self.membership = membership
        self.policy = policy

    def require(
        self, token: str | None, action: str, resource: ResourceRef
    ) -> TenantContext:
        actor = self.auth.verify(token) if token and self.auth else None
        if actor is None:
            raise AccessError(
                401, "unauthenticated", "Valid bearer credentials required"
            )
        context = (
            self.membership.active(actor, resource.tenant_id)
            if self.membership
            else None
        )
        if (
            context is None
            or context.actor_id != actor.actor_id
            or context.tenant_id != resource.tenant_id
            or not context.roles
            or self.policy is None
            or not self.policy.permits(context, action, resource)
        ):
            raise AccessError(403, "forbidden", "Access denied")
        return context
