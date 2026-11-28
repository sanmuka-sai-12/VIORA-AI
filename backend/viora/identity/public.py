from collections.abc import Sequence
from uuid import UUID

from fastapi import APIRouter
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import AuthSettings, Settings
from ..db import Database
from ..security import TransactionalAccess as _TransactionalAccess
from .auth import AuthFailure, AuthService, TokenPair
from .discovery import discover_memberships
from .membership_router import build_membership_router
from .models import MembershipRecord, RoleGrant, User, VerifiedIdentityAttribute
from .ports import IdentityDirectory, InstitutionStatusReader
from .provision import provision_account
from .repository import SqlIdentityDirectory as _SqlIdentityDirectory
from .router import AuthenticatedUser, TokenResponse, build_auth_router
from .schemas import (
    AccountStatus,
    DiscoveredMembership,
    IdentityAttributeKind,
    InstitutionRole,
    MemberOnboardCreate,
    MembershipStatus,
    MembershipSummary,
    MemberSummary,
    UserSummary,
)

__all__ = [
    "AccountStatus",
    "AuthFailure",
    "AuthService",
    "AuthSettings",
    "AuthenticatedUser",
    "DiscoveredMembership",
    "IdentityAttributeKind",
    "IdentityDirectory",
    "InstitutionRole",
    "InstitutionStatusReader",
    "MemberOnboardCreate",
    "MembershipStatus",
    "MembershipSummary",
    "TokenPair",
    "TokenResponse",
    "UserSummary",
    "auth_router",
    "build_auth_service",
    "discover_memberships",
    "get_tenant_member",
    "grant_initial_owner_membership",
    "identity_directory",
    "is_verified_active_user",
    "list_tenant_members",
    "membership_router",
    "provision_account",
    "register_models",
    "update_user_display_name",
    "user_display_names",
    "user_emails",
]


def membership_router(access: _TransactionalAccess) -> APIRouter:
    return build_membership_router(access)


def build_auth_service(db: Database, settings: Settings) -> AuthService:
    """Compose one AuthService per application, rejecting missing auth settings."""
    if settings.auth is None:
        raise ValueError("AUTH_SIGNING_KEY is required to compose AuthService")
    return AuthService(db.sessions, settings.auth)


def auth_router(
    config: Settings, db: Database, auth_service: AuthService | None
) -> APIRouter:
    return build_auth_router(config, db, auth_service)


def register_models() -> None:
    """Load identity-owned metadata for migration composition."""
    from . import models  # noqa: F401


def identity_directory(
    session: Session, institutions: InstitutionStatusReader
) -> IdentityDirectory:
    """Bind the read-only directory to the caller's transaction."""
    return _SqlIdentityDirectory(session, institutions)


def is_verified_active_user(session: Session, user_id: UUID) -> bool:
    """Return True only when user exists, is active, and has a verified identifier."""
    found = session.scalar(
        select(User.user_id)
        .join(
            VerifiedIdentityAttribute,
            VerifiedIdentityAttribute.user_id == User.user_id,
        )
        .where(User.user_id == user_id, User.status == AccountStatus.active)
        .limit(1)
    )
    return found is not None


def user_emails(session: Session, user_ids: Sequence[UUID]) -> dict[UUID, str]:
    """Return verified email addresses keyed by user_id for directory/profile views."""
    if not user_ids:
        return {}
    rows = session.execute(
        select(
            VerifiedIdentityAttribute.user_id,
            VerifiedIdentityAttribute.normalized_value,
        ).where(
            VerifiedIdentityAttribute.user_id.in_(user_ids),
            VerifiedIdentityAttribute.kind == IdentityAttributeKind.email.value,
        )
    ).all()
    return {row.user_id: row.normalized_value for row in rows}


def user_display_names(session: Session, user_ids: Sequence[UUID]) -> dict[UUID, str]:
    """Return display names keyed by user_id."""
    if not user_ids:
        return {}
    rows = session.execute(
        select(User.user_id, User.display_name).where(User.user_id.in_(user_ids))
    ).all()
    return {row.user_id: row.display_name for row in rows}


def update_user_display_name(
    session: Session, user_id: UUID, display_name: str
) -> str | None:
    """Update a user's display name within an authorized transaction."""
    cleaned = display_name.strip()
    if not cleaned:
        raise ValueError("Display name must not be empty")
    user = session.scalar(select(User).where(User.user_id == user_id).with_for_update())
    if user is None:
        return None
    user.display_name = cleaned
    session.flush()
    return user.display_name


def get_tenant_member(
    session: Session, tenant_id: UUID, user_id: UUID
) -> "MemberSummary | None":
    """Return membership and role summary for a single tenant member."""
    from .repository import SqlMembershipRepository

    return SqlMembershipRepository(session).get_member(tenant_id, user_id)


def list_tenant_members(
    session: Session,
    tenant_id: UUID,
    *,
    status: MembershipStatus | None = None,
) -> list["MemberSummary"]:
    """Collect all members for a tenant for directory/profile composition."""
    from .repository import SqlMembershipRepository

    repo = SqlMembershipRepository(session)
    collected: list[MemberSummary] = []
    cursor: UUID | None = None
    while True:
        page = repo.list_members(tenant_id, status=status, cursor=cursor, limit=100)
        collected.extend(page.items)
        if page.next_cursor is None:
            break
        cursor = UUID(page.next_cursor)
    return collected


def grant_initial_owner_membership(
    session: Session, tenant_id: UUID, user_id: UUID
) -> None:
    """Create initial active membership and institution_owner role inside caller transaction."""
    session.add(
        MembershipRecord(
            tenant_id=tenant_id,
            user_id=user_id,
            status=MembershipStatus.active,
        )
    )
    session.flush()
    session.add(
        RoleGrant(
            tenant_id=tenant_id,
            user_id=user_id,
            role=InstitutionRole.institution_owner,
        )
    )
    session.flush()
