"""Authentication-only persistence. These interfaces belong inside the identity domain."""

from typing import Protocol
from uuid import UUID

from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import PasswordCredential, User, VerifiedIdentityAttribute
from .schemas import AccountStatus, IdentityAttributeKind


class CredentialReader(Protocol):
    def verified_user_id(
        self, kind: IdentityAttributeKind, normalized_value: str
    ) -> UUID | None: ...

    def password_verifier(self, user_id: UUID) -> SecretStr | None: ...


class SqlCredentialReader:
    def __init__(self, session: Session) -> None:
        self.session = session

    def verified_user_id(
        self, kind: IdentityAttributeKind, normalized_value: str
    ) -> UUID | None:
        return self.session.scalar(
            select(VerifiedIdentityAttribute.user_id)
            .join(User, User.user_id == VerifiedIdentityAttribute.user_id)
            .where(
                User.status == AccountStatus.active,
                VerifiedIdentityAttribute.kind == kind.value,
                VerifiedIdentityAttribute.normalized_value == normalized_value,
            )
        )

    def password_verifier(self, user_id: UUID) -> SecretStr | None:
        encoded = self.session.scalar(
            select(PasswordCredential.password_hash)
            .join(User, User.user_id == PasswordCredential.user_id)
            .where(
                User.status == AccountStatus.active,
                PasswordCredential.user_id == user_id,
            )
        )
        return SecretStr(encoded) if encoded is not None else None
