"""Operator-only institution bootstrap workflow per DEC-003.

Never import this module from HTTP routers or execute it with runtime app roles.
"""

import argparse
import os
import sys
from uuid import UUID, uuid4

from pydantic import SecretStr
from sqlalchemy.orm import Session, sessionmaker

from ..authorization.public import bind_tenant, change_audit
from ..config import Settings
from ..contracts import Actor, ResourceRef
from ..db import Database
from ..identity.public import (
    InstitutionRole,
    grant_initial_owner_membership,
    identity_directory,
    is_verified_active_user,
)
from .models import Institution
from .repository import SqlInstitutionStatus
from .schemas import InstitutionInfo


def bootstrap_institution(
    admin_sessions: sessionmaker[Session],
    *,
    name: str,
    owner_user_id: UUID,
    tenant_id: UUID | None = None,
    validate_before_activate: bool = True,
) -> InstitutionInfo:
    """Create, populate, verify, audit, and activate an institution atomically."""
    cleaned_name = name.strip()
    if not cleaned_name:
        raise ValueError("Institution name must not be blank")
    tid = tenant_id or uuid4()

    with admin_sessions.begin() as db:
        bind_tenant(db, tid)
        if not is_verified_active_user(db, owner_user_id):
            raise ValueError(
                "Initial owner must be an active user with a verified identity attribute"
            )
        institution = Institution(tenant_id=tid, name=cleaned_name, active=False)
        db.add(institution)
        db.flush()

        grant_initial_owner_membership(db, tid, owner_user_id)

        if validate_before_activate:
            identities = identity_directory(db, SqlInstitutionStatus(db))
            # While institution is still inactive, directory must report inactive/None.
            if identities.active(Actor(actor_id=owner_user_id), tid) is not None:
                raise RuntimeError("Inactive institution unexpectedly reported active")

        institution.active = True
        db.flush()

        identities = identity_directory(db, SqlInstitutionStatus(db))
        context = identities.active(Actor(actor_id=owner_user_id), tid)
        if (
            context is None
            or InstitutionRole.institution_owner.value not in context.roles
        ):
            raise RuntimeError("Owner membership validation failed during bootstrap")

        resource = ResourceRef(tenant_id=tid, kind="institution", resource_id=tid)
        change_audit(db).change(context, "institution.bootstrap", resource)

    return InstitutionInfo(tenant_id=tid, name=cleaned_name, active=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Operator-only bootstrap for a new VIORA-AI institution."
    )
    parser.add_argument("--name", required=True, help="Institution display name")
    parser.add_argument(
        "--owner-user-id", required=True, type=UUID, help="Verified active user UUID"
    )
    parser.add_argument(
        "--tenant-id", type=UUID, default=None, help="Optional explicit tenant UUID"
    )
    args = parser.parse_args(argv)

    admin_url = os.environ.get("BOOTSTRAP_DATABASE_URL") or os.environ.get(
        "MIGRATION_DATABASE_URL"
    )
    if not admin_url:
        print(
            "BOOTSTRAP_DATABASE_URL or MIGRATION_DATABASE_URL is required.",
            file=sys.stderr,
        )
        return 1

    settings = Settings.load().model_copy(update={"database_url": SecretStr(admin_url)})
    db = Database(settings)
    try:
        info = bootstrap_institution(
            db.sessions,
            name=args.name,
            owner_user_id=args.owner_user_id,
            tenant_id=args.tenant_id,
        )
        print(
            f"Bootstrapped institution {info.tenant_id} ({info.name}), active={info.active}"
        )
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
