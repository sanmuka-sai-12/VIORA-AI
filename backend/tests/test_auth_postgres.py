"""Real PostgreSQL locking and commit tests, collected by mandatory postgres CI."""

import os
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Barrier
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from pydantic import SecretStr
from sqlalchemy import create_engine, event, func, select, update
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session
from sqlalchemy.schema import CreateSchema, DropSchema
from viora.config import AuthSettings, Settings
from viora.db import Database
from viora.identity.auth import refresh_hash
from viora.identity.models import (
    AuthSession,
    PasswordCredential,
    RefreshToken,
    User,
    VerifiedIdentityAttribute,
)
from viora.identity.public import (
    AccountStatus,
    AuthFailure,
    AuthService,
    IdentityAttributeKind,
    TokenPair,
)

pytestmark = pytest.mark.postgres
PASSWORD = SecretStr("test password for authentication")


@pytest.fixture(scope="module")
def database() -> Iterator[Database]:
    raw_url = os.environ.get("VIORA_TEST_DATABASE_URL")
    if not raw_url:
        pytest.skip("Requires dedicated PostgreSQL integration database")
    schema = f"auth_test_{uuid4().hex}"
    url = make_url(raw_url)
    scoped_url = url.update_query_dict({"options": f"-csearch_path={schema}"})
    admin = create_engine(url)
    db = Database(
        Settings(
            database_url=SecretStr(scoped_url.render_as_string(hide_password=False))
        )
    )
    with admin.begin() as connection:
        connection.execute(CreateSchema(schema))
    try:
        with pytest.MonkeyPatch.context() as patch:
            patch.setenv(
                "DATABASE_URL", scoped_url.render_as_string(hide_password=False)
            )
            command.upgrade(
                Config(str(Path(__file__).resolve().parents[2] / "alembic.ini")), "head"
            )
        yield db
    finally:
        db.close()
        with admin.begin() as connection:
            connection.execute(DropSchema(schema, cascade=True))
        admin.dispose()


@dataclass(frozen=True)
class Harness:
    db: Database
    service: AuthService
    user_id: UUID
    email: str

    def login(self) -> TokenPair:
        return self.service.login(
            IdentityAttributeKind.email, self.email.upper(), PASSWORD
        )


@pytest.fixture
def harness(database: Database) -> Harness:
    service = AuthService(
        database.sessions, AuthSettings(signing_key=SecretStr("s" * 64))
    )
    user_id = uuid4()
    email = f"{user_id}@example.test"
    with database.sessions.begin() as db:
        db.add(
            User(user_id=user_id, display_name="Auth test", status=AccountStatus.active)
        )
        db.flush()
        db.add(
            VerifiedIdentityAttribute(
                user_id=user_id,
                kind="email",
                normalized_value=email,
                verified_at=datetime.now(UTC),
            )
        )
        db.add(
            PasswordCredential(
                user_id=user_id, password_hash=service.passwords.hash(PASSWORD)
            )
        )
    return Harness(database, service, user_id, email)


def test_rotation_and_durable_family_replay(harness: Harness) -> None:
    first = harness.login()
    independent = harness.login()
    second = harness.service.refresh(first.refresh_token)
    third = harness.service.refresh(second.refresh_token)
    assert third.refresh_token != second.refresh_token != first.refresh_token
    assert harness.service.verify(third.access_token.get_secret_value()) is not None
    with harness.db.sessions() as db:
        stored = db.scalars(
            select(RefreshToken).where(RefreshToken.session_id == first.session_id)
        ).all()
        assert len(stored) == 3
        assert {row.token_hash for row in stored} == {
            refresh_hash(pair.refresh_token) for pair in (first, second, third)
        }
    with pytest.raises(AuthFailure):
        harness.service.refresh(first.refresh_token)
    # A distinct transaction observes revocation despite the raised authentication error.
    with harness.db.sessions() as db:
        family = db.get(AuthSession, first.session_id)
        assert family is not None and family.revoked_at is not None
    assert harness.service.verify(third.access_token.get_secret_value()) is None
    with pytest.raises(AuthFailure):
        harness.service.refresh(third.refresh_token)
    assert (
        harness.service.verify(independent.access_token.get_secret_value()) is not None
    )


