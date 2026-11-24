from uuid import UUID

from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from ..contracts import Actor, TenantContext
from .models import MembershipRecord, RoleGrant, User
from .ports import InstitutionStatusReader
from .schemas import (
    AccountStatus,
    InstitutionRole,
    MembershipStatus,
    MembershipSummary,
    UserSummary,
)


class SqlIdentityDirectory:
    def __init__(self, session: Session, institutions: InstitutionStatusReader) -> None:
        self.session = session
        self.institutions = institutions

    def user(self, user_id: UUID) -> UserSummary | None:
        row = self.session.execute(
            select(User.user_id, User.display_name, User.status).where(
                User.user_id == user_id
            )
        ).one_or_none()
        return (
            UserSummary(
                user_id=row.user_id, display_name=row.display_name, status=row.status
            )
            if row
            else None
        )

    def membership(self, user_id: UUID, tenant_id: UUID) -> MembershipSummary | None:
        status = self.session.scalar(
            select(MembershipRecord.status).where(
                MembershipRecord.user_id == user_id,
                MembershipRecord.tenant_id == tenant_id,
            )
        )
        return (
            MembershipSummary(user_id=user_id, tenant_id=tenant_id, status=status)
            if status is not None
            else None
        )

    def _roles(
        self, user_id: UUID, tenant_id: UUID
    ) -> frozenset[InstitutionRole] | None:
        """A left join distinguishes an active roleless membership from an inactive membership."""
        if not self.institutions.is_active(tenant_id):
            return None
        rows = self.session.scalars(
            select(RoleGrant.role)
            .select_from(MembershipRecord)
            .join(User, User.user_id == MembershipRecord.user_id)
            .outerjoin(
                RoleGrant,
                and_(
                    RoleGrant.tenant_id == MembershipRecord.tenant_id,
                    RoleGrant.user_id == MembershipRecord.user_id,
                    RoleGrant.revoked_at.is_(None),
                ),
            )
            .where(
                MembershipRecord.tenant_id == tenant_id,
                MembershipRecord.user_id == user_id,
                MembershipRecord.status == MembershipStatus.active,
                User.status == AccountStatus.active,
            )
        ).all()
        return frozenset(role for role in rows if role is not None) if rows else None

    def active(self, actor: Actor, tenant_id: UUID) -> TenantContext | None:
        roles = self._roles(actor.actor_id, tenant_id)
        if roles is None:
            return None
        return TenantContext(
            actor_id=actor.actor_id,
            tenant_id=tenant_id,
            roles=frozenset(role.value for role in roles),
        )

    def applicable_roles(
        self, user_id: UUID, tenant_id: UUID
    ) -> frozenset[InstitutionRole]:
        return self._roles(user_id, tenant_id) or frozenset()
