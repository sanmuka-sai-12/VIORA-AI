from sqlalchemy.orm import Session

from .ports import IdentityDirectory, InstitutionStatusReader
from .repository import SqlIdentityDirectory as _SqlIdentityDirectory
from .schemas import (
    AccountStatus,
    IdentityAttributeKind,
    InstitutionRole,
    MembershipStatus,
    MembershipSummary,
    UserSummary,
)

__all__ = [
    "AccountStatus",
    "IdentityAttributeKind",
    "IdentityDirectory",
    "InstitutionRole",
    "InstitutionStatusReader",
    "MembershipStatus",
    "MembershipSummary",
    "UserSummary",
    "identity_directory",
    "register_models",
]


def register_models() -> None:
    """Load identity-owned metadata for migration composition."""
    from . import models  # noqa: F401


def identity_directory(
    session: Session, institutions: InstitutionStatusReader
) -> IdentityDirectory:
    """Bind the read-only directory to the caller's transaction."""
    return _SqlIdentityDirectory(session, institutions)
