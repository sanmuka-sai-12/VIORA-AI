from sqlalchemy.orm import Session

from ..identity.public import IdentityDirectory, InstitutionStatusReader
from .ports import InstitutionDirectory
from .schemas import AcademicContext, InstitutionInfo, OfferingAssignment

__all__ = [
    "AcademicContext",
    "InstitutionDirectory",
    "InstitutionInfo",
    "OfferingAssignment",
    "institution_directory",
    "institution_status",
    "register_models",
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
