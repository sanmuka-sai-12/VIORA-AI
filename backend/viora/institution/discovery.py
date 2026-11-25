"""Minimal actor-scoped institution discovery."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..identity.public import DiscoveredMembership
from .models import Institution
from .schemas import InstitutionDiscovery


def discover_institutions(
    session: Session, memberships: tuple[DiscoveredMembership, ...]
) -> tuple[InstitutionDiscovery, ...]:
    roles = {membership.tenant_id: membership.roles for membership in memberships}
    if not roles:
        return ()
    rows = session.execute(
        select(Institution.tenant_id, Institution.name)
        .where(Institution.tenant_id.in_(roles), Institution.active.is_(True))
        .order_by(Institution.name, Institution.tenant_id)
    ).all()
    return tuple(
        InstitutionDiscovery(
            tenant_id=tenant_id,
            name=name,
            roles=tuple(role.value for role in roles[tenant_id]),
        )
        for tenant_id, name in rows
    )
