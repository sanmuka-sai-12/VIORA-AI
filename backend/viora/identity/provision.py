"""Controlled development and test account provisioning (no public signup)."""

import argparse
import getpass
import os
import sys
from datetime import UTC, datetime
from uuid import UUID, uuid4

from pydantic import SecretStr
from sqlalchemy.orm import Session, sessionmaker

from ..config import Settings
from ..db import Database
from .auth import Passwords
from .models import PasswordCredential, User, VerifiedIdentityAttribute
from .schemas import AccountStatus, IdentityAttributeKind, UserSummary


def provision_account(
    sessions: sessionmaker[Session],
    *,
    email: str,
    display_name: str,
    password: SecretStr,
    status: AccountStatus = AccountStatus.active,
    user_id: UUID | None = None,
) -> UserSummary:
    """Create a user, verified email identifier, and Argon2id password credential."""
    normalized_email = email.strip().lower()
    cleaned_name = display_name.strip()
    if not normalized_email or "@" not in normalized_email:
        raise ValueError("A valid email address is required")
    if not cleaned_name:
        raise ValueError("A non-empty display name is required")
    encoded_password = Passwords().hash(password)
    uid = user_id or uuid4()
    now = datetime.now(UTC)
    with sessions.begin() as db:
        db.add(User(user_id=uid, display_name=cleaned_name, status=status))
        db.flush()
        db.add(
            VerifiedIdentityAttribute(
                user_id=uid,
                kind=IdentityAttributeKind.email.value,
                normalized_value=normalized_email,
                verified_at=now,
            )
        )
        db.add(PasswordCredential(user_id=uid, password_hash=encoded_password))
    return UserSummary(user_id=uid, display_name=cleaned_name, status=status)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Provision a local development or test VIORA-AI user account."
    )
    parser.add_argument("--email", required=True, help="Verified login email")
    parser.add_argument("--display-name", required=True, help="Account display name")
    parser.add_argument(
        "--status",
        choices=[s.value for s in AccountStatus],
        default=AccountStatus.active.value,
        help="Initial account status (default: active)",
    )
    parser.add_argument(
        "--password-env",
        default="VIORA_PROVISION_PASSWORD",
        help="Environment variable holding the initial password (prompts if unset)",
    )
    args = parser.parse_args(argv)

    settings = Settings.load()
    if settings.environment == "production":
        print(
            "Refusing to run development provisioning CLI in production.",
            file=sys.stderr,
        )
        return 1

    raw_password = os.environ.get(args.password_env)
    if not raw_password:
        raw_password = getpass.getpass("Password (12-1024 chars): ")

    admin_url = os.environ.get("MIGRATION_DATABASE_URL")
    db_settings = (
        settings.model_copy(update={"database_url": SecretStr(admin_url)})
        if admin_url
        else settings
    )
    db = Database(db_settings)
    try:
        summary = provision_account(
            db.sessions,
            email=args.email,
            display_name=args.display_name,
            password=SecretStr(raw_password),
            status=AccountStatus(args.status),
        )
        print(
            f"Provisioned {summary.status.value} user {summary.user_id} ({summary.display_name})"
        )
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
