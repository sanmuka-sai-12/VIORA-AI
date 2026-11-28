"""Authentication algorithms and transaction-owning application service.

Never bind this service to an HTTP request's transaction. In particular, replay
revocation commits before AuthFailure is raised. PostgreSQL READ COMMITTED is
required; every mutation locks User then AuthSession in that order.
"""

import hashlib
import re
import secrets
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import UUID, uuid4

import jwt
from pwdlib import PasswordHash
from pwdlib.exceptions import HashVerificationError, UnknownHashError
from pwdlib.hashers.argon2 import Argon2Hasher
from pydantic import SecretStr
from sqlalchemy import select, text, update
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from ..config import AuthSettings
from ..contracts import Actor, Contract
from .models import (
    AccountInvitation,
    AuthSession,
    MembershipRecord,
    PasswordCredential,
    RefreshToken,
    User,
    VerifiedIdentityAttribute,
)
from .schemas import AccountStatus, IdentityAttributeKind, MembershipStatus

REFRESH_ENTROPY_BYTES = 32
MAX_ACCESS_TOKEN_LENGTH = 4096
REFRESH_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_-]{43}")
MAX_PASSWORD_LENGTH = 1024
MIN_PASSWORD_LENGTH = 12


class AuthFailure(Exception):
    """Map every authentication rejection to the same HTTP 401 response."""

    def __init__(self) -> None:
        super().__init__("Invalid authentication credentials")


class TokenPair(Contract):
    access_token: SecretStr
    refresh_token: SecretStr
    session_id: UUID
    access_expires_in: int
    refresh_expires_at: datetime
    session_expires_at: datetime


class Passwords:
    def __init__(self) -> None:
        self.hasher = PasswordHash((Argon2Hasher(),))
        self.dummy = self.hasher.hash(secrets.token_urlsafe(REFRESH_ENTROPY_BYTES))

    def hash(self, password: SecretStr) -> str:
        if (
            not MIN_PASSWORD_LENGTH
            <= len(password.get_secret_value())
            <= MAX_PASSWORD_LENGTH
        ):
            raise ValueError("Password must contain 12 to 1024 characters")
        return self.hasher.hash(password.get_secret_value())

    def verify(self, password: SecretStr, encoded: str | None) -> bool:
        value = password.get_secret_value()
        acceptable = encoded is not None and encoded.startswith("$argon2id$")
        if len(value) > MAX_PASSWORD_LENGTH:
            value, acceptable = "", False
        try:
            matched = self.hasher.verify(value, encoded if acceptable else self.dummy)
        except (HashVerificationError, UnknownHashError):
            self.hasher.verify(value, self.dummy)
            return False
        return matched and acceptable

    def rehash(self, password: SecretStr, encoded: str) -> str | None:
        """Upgrade verified legacy parameters without applying new-password policy."""
        if self.hasher.current_hasher.check_needs_rehash(encoded):
            return self.hasher.hash(password.get_secret_value())
        return None


class Tokens:
    def __init__(self, settings: AuthSettings) -> None:
        self.settings = settings

    def issue(self, actor_id: UUID, session_id: UUID) -> str:
        now = datetime.now(UTC)
        return jwt.encode(
            {
                "sub": str(actor_id),
                "sid": str(session_id),
                "iss": self.settings.issuer,
                "aud": self.settings.audience,
                "iat": now,
                "exp": now + timedelta(seconds=self.settings.access_seconds),
            },
            self.settings.signing_key.get_secret_value(),
            algorithm="HS256",
        )

    def decode(self, bearer: str) -> tuple[UUID, UUID]:
        if len(bearer) > MAX_ACCESS_TOKEN_LENGTH:
            raise AuthFailure()
        try:
            claims = cast(
                Mapping[str, object],
                jwt.decode(
                    bearer,
                    self.settings.signing_key.get_secret_value(),
                    algorithms=["HS256"],
                    issuer=self.settings.issuer,
                    audience=self.settings.audience,
                    options={
                        "require": ["sub", "sid", "iss", "aud", "iat", "exp"],
                        "strict_aud": True,
                    },
                ),
            )
            issued, expires, subject, session_id = (
                claims["iat"],
                claims["exp"],
                claims["sub"],
                claims["sid"],
            )
            if (
                type(issued) is not int
                or type(expires) is not int
                or expires <= issued
                or not isinstance(subject, str)
                or not isinstance(session_id, str)
            ):
                raise AuthFailure()
            return UUID(subject), UUID(session_id)
        except (jwt.InvalidTokenError, ValueError, TypeError, KeyError):
            raise AuthFailure() from None


