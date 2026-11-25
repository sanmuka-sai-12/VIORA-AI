from fastapi import APIRouter
from sqlalchemy.orm import Session

from ..config import AuthSettings, Settings
from ..db import Database
from .auth import AuthFailure, AuthService, TokenPair
from .ports import IdentityDirectory, InstitutionStatusReader
from .provision import provision_account
from .repository import SqlIdentityDirectory as _SqlIdentityDirectory
from .router import AuthenticatedUser, TokenResponse, build_auth_router
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
    "AuthFailure",
    "AuthService",
    "AuthSettings",
    "AuthenticatedUser",
    "IdentityAttributeKind",
    "IdentityDirectory",
    "InstitutionRole",
    "InstitutionStatusReader",
    "MembershipStatus",
    "MembershipSummary",
    "TokenPair",
    "TokenResponse",
    "UserSummary",
    "auth_router",
    "build_auth_service",
    "identity_directory",
    "provision_account",
    "register_models",
]


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
