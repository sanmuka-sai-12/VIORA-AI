"""Actor-scoped membership discovery under forced RLS."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import MembershipRecord, RoleGrant, User
from .schemas import (
    AccountStatus,
    DiscoveredMembership,
    InstitutionRole,
    MembershipStatus,
)


def discover_memberships(
    session: Session, actor_id: UUID
) -> tuple[DiscoveredMembership, ...]:
    tenants = session.scalars(
        select(MembershipRecord.tenant_id)
        .join(User, User.user_id == MembershipRecord.user_id)
        .where(
            MembershipRecord.user_id == actor_id,
            MembershipRecord.status == MembershipStatus.active,
            User.status == AccountStatus.active,
        )
        .order_by(MembershipRecord.tenant_id)
    ).all()
    result: list[DiscoveredMembership] = []
    if not tenants:
        return ()
    grants = session.execute(
        select(RoleGrant.tenant_id, RoleGrant.role).where(
            RoleGrant.tenant_id.in_(tenants),
            RoleGrant.user_id == actor_id,
            RoleGrant.revoked_at.is_(None),
        )
    ).all()
    roles_by_tenant: dict[UUID, list[InstitutionRole]] = {tid: [] for tid in tenants}
    for tenant_id, role in grants:
        roles_by_tenant[tenant_id].append(role)
    for tenant_id in tenants:
        roles = tuple(
            sorted(roles_by_tenant[tenant_id], key=lambda role: role.value)
        )
        result.append(DiscoveredMembership(tenant_id=tenant_id, roles=roles))
    return tuple(result)
