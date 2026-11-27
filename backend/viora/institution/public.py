from uuid import UUID

from fastapi import APIRouter
from sqlalchemy.orm import Session

from ..identity.public import IdentityDirectory, InstitutionStatusReader
from ..security import TransactionalAccess
from .discovery import discover_institutions
from .ports import InstitutionDirectory
from .router import build_router
from .schemas import (
    AcademicContext,
    InstitutionDiscovery,
    InstitutionInfo,
    OfferingActorAccess,
    OfferingAssignment,
)

__all__ = [
    "AcademicContext",
    "InstitutionDirectory",
    "InstitutionDiscovery",
    "InstitutionInfo",
    "OfferingActorAccess",
    "OfferingAssignment",
    "discover_institutions",
    "institution_directory",
    "institution_status",
    "offering_actor_access",
    "register_models",
    "router",
]


def institution_directory(
    session: Session, identities: IdentityDirectory
) -> InstitutionDirectory:
    from .repository import SqlInstitutionDirectory

    return SqlInstitutionDirectory(session, identities)


def institution_status(session: Session) -> InstitutionStatusReader:
    from .repository import SqlInstitutionStatus

    return SqlInstitutionStatus(session)


def offering_actor_access(
    session: Session, tenant_id: UUID, user_id: UUID, offering_id: UUID
) -> OfferingActorAccess:
    """Inspect active offering chain, teacher assignment, and student enrolment for one actor."""
    from ..identity.public import InstitutionRole, identity_directory
    from .repository import SqlInstitutionDirectory

    identities = identity_directory(session, institution_status(session))
    directory = SqlInstitutionDirectory(session, identities)
    active, has_teacher, has_student = directory.offering_access_state(
        tenant_id, user_id, offering_id, need_teacher=True, need_student=True
    )
    if not active:
        return OfferingActorAccess(
            tenant_id=tenant_id,
            offering_id=offering_id,
            user_id=user_id,
            academic_active=False,
            is_assigned_teacher=False,
            is_enrolled_student=False,
        )
    roles = (
        identities.applicable_roles(user_id, tenant_id)
        if (has_teacher or has_student)
        else frozenset()
    )
    is_teacher = has_teacher and InstitutionRole.subject_teacher in roles
    is_student = has_student and InstitutionRole.student in roles
    return OfferingActorAccess(
        tenant_id=tenant_id,
        offering_id=offering_id,
        user_id=user_id,
        academic_active=True,
        is_assigned_teacher=is_teacher,
        is_enrolled_student=is_student,
    )


def register_models() -> None:
    """Load declarative mappings into the shared Alembic metadata."""
    from . import models  # noqa: F401


def router(access: TransactionalAccess) -> APIRouter:
    return build_router(access)
