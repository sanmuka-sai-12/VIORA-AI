from uuid import UUID

from sqlalchemy import and_, select, update
from sqlalchemy.orm import Session

from ..contracts import Actor, Page, TenantContext
from ..security import AccessError
from .models import MembershipRecord, RoleGrant, User, VerifiedIdentityAttribute
from .ports import InstitutionStatusReader
from .schemas import (
    AccountStatus,
    InstitutionRole,
    MembershipStatus,
    MembershipSummary,
    MemberSummary,
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

    def unrevoked_roles(
        self, user_id: UUID, tenant_id: UUID
    ) -> frozenset[InstitutionRole]:
        rows = self.session.scalars(
            select(RoleGrant.role).where(
                RoleGrant.tenant_id == tenant_id,
                RoleGrant.user_id == user_id,
                RoleGrant.revoked_at.is_(None),
            )
        ).all()
        return frozenset(rows)


class SqlMembershipRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def _member_summary(self, tenant_id: UUID, user_id: UUID) -> MemberSummary | None:
        row = self.session.execute(
            select(
                MembershipRecord.tenant_id,
                MembershipRecord.user_id,
                User.display_name,
                User.status.label("account_status"),
                MembershipRecord.status,
            )
            .join(User, User.user_id == MembershipRecord.user_id)
            .where(
                MembershipRecord.tenant_id == tenant_id,
                MembershipRecord.user_id == user_id,
            )
            .execution_options(populate_existing=True)
        ).one_or_none()
        if row is None:
            return None
        roles = tuple(
            sorted(
                self.session.scalars(
                    select(RoleGrant.role).where(
                        RoleGrant.tenant_id == tenant_id,
                        RoleGrant.user_id == user_id,
                        RoleGrant.revoked_at.is_(None),
                    )
                ).all(),
                key=lambda r: r.value,
            )
        )
        return MemberSummary(
            tenant_id=row.tenant_id,
            user_id=row.user_id,
            display_name=row.display_name,
            account_status=row.account_status,
            status=row.status,
            roles=roles,
        )

    def _lock_owner_grants(self, tenant_id: UUID) -> list[UUID]:
        """Serialize all owner-affecting mutations in deterministic user order."""
        return list(
            self.session.scalars(
                select(RoleGrant.user_id)
                .where(
                    RoleGrant.tenant_id == tenant_id,
                    RoleGrant.role == InstitutionRole.institution_owner,
                    RoleGrant.revoked_at.is_(None),
                )
                .order_by(RoleGrant.user_id)
                .with_for_update()
            ).all()
        )

    def lock_member(self, tenant_id: UUID, user_id: UUID) -> bool:
        """Lock the mutation target before the caller refreshes authorization."""
        return (
            self.session.scalar(
                select(MembershipRecord.user_id)
                .where(
                    MembershipRecord.tenant_id == tenant_id,
                    MembershipRecord.user_id == user_id,
                )
                .with_for_update()
            )
            is not None
        )

    def _count_other_active_owners(self, tenant_id: UUID, exclude_user_id: UUID) -> int:
        return len(
            self.session.scalars(
                select(RoleGrant.user_id)
                .join(
                    MembershipRecord,
                    and_(
                        MembershipRecord.tenant_id == RoleGrant.tenant_id,
                        MembershipRecord.user_id == RoleGrant.user_id,
                    ),
                )
                .join(User, User.user_id == MembershipRecord.user_id)
                .where(
                    RoleGrant.tenant_id == tenant_id,
                    RoleGrant.role == InstitutionRole.institution_owner,
                    RoleGrant.revoked_at.is_(None),
                    RoleGrant.user_id != exclude_user_id,
                    MembershipRecord.status == MembershipStatus.active,
                    User.status == AccountStatus.active,
                )
            ).all()
        )

    def list_members(
        self,
        tenant_id: UUID,
        *,
        status: MembershipStatus | None,
        cursor: UUID | None,
        limit: int,
    ) -> Page[MemberSummary]:
        stmt = (
            select(
                MembershipRecord.tenant_id,
                MembershipRecord.user_id,
                User.display_name,
                User.status.label("account_status"),
                MembershipRecord.status,
            )
            .join(User, User.user_id == MembershipRecord.user_id)
            .where(MembershipRecord.tenant_id == tenant_id)
        )
        if status is not None:
            stmt = stmt.where(MembershipRecord.status == status)
        if cursor is not None:
            stmt = stmt.where(MembershipRecord.user_id > cursor)
        rows = list(
            self.session.execute(
                stmt.order_by(MembershipRecord.user_id).limit(limit + 1)
            ).all()
        )
        has_more = len(rows) > limit
        page_rows = rows[:limit]
        user_ids = [r.user_id for r in page_rows]
        roles_by_user: dict[UUID, list[InstitutionRole]] = {uid: [] for uid in user_ids}
        if user_ids:
            for uid, role in self.session.execute(
                select(RoleGrant.user_id, RoleGrant.role).where(
                    RoleGrant.tenant_id == tenant_id,
                    RoleGrant.user_id.in_(user_ids),
                    RoleGrant.revoked_at.is_(None),
                )
            ).all():
                roles_by_user[uid].append(role)
        items = tuple(
            MemberSummary(
                tenant_id=r.tenant_id,
                user_id=r.user_id,
                display_name=r.display_name,
                account_status=r.account_status,
                status=r.status,
                roles=tuple(sorted(roles_by_user[r.user_id], key=lambda x: x.value)),
            )
            for r in page_rows
        )
        next_cursor = str(page_rows[-1].user_id) if has_more and page_rows else None
        return Page[MemberSummary](items=items, next_cursor=next_cursor)

    def get_member(self, tenant_id: UUID, user_id: UUID) -> MemberSummary | None:
        return self._member_summary(tenant_id, user_id)

    def add_member(
        self, tenant_id: UUID, user_id: UUID, status: MembershipStatus
    ) -> MemberSummary:
        verified = self.session.scalar(
            select(User.user_id)
            .join(
                VerifiedIdentityAttribute,
                VerifiedIdentityAttribute.user_id == User.user_id,
            )
            .where(User.user_id == user_id, User.status == AccountStatus.active)
            .limit(1)
        )
        if verified is None:
            raise AccessError(
                409,
                "user_ineligible",
                "User must be an active verified account before institutional association",
            )
        existing = self.session.scalar(
            select(MembershipRecord)
            .where(
                MembershipRecord.tenant_id == tenant_id,
                MembershipRecord.user_id == user_id,
            )
            .with_for_update()
        )
        if existing is not None:
            raise AccessError(
                409, "duplicate_record", "User already has a membership record"
            )
        self.session.add(
            MembershipRecord(tenant_id=tenant_id, user_id=user_id, status=status)
        )
        self.session.flush()
        summary = self._member_summary(tenant_id, user_id)
        assert summary is not None
        return summary

    def set_membership_status(
        self, tenant_id: UUID, user_id: UUID, status: MembershipStatus
    ) -> MemberSummary | None:
        if status != MembershipStatus.active:
            owner_ids = self._lock_owner_grants(tenant_id)
            if (
                user_id in owner_ids
                and self._count_other_active_owners(tenant_id, user_id) == 0
            ):
                raise AccessError(
                    409,
                    "last_owner_protected",
                    "Cannot suspend or revoke the last active institution owner",
                )
        record = self.session.scalar(
            select(MembershipRecord)
            .where(
                MembershipRecord.tenant_id == tenant_id,
                MembershipRecord.user_id == user_id,
            )
            .with_for_update()
        )
        if record is None:
            return None
        if status == MembershipStatus.active:
            active_user = self.session.scalar(
                select(User.user_id).where(
                    User.user_id == user_id, User.status == AccountStatus.active
                )
            )
            if active_user is None:
                raise AccessError(
                    409,
                    "user_ineligible",
                    "Cannot activate membership for inactive user",
                )
        record.status = status
        if status != MembershipStatus.active:
            from datetime import UTC, datetime

            self.session.execute(
                update(RoleGrant)
                .where(
                    RoleGrant.tenant_id == tenant_id,
                    RoleGrant.user_id == user_id,
                    RoleGrant.revoked_at.is_(None),
                )
                .values(revoked_at=datetime.now(UTC))
            )
        self.session.flush()
        return self._member_summary(tenant_id, user_id)

    def grant_role(
        self, tenant_id: UUID, user_id: UUID, role: InstitutionRole
    ) -> MemberSummary | None:
        if role == InstitutionRole.institution_owner:
            self._lock_owner_grants(tenant_id)
        record = self.session.scalar(
            select(MembershipRecord)
            .where(
                MembershipRecord.tenant_id == tenant_id,
                MembershipRecord.user_id == user_id,
            )
            .with_for_update()
        )
        if record is None:
            return None
        if record.status != MembershipStatus.active:
            raise AccessError(
                409, "inactive_membership", "Membership must be active to grant roles"
            )
        existing = self.session.scalar(
            select(RoleGrant)
            .where(
                RoleGrant.tenant_id == tenant_id,
                RoleGrant.user_id == user_id,
                RoleGrant.role == role,
            )
            .with_for_update()
        )
        if existing is not None:
            if existing.revoked_at is None:
                raise AccessError(
                    409, "duplicate_record", "Role is already assigned to this member"
                )
            existing.revoked_at = None
        else:
            self.session.add(RoleGrant(tenant_id=tenant_id, user_id=user_id, role=role))
        self.session.flush()
        return self._member_summary(tenant_id, user_id)

    def revoke_role(
        self, tenant_id: UUID, user_id: UUID, role: InstitutionRole
    ) -> MemberSummary | None:
        if role == InstitutionRole.institution_owner:
            owner_ids = self._lock_owner_grants(tenant_id)
            if (
                user_id in owner_ids
                and self._count_other_active_owners(tenant_id, user_id) == 0
            ):
                raise AccessError(
                    409,
                    "last_owner_protected",
                    "Cannot revoke the last active institution owner",
                )
        existing = self.session.scalar(
            select(RoleGrant)
            .where(
                RoleGrant.tenant_id == tenant_id,
                RoleGrant.user_id == user_id,
                RoleGrant.role == role,
            )
            .with_for_update()
        )
        if existing is None or existing.revoked_at is not None:
            return None
        from datetime import UTC, datetime

        existing.revoked_at = datetime.now(UTC)
        self.session.flush()
        return self._member_summary(tenant_id, user_id)
