"""Interactive password setter for existing Northbridge College dev accounts.

Prompts securely via getpass in the local terminal, updates only
password_credentials, and preserves all memberships, roles, and assignments.
"""

from __future__ import annotations

import getpass
import os
import sys
from pathlib import Path
from urllib.parse import quote_plus

from pydantic import SecretStr
from sqlalchemy import select

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from viora.config import Settings
from viora.db import Database
from viora.identity.auth import Passwords
from viora.identity.models import PasswordCredential, VerifiedIdentityAttribute
from viora.identity.schemas import IdentityAttributeKind

TARGET_ACCOUNTS = (
    ("Owner", "owner@northbridge.example"),
    ("Admin", "admin@northbridge.example"),
    ("Teacher", "teacher@northbridge.example"),
    ("Student", "student@northbridge.example"),
)


def _prompt_password(role_label: str, email: str) -> SecretStr | None:
    while True:
        try:
            pw = getpass.getpass(
                f"New password for {role_label} ({email}) [12+ chars, input hidden, Enter to skip]: "
            )
            if pw == "":
                return None
            if len(pw) < 12 or len(pw) > 1024:
                print("Password must be 12 to 1024 characters.", file=sys.stderr)
                continue
            confirm = getpass.getpass(f"Confirm password for {email} (input hidden): ")
        except KeyboardInterrupt:
            print("\nCancelled.", file=sys.stderr)
            raise
        if pw != confirm:
            print("Passwords did not match. Try again.", file=sys.stderr)
            continue
        return SecretStr(pw)


def _resolve_admin_url() -> str:
    explicit = os.environ.get("MIGRATION_DATABASE_URL")
    if explicit:
        return explicit
    env_file = REPO_ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            if line.startswith("POSTGRES_PASSWORD="):
                pw = quote_plus(line.split("=", 1)[1].strip())
                return f"postgresql+psycopg://viora:{pw}@127.0.0.1:5432/viora"
    raise RuntimeError(
        "Set MIGRATION_DATABASE_URL or POSTGRES_PASSWORD in .env first."
    )


def main() -> int:
    settings = Settings.load()
    if settings.environment == "production":
        print("Refusing to run dev password helper in production.", file=sys.stderr)
        return 1

    db = Database(
        settings.model_copy(update={"database_url": SecretStr(_resolve_admin_url())})
    )
    hasher = Passwords()
    try:
        updated: list[str] = []
        for role_label, email in TARGET_ACCOUNTS:
            secret = _prompt_password(role_label, email)
            if secret is None:
                continue
            with db.sessions.begin() as session:
                user_id = session.scalar(
                    select(VerifiedIdentityAttribute.user_id).where(
                        VerifiedIdentityAttribute.kind
                        == IdentityAttributeKind.email.value,
                        VerifiedIdentityAttribute.normalized_value == email,
                    )
                )
                if user_id is None:
                    print(f"Account not found: {email}", file=sys.stderr)
                    return 1
                cred = session.get(PasswordCredential, user_id)
                encoded = hasher.hash(secret)
                if cred is None:
                    session.add(
                        PasswordCredential(user_id=user_id, password_hash=encoded)
                    )
                else:
                    cred.password_hash = encoded
            updated.append(email)

        if updated:
            print("Updated passwords for:", ", ".join(updated))
        else:
            print("No passwords changed.")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130) from None