def test_concurrent_refresh_revokes_winner(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = harness.login()
    rendezvous = Barrier(2)
    original = harness.service._lock_user

    def simultaneous_lock(db: Session, actor_id: UUID) -> User | None:
        rendezvous.wait(timeout=10)
        return original(db, actor_id)

    monkeypatch.setattr(harness.service, "_lock_user", simultaneous_lock)

    def refresh() -> TokenPair | None:
        try:
            return harness.service.refresh(first.refresh_token)
        except AuthFailure:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(refresh) for _ in range(2)]
        results = [future.result(timeout=20) for future in futures]
    winners = [result for result in results if result is not None]
    assert len(winners) == 1
    assert harness.service.verify(winners[0].access_token.get_secret_value()) is None
    with harness.db.sessions() as db:
        assert (
            db.scalar(
                select(func.count())
                .select_from(RefreshToken)
                .where(RefreshToken.session_id == first.session_id)
            )
            == 2
        )


@pytest.mark.parametrize(
    "state", ["disabled", "pending", "revoked", "idle_expired", "absolute_expired"]
)
def test_invalid_account_or_session(harness: Harness, state: str) -> None:
    pair = harness.login()
    now = datetime.now(UTC)
    with harness.db.sessions.begin() as db:
        if state in {"disabled", "pending"}:
            db.execute(
                update(User)
                .where(User.user_id == harness.user_id)
                .values(status=AccountStatus(state))
            )
        elif state == "revoked":
            db.execute(
                update(AuthSession)
                .where(AuthSession.session_id == pair.session_id)
                .values(revoked_at=now)
            )
        elif state == "idle_expired":
            db.execute(
                update(AuthSession)
                .where(AuthSession.session_id == pair.session_id)
                .values(refresh_expires_at=now - timedelta(seconds=1))
            )
        else:
            db.execute(
                update(AuthSession)
                .where(AuthSession.session_id == pair.session_id)
                .values(
                    created_at=now - timedelta(days=31),
                    expires_at=now - timedelta(seconds=1),
                    refresh_expires_at=now - timedelta(seconds=1),
                )
            )
    assert harness.service.verify(pair.access_token.get_secret_value()) is None
    with pytest.raises(AuthFailure):
        harness.service.refresh(pair.refresh_token)
    if state in {"disabled", "pending"}:
        with pytest.raises(AuthFailure, match="Invalid authentication credentials"):
            harness.login()


def test_absolute_lifetime_caps_rotation(harness: Harness) -> None:
    first = harness.login()
    limit = datetime.now(UTC) + timedelta(minutes=5)
    with harness.db.sessions.begin() as db:
        db.execute(
            update(AuthSession)
            .where(AuthSession.session_id == first.session_id)
            .values(expires_at=limit, refresh_expires_at=limit)
        )
    second = harness.service.refresh(first.refresh_token)
    assert second.refresh_expires_at == second.session_expires_at == limit


def test_generic_login_failure(harness: Harness) -> None:
    for identifier, password in [
        (harness.email, SecretStr("wrong")),
        ("absent@example.test", PASSWORD),
    ]:
        with pytest.raises(AuthFailure, match="^Invalid authentication credentials$"):
            harness.service.login(IdentityAttributeKind.email, identifier, password)
    with harness.db.sessions.begin() as db:
        db.execute(
            update(PasswordCredential)
            .where(PasswordCredential.user_id == harness.user_id)
            .values(password_hash="broken")
        )
    with pytest.raises(AuthFailure, match="^Invalid authentication credentials$"):
        harness.login()


def test_revocation_and_logout_all(harness: Harness) -> None:
    first, second = harness.login(), harness.login()
    harness.service.revoke_session(
        first.access_token.get_secret_value(), second.session_id
    )
    assert harness.service.verify(second.access_token.get_secret_value()) is None
    assert harness.service.verify(first.access_token.get_secret_value()) is not None
    with pytest.raises(AuthFailure):
        harness.service.revoke_session(first.access_token.get_secret_value(), uuid4())
    harness.service.logout_all(first.access_token.get_secret_value())
    assert harness.service.verify(first.access_token.get_secret_value()) is None
    for pair in (first, second):
        with pytest.raises(AuthFailure):
            harness.service.refresh(pair.refresh_token)