def refresh_hash(token: SecretStr) -> str:
    return hashlib.sha256(token.get_secret_value().encode("utf-8")).hexdigest()


class AuthService:
    """Own short independent transactions; return tokens only after a successful commit."""

    def __init__(self, sessions: sessionmaker[Session], settings: AuthSettings) -> None:
        bind: object = sessions.kw.get("bind")
        if not isinstance(bind, Engine) or bind.dialect.name != "postgresql":
            raise ValueError(
                "Authentication requires an engine-bound PostgreSQL session factory"
            )
        # An Engine guarantees these commits cannot join an outer request transaction.
        self.sessions: sessionmaker[Session] = sessionmaker(
            bind.execution_options(isolation_level="READ COMMITTED"),
            expire_on_commit=False,
        )
        self.settings = settings
        self.tokens = Tokens(settings)
        self.passwords = Passwords()

    def _lock_user(self, db: Session, actor_id: UUID) -> User | None:
        return db.scalar(select(User).where(User.user_id == actor_id).with_for_update())

    def _lock_session(self, db: Session, session_id: UUID) -> AuthSession | None:
        return db.scalar(
            select(AuthSession)
            .where(AuthSession.session_id == session_id)
            .with_for_update()
        )

    @staticmethod
    def _valid(user: User | None, session: AuthSession | None, now: datetime) -> bool:
        return (
            user is not None
            and user.status == AccountStatus.active
            and session is not None
            and session.user_id == user.user_id
            and session.revoked_at is None
            and session.expires_at > now
            and session.refresh_expires_at > now
        )

    def _pair(self, db: Session, session: AuthSession, now: datetime) -> TokenPair:
        raw = SecretStr(secrets.token_urlsafe(REFRESH_ENTROPY_BYTES))
        session.refresh_expires_at = min(
            session.expires_at, now + timedelta(seconds=self.settings.refresh_seconds)
        )
        db.add(
            RefreshToken(
                token_hash=refresh_hash(raw),
                session_id=session.session_id,
                expires_at=session.refresh_expires_at,
            )
        )
        return TokenPair(
            access_token=SecretStr(
                self.tokens.issue(session.user_id, session.session_id)
            ),
            refresh_token=raw,
            session_id=session.session_id,
            access_expires_in=self.settings.access_seconds,
            refresh_expires_at=session.refresh_expires_at,
            session_expires_at=session.expires_at,
        )

    def login(
        self, kind: IdentityAttributeKind, identifier: str, password: SecretStr
    ) -> TokenPair:
        normalized = (
            identifier.strip().lower()
            if kind == IdentityAttributeKind.email
            else identifier.strip()
        )
        with self.sessions.begin() as db:
            actor_id = db.scalar(
                select(VerifiedIdentityAttribute.user_id).where(
                    VerifiedIdentityAttribute.kind == kind.value,
                    VerifiedIdentityAttribute.normalized_value == normalized,
                )
            )
            user = self._lock_user(db, actor_id) if actor_id else None
            credential = db.get(PasswordCredential, actor_id) if user else None
            matched = self.passwords.verify(
                password, credential.password_hash if credential else None
            )
            if not matched or user is None or user.status != AccountStatus.active:
                raise AuthFailure()
            if credential is not None:
                upgraded = self.passwords.rehash(password, credential.password_hash)
                if upgraded is not None:
                    credential.password_hash = upgraded
            now = datetime.now(UTC)
            session = AuthSession(
                session_id=uuid4(),
                user_id=user.user_id,
                created_at=now,
                expires_at=now + timedelta(seconds=self.settings.absolute_seconds),
                refresh_expires_at=now,
            )
            db.add(session)
            db.flush()
            result = self._pair(db, session, now)
        return result

    def refresh(self, refresh_token: SecretStr) -> TokenPair:
        if REFRESH_TOKEN_PATTERN.fullmatch(refresh_token.get_secret_value()) is None:
            raise AuthFailure()
        result: TokenPair | None = None
        with self.sessions.begin() as db:
            digest = refresh_hash(refresh_token)
            locator = db.execute(
                select(RefreshToken.session_id, AuthSession.user_id)
                .join(
                    AuthSession, AuthSession.session_id == RefreshToken.session_id
                )
                .where(RefreshToken.token_hash == digest)
            ).one_or_none()
            if locator is not None:
                session_id, actor_id = locator
                user = self._lock_user(db, actor_id)
                session = self._lock_session(db, session_id)
                token = db.get(RefreshToken, digest)
                now = datetime.now(UTC)
                if session is not None and token is not None:
                    if token.consumed_at is not None:
                        session.revoked_at = session.revoked_at or now
                    elif self._valid(user, session, now) and token.expires_at > now:
                        token.consumed_at = now
                        result = self._pair(db, session, now)
        # Deliberately outside the transaction: an HTTP 401 must not roll back revocation.
        if result is None:
            raise AuthFailure()
        return result

    def verify(self, bearer: str) -> Actor | None:
        """Implement security.Authentication; storage errors propagate and never authenticate."""
        try:
            actor_id, session_id = self.tokens.decode(bearer)
        except AuthFailure:
            return None
        with self.sessions() as db:
            now = datetime.now(UTC)
            found = db.scalar(
                select(User.user_id)
                .join(AuthSession, AuthSession.user_id == User.user_id)
                .where(
                    User.user_id == actor_id,
                    User.status == AccountStatus.active,
                    AuthSession.session_id == session_id,
                    AuthSession.revoked_at.is_(None),
                    AuthSession.expires_at > now,
                    AuthSession.refresh_expires_at > now,
                )
            )
            return Actor(actor_id=found) if found is not None else None

    def _authenticated(self, db: Session, bearer: str) -> User:
        actor_id, session_id = self.tokens.decode(bearer)
        user = self._lock_user(db, actor_id)
        session = self._lock_session(db, session_id)
        self.tokens.decode(bearer)
        if not self._valid(user, session, datetime.now(UTC)) or user is None:
            raise AuthFailure()
        return user

    def revoke_session(self, bearer: str, session_id: UUID) -> None:
        with self.sessions.begin() as db:
            user = self._authenticated(db, bearer)
            target = db.scalar(
                select(AuthSession)
                .where(
                    AuthSession.session_id == session_id,
                    AuthSession.user_id == user.user_id,
                )
                .with_for_update()
            )
            if target is None or target.user_id != user.user_id:
                raise AuthFailure()
            target.revoked_at = target.revoked_at or datetime.now(UTC)

    def _revoke_all(self, db: Session, actor_id: UUID) -> None:
        db.execute(
            update(AuthSession)
            .where(AuthSession.user_id == actor_id, AuthSession.revoked_at.is_(None))
            .values(revoked_at=datetime.now(UTC))
        )

    def logout_all(self, bearer: str) -> None:
        with self.sessions.begin() as db:
            user = self._authenticated(db, bearer)
            self._revoke_all(db, user.user_id)

    def change_password(
        self, bearer: str, current_password: SecretStr, new_password: SecretStr
    ) -> None:
        with self.sessions.begin() as db:
            user = self._authenticated(db, bearer)
            credential = db.get(PasswordCredential, user.user_id)
            if (
                not self.passwords.verify(
                    current_password, credential.password_hash if credential else None
                )
                or credential is None
            ):
                raise AuthFailure()
            credential.password_hash = self.passwords.hash(new_password)
            self._revoke_all(db, user.user_id)

    def accept_invite(self, token: str, new_password: SecretStr) -> UUID:
        cleaned_token = token.strip()
        if not cleaned_token:
            raise AuthFailure()
        encoded_password = self.passwords.hash(new_password)
        digest = hashlib.sha256(cleaned_token.encode("utf-8")).hexdigest()
        now = datetime.now(UTC)
        with self.sessions.begin() as db:
            invite = db.scalar(
                select(AccountInvitation)
                .where(
                    AccountInvitation.token_hash == digest,
                    AccountInvitation.accepted_at.is_(None),
                    AccountInvitation.expires_at > now,
                )
                .with_for_update()
            )
            if invite is None:
                raise AuthFailure()
            user = self._lock_user(db, invite.user_id)
            if user is None or user.status == AccountStatus.disabled:
                raise AuthFailure()
            credential = db.get(PasswordCredential, user.user_id)
            if credential is None:
                db.add(
                    PasswordCredential(
                        user_id=user.user_id, password_hash=encoded_password
                    )
                )
            else:
                credential.password_hash = encoded_password
            invite.accepted_at = now
            if invite.invited_tenant_id is not None:
                db.execute(
                    text("SELECT set_config('viora.tenant_id', :tenant, true)"),
                    {"tenant": str(invite.invited_tenant_id)},
                )
                membership = db.scalar(
                    select(MembershipRecord)
                    .where(
                        MembershipRecord.tenant_id == invite.invited_tenant_id,
                        MembershipRecord.user_id == user.user_id,
                    )
                    .with_for_update()
                )
                if (
                    membership is not None
                    and membership.status == MembershipStatus.invited
                ):
                    membership.status = MembershipStatus.active
            return user.user_id
