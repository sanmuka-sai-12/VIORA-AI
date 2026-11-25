from fastapi import APIRouter
from sqlalchemy.orm import Session

from ..identity.public import IdentityDirectory, InstitutionStatusReader
from ..security import TransactionalAccess
from .ports import InstitutionDirectory
from .router import build_router
from .schemas import AcademicContext, InstitutionInfo, OfferingAssignment

__all__ = [
    "AcademicContext",
    "InstitutionDirectory",
    "InstitutionInfo",
    "OfferingAssignment",
    "institution_directory",
    "institution_status",
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


def register_models() -> None:
    """Load declarative mappings into the shared Alembic metadata."""
    from . import models  # noqa: F401


def router(access: TransactionalAccess) -> APIRouter:
    return build_router(access)