def test_password_change_revokes_all(harness: Harness) -> None:
    first, second = harness.login(), harness.login()
    with pytest.raises(AuthFailure):
        harness.service.change_password(
            first.access_token.get_secret_value(),
            SecretStr("wrong"),
            SecretStr("new secure password"),
        )
    assert harness.service.verify(first.access_token.get_secret_value()) is not None
    harness.service.change_password(
        first.access_token.get_secret_value(),
        PASSWORD,
        SecretStr("new secure password"),
    )
    for pair in (first, second):
        assert harness.service.verify(pair.access_token.get_secret_value()) is None
        with pytest.raises(AuthFailure):
            harness.service.refresh(pair.refresh_token)
    with pytest.raises(AuthFailure):
        harness.login()
    harness.service.login(
        IdentityAttributeKind.email, harness.email, SecretStr("new secure password")
    )


def test_refresh_transaction_rollback(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = harness.login()

    def failed_issue(actor_id: UUID, session_id: UUID) -> str:
        raise RuntimeError("Injected token signing failure")

    with monkeypatch.context() as patch:
        patch.setattr(harness.service.tokens, "issue", failed_issue)
        with pytest.raises(RuntimeError, match="Injected"):
            harness.service.refresh(first.refresh_token)
    with harness.db.sessions() as db:
        token = db.get(RefreshToken, refresh_hash(first.refresh_token))
        assert token is not None and token.consumed_at is None
        assert (
            db.scalar(
                select(func.count())
                .select_from(RefreshToken)
                .where(RefreshToken.session_id == first.session_id)
            )
            == 1
        )
    assert harness.service.refresh(first.refresh_token)


def test_commit_failure_returns_no_tokens(harness: Harness) -> None:
    first = harness.login()

    def failed_commit(db: Session) -> None:
        db.flush()
        raise RuntimeError("Injected commit failure")

    event.listen(harness.service.sessions, "before_commit", failed_commit)
    try:
        with pytest.raises(RuntimeError, match="Injected"):
            harness.service.refresh(first.refresh_token)
    finally:
        event.remove(harness.service.sessions, "before_commit", failed_commit)
    assert harness.service.refresh(first.refresh_token)


def test_password_and_revocation_rollback_together(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = harness.login()
    original = harness.service._revoke_all

    def failed_revoke(db: Session, actor_id: UUID) -> None:
        original(db, actor_id)
        db.flush()
        raise RuntimeError("Injected failure after password update and revocation")

    with monkeypatch.context() as patch:
        patch.setattr(harness.service, "_revoke_all", failed_revoke)
        with pytest.raises(RuntimeError, match="Injected"):
            harness.service.change_password(
                first.access_token.get_secret_value(),
                PASSWORD,
                SecretStr("new secure password"),
            )
    assert harness.service.verify(first.access_token.get_secret_value()) is not None
    assert harness.login()


def test_unknown_refresh_and_mismatched_actor(harness: Harness) -> None:
    with pytest.raises(AuthFailure):
        harness.service.refresh(SecretStr("unknown"))
    first = harness.login()
    assert (
        harness.service.verify(harness.service.tokens.issue(uuid4(), first.session_id))
        is None
    )
    assert (
        harness.service.verify(harness.service.tokens.issue(harness.user_id, uuid4()))
        is None
    )


def test_expired_refresh_generation(harness: Harness) -> None:
    pair = harness.login()
    with harness.db.sessions.begin() as db:
        db.execute(
            update(RefreshToken)
            .where(RefreshToken.token_hash == refresh_hash(pair.refresh_token))
            .values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
        )
    with pytest.raises(AuthFailure):
        harness.service.refresh(pair.refresh_token)


def test_cross_account_revocation_denied(harness: Harness) -> None:
    pair = harness.login()
    other_id = uuid4()
    now = datetime.now(UTC)
    with harness.db.sessions.begin() as db:
        db.add(
            User(user_id=other_id, display_name="Other", status=AccountStatus.active)
        )
        db.flush()
        other_session = AuthSession(
            session_id=uuid4(),
            user_id=other_id,
            created_at=now,
            expires_at=now + timedelta(days=30),
            refresh_expires_at=now + timedelta(days=7),
        )
        db.add(other_session)
    with pytest.raises(AuthFailure):
        harness.service.revoke_session(
            pair.access_token.get_secret_value(), other_session.session_id
        )
    assert (
        harness.service.verify(
            harness.service.tokens.issue(other_id, other_session.session_id)
        )
        is not None
    )
