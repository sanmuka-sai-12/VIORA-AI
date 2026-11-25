from typing import Protocol
from uuid import UUID

from ..contracts import Actor, Page, TenantContext
from .schemas import (
    InstitutionRole,
    MembershipStatus,
    MembershipSummary,
    MemberSummary,
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

    def unrevoked_roles(
        self, user_id: UUID, tenant_id: UUID
    ) -> frozenset[InstitutionRole]: ...


class MembershipManagementRepository(Protocol):
    def list_members(
        self,
        tenant_id: UUID,
        *,
        status: MembershipStatus | None,
        cursor: UUID | None,
        limit: int,
    ) -> Page[MemberSummary]: ...

    def get_member(self, tenant_id: UUID, user_id: UUID) -> MemberSummary | None: ...

    def add_member(
        self, tenant_id: UUID, user_id: UUID, status: MembershipStatus
    ) -> MemberSummary: ...

    def set_membership_status(
        self, tenant_id: UUID, user_id: UUID, status: MembershipStatus
    ) -> MemberSummary | None: ...

    def grant_role(
        self, tenant_id: UUID, user_id: UUID, role: InstitutionRole
    ) -> MemberSummary | None: ...

    def revoke_role(
        self, tenant_id: UUID, user_id: UUID, role: InstitutionRole
    ) -> MemberSummary | None: ...
