from typing import Protocol
from uuid import UUID

from ..contracts import Actor, TenantContext
from .schemas import (
    InstitutionRole,
    MembershipSummary,
    UserSummary,
)


class InstitutionStatusReader(Protocol):
    def is_active(self, tenant_id: UUID) -> bool: ...


class IdentityDirectory(Protocol):
    def user(self, user_id: UUID) -> UserSummary | None: ...

    def membership(
        self, user_id: UUID, tenant_id: UUID
    ) -> MembershipSummary | None: ...

    def active(self, actor: Actor, tenant_id: UUID) -> TenantContext | None: ...

    def applicable_roles(
        self, user_id: UUID, tenant_id: UUID
    ) -> frozenset[InstitutionRole]: ...
