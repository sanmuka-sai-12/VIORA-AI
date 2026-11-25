"""Interactive local development account provisioning & synthetic tenant onboarding.

Prompts for passwords securely via getpass (never stored or echoed).
Refuses to run when APP_ENV=production.
"""

from __future__ import annotations

import getpass
import os
import sys
from pathlib import Path

from pydantic import SecretStr
from sqlalchemy import select

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from viora.authorization.public import bind_tenant
from viora.config import Settings
from viora.db import Database
from viora.identity.auth import Passwords
from viora.identity.models import (
    PasswordCredential,
    User,
    VerifiedIdentityAttribute,
)
from viora.identity.provision import provision_account
from viora.identity.repository import SqlMembershipRepository
from viora.identity.schemas import (
    AccountStatus,
    IdentityAttributeKind,
    InstitutionRole,
    MembershipStatus,
)
from viora.institution.bootstrap import bootstrap_institution
from viora.institution.models import Institution, SubjectOffering
from viora.institution.repository import SqlAcademicRepository


def _prompt_password(label: str) -> SecretStr:
    while True:
        pw = getpass.getpass(f"Enter password for {label} (12-1024 chars): ")
        if len(pw) < 12 or len(pw) > 1024:
            print("Password must be between 12 and 1024 characters.", file=sys.stderr)
            continue
        confirm = getpass.getpass(f"Confirm password for {label}: ")
        if pw != confirm:
            print("Passwords did not match. Try again.", file=sys.stderr)
            continue
        return SecretStr(pw)


def _upsert_user(db: Database, *, email: str, display_name: str, password: SecretStr):
    normalized = email.strip().lower()
    with db.sessions.begin() as session:
        existing_uid = session.scalar(
            select(VerifiedIdentityAttribute.user_id).where(
                VerifiedIdentityAttribute.kind == IdentityAttributeKind.email.value,
                VerifiedIdentityAttribute.normalized_value == normalized,
            )
        )
        if existing_uid is not None:
            user = session.get(User, existing_uid)
            cred = session.get(PasswordCredential, existing_uid)
            hashed = Passwords().hash(password)
            if cred is None:
                session.add(PasswordCredential(user_id=existing_uid, password_hash=hashed))
            else:
                cred.password_hash = hashed
            if user is not None:
                user.display_name = display_name
                user.status = AccountStatus.active
            return existing_uid

    summary = provision_account(
        db.sessions,
        email=normalized,
        display_name=display_name,
        password=password,
        status=AccountStatus.active,
    )
    return summary.user_id


def _ensure_membership_role(session, tenant_id, user_id, role: InstitutionRole) -> None:
    repo = SqlMembershipRepository(session)
    member = repo.get_member(tenant_id, user_id)
    if member is None:
        member = repo.add_member(tenant_id, user_id, MembershipStatus.active)
    elif member.status != MembershipStatus.active:
        member = repo.set_membership_status(tenant_id, user_id, MembershipStatus.active)
    if member is not None and role not in member.roles:
        repo.grant_role(tenant_id, user_id, role)


def main() -> int:
    settings = Settings.load()
    if settings.environment == "production":
        print("Refusing to provision development accounts in production.", file=sys.stderr)
        return 1

    admin_url = os.environ.get("MIGRATION_DATABASE_URL") or os.environ.get(
        "BOOTSTRAP_DATABASE_URL"
    )
    if not admin_url:
        print(
            "Set MIGRATION_DATABASE_URL (owner role) to provision and associate dev accounts.",
            file=sys.stderr,
        )
        return 1

    owner_pw = _prompt_password("Owner (dev-owner@northbridge.example)")
    teacher_pw = _prompt_password("Teacher (dev-teacher@northbridge.example)")
    student_pw = _prompt_password("Student (dev-student@northbridge.example)")

    db = Database(settings.model_copy(update={"database_url": SecretStr(admin_url)}))
    try:
        owner_id = _upsert_user(
            db,
            email="dev-owner@northbridge.example",
            display_name="Dev Owner",
            password=owner_pw,
        )
        teacher_id = _upsert_user(
            db,
            email="dev-teacher@northbridge.example",
            display_name="Dev Teacher",
            password=teacher_pw,
        )
        student_id = _upsert_user(
            db,
            email="dev-student@northbridge.example",
            display_name="Dev Student",
            password=student_pw,
        )

        with db.sessions.begin() as session:
            north_id = session.scalar(
                select(Institution.tenant_id).where(Institution.name == "Northbridge College")
            )
            south_id = session.scalar(
                select(Institution.tenant_id).where(Institution.name == "Southvale Institute")
            )

        if north_id is None:
            north_id = bootstrap_institution(
                db.sessions,
                name="Northbridge College",
                owner_user_id=owner_id,
            ).tenant_id

        with db.sessions.begin() as session:
            bind_tenant(session, north_id)
            _ensure_membership_role(session, north_id, owner_id, InstitutionRole.institution_owner)
            _ensure_membership_role(session, north_id, teacher_id, InstitutionRole.subject_teacher)
            _ensure_membership_role(session, north_id, student_id, InstitutionRole.student)

            offering_id = session.scalar(
                select(SubjectOffering.id).where(
                    SubjectOffering.tenant_id == north_id,
                    SubjectOffering.active.is_(True),
                )
            )
            if offering_id is not None:
                acad = SqlAcademicRepository(session)
                if acad.teacher_assignment(north_id, teacher_id, offering_id) is None:
                    acad.assign_teacher(north_id, offering_id, teacher_id)
                if acad.student_enrolment(north_id, student_id, offering_id) is None:
                    acad.enrol_student(north_id, offering_id, student_id)

        if south_id is not None:
            with db.sessions.begin() as session:
                bind_tenant(session, south_id)
                _ensure_membership_role(session, south_id, owner_id, InstitutionRole.subject_teacher)

        print("\nProvisioned and associated local development accounts:")
        print("  Owner:   dev-owner@northbridge.example   (Northbridge College owner; Southvale teacher)")
        print("  Teacher: dev-teacher@northbridge.example (Northbridge College subject_teacher)")
        print("  Student: dev-student@northbridge.example (Northbridge College student)")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
