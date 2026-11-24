"""Exercise the migrated PostgreSQL schema through independent, rolled-back transactions."""

import os
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from viora.config import Settings
from viora.contracts import Actor
from viora.db import Database
from viora.identity.public import identity_directory
from viora.institution.public import institution_directory, institution_status

pytestmark = pytest.mark.postgres


@pytest.fixture
def session() -> Iterator[Session]:
    url = os.environ.get("VIORA_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Requires a migrated PostgreSQL test database")
    db = Database(Settings.model_validate({"database_url": url}))
    try:
        with db.sessions() as session:
            try:
                yield session
            finally:
                session.rollback()
    finally:
        db.close()


@contextmanager
def rejected(session: Session, sqlstate: str) -> Iterator[None]:
    """Verify the expected constraint class while preserving the fixture transaction."""
    with pytest.raises(IntegrityError) as caught, session.begin_nested():
        yield
    assert getattr(caught.value.orig, "sqlstate", None) == sqlstate


@dataclass(frozen=True)
class School:
    tenant: UUID
    section: UUID
    subject: UUID
    offering: UUID


def school(session: Session, name: str) -> School:
    ids = {
        key: uuid4()
        for key in ("tenant", "year", "term", "section", "subject", "offering")
    }
    statements = (
        "INSERT INTO institutions (tenant_id,name) VALUES (:tenant,:name)",
        "INSERT INTO academic_years (tenant_id,id,name) VALUES (:tenant,:year,'Year 1')",
        "INSERT INTO semesters (tenant_id,id,name,academic_year_id) VALUES (:tenant,:term,'Term 1',:year)",
        "INSERT INTO sections (tenant_id,id,name,semester_id) VALUES (:tenant,:section,'Section A',:term)",
        "INSERT INTO subjects (tenant_id,id,name) VALUES (:tenant,:subject,'Mathematics')",
        "INSERT INTO subject_offerings (tenant_id,id,name,subject_id,section_id) VALUES (:tenant,:offering,'Algebra',:subject,:section)",
    )
    for statement in statements:
        session.execute(text(statement), {**ids, "name": name})
    return School(ids["tenant"], ids["section"], ids["subject"], ids["offering"])


def user(session: Session) -> UUID:
    user_id = uuid4()
    session.execute(
        text(
            "INSERT INTO users (user_id,display_name,status) VALUES (:id,'Test member','active')"
        ),
        {"id": user_id},
    )
    return user_id


def membership(session: Session, tenant: UUID, member: UUID) -> None:
    session.execute(
        text(
            "INSERT INTO memberships (tenant_id,user_id,status) VALUES (:tenant,:member,'active')"
        ),
        {"tenant": tenant, "member": member},
    )


def role(session: Session, tenant: UUID, member: UUID, value: str) -> None:
    session.execute(
        text(
            "INSERT INTO role_grants (tenant_id,user_id,role) VALUES (:tenant,:member,:role)"
        ),
        {"tenant": tenant, "member": member, "role": value},
    )


def test_multi_institution_identity_and_lifecycle(session: Session) -> None:
    first, second = school(session, "North"), school(session, "South")
    member = user(session)
    actor = Actor(actor_id=member)
    identities = identity_directory(session, institution_status(session))
    for tenant, value in (
        (first.tenant, "subject_teacher"),
        (second.tenant, "student"),
    ):
        membership(session, tenant, member)
        role(session, tenant, member, value)
        assert identities.applicable_roles(member, tenant) == {value}
        context = identities.active(actor, tenant)
        assert context is not None and context.roles == {value}
    assert identities.active(actor, uuid4()) is None
    with session.begin_nested() as snapshot:
        session.execute(
            text("UPDATE institutions SET active=false WHERE tenant_id=:tenant"),
            {"tenant": first.tenant},
        )
        assert identities.active(actor, first.tenant) is None
        assert not identities.applicable_roles(member, first.tenant)
        assert identities.active(actor, second.tenant) is not None
        snapshot.rollback()
    with rejected(session, "23505"):
        membership(session, first.tenant, member)
    with rejected(session, "23503"):
        membership(session, first.tenant, uuid4())
    with rejected(session, "23503"):
        membership(session, uuid4(), member)
    with rejected(session, "23514"):
        role(session, first.tenant, member, "admin")
    for state in ("invited", "disabled", "revoked"):
        session.execute(
            text(
                "UPDATE memberships SET status=:state WHERE tenant_id=:tenant AND user_id=:member"
            ),
            {"state": state, "tenant": first.tenant, "member": member},
        )
        assert identities.active(actor, first.tenant) is None
        assert not identities.applicable_roles(member, first.tenant)
        assert identities.active(actor, second.tenant) is not None
    session.execute(
        text("UPDATE users SET status='disabled' WHERE user_id=:member"),
        {"member": member},
    )
    assert identities.active(actor, second.tenant) is None
    assert not identities.applicable_roles(member, second.tenant)


def test_resource_assignments_and_revocation(session: Session) -> None:
    first, second = school(session, "North"), school(session, "South")
    teacher, student = user(session), user(session)
    visiting_member = user(session)
    membership(session, second.tenant, visiting_member)
    for tenant in (first.tenant, second.tenant):
        for member, name in ((teacher, "subject_teacher"), (student, "student")):
            membership(session, tenant, member)
            role(session, tenant, member, name)
    directory = institution_directory(
        session, identity_directory(session, institution_status(session))
    )
    assert directory.resolve_institution(first.tenant) is not None
    assert directory.academic_context(first.tenant, first.offering) is not None
    assert directory.academic_context(second.tenant, first.offering) is None
    assert directory.teacher_assignment(first.tenant, teacher, first.offering) is None
    assert directory.student_enrolment(first.tenant, student, first.offering) is None
    for table, member in (
        ("teacher_assignments", teacher),
        ("student_enrolments", student),
    ):
        insert = text(
            f"INSERT INTO {table} (tenant_id,user_id,offering_id) VALUES (:tenant,:member,:offering)"
        )
        args = {"tenant": first.tenant, "member": member, "offering": first.offering}
        session.execute(insert, args)
        with rejected(session, "23503"):
            session.execute(insert, {**args, "offering": second.offering})
        with rejected(session, "23503"):
            session.execute(insert, {**args, "member": visiting_member})
    assert (
        directory.teacher_assignment(first.tenant, teacher, first.offering) is not None
    )
    assert (
        directory.student_enrolment(first.tenant, student, first.offering) is not None
    )
    assert directory.teacher_assignment(second.tenant, teacher, second.offering) is None
    assert directory.student_enrolment(second.tenant, student, second.offering) is None
    session.execute(
        text(
            "UPDATE role_grants SET revoked_at=CURRENT_TIMESTAMP WHERE tenant_id=:tenant AND user_id=:member"
        ),
        {"tenant": first.tenant, "member": teacher},
    )
    assert directory.teacher_assignment(first.tenant, teacher, first.offering) is None
    session.execute(
        text(
            "UPDATE memberships SET status='revoked' WHERE tenant_id=:tenant AND user_id=:member"
        ),
        {"tenant": first.tenant, "member": student},
    )
    assert directory.student_enrolment(first.tenant, student, first.offering) is None


def test_academic_and_source_foreign_keys(session: Session) -> None:
    first, second = school(session, "North"), school(session, "South")
    for subject, section in (
        (first.subject, second.section),
        (second.subject, first.section),
    ):
        with rejected(session, "23503"):
            session.execute(
                text(
                    "INSERT INTO subject_offerings (tenant_id,id,name,subject_id,section_id) VALUES (:tenant,:id,'Mixed',:subject,:section)"
                ),
                {
                    "tenant": first.tenant,
                    "id": uuid4(),
                    "subject": subject,
                    "section": section,
                },
            )
    with rejected(session, "23503"):
        session.execute(
            text(
                "INSERT INTO sources (tenant_id,source_id,title) VALUES (:tenant,:id,'Unknown institution')"
            ),
            {"tenant": uuid4(), "id": uuid4()},
        )


def test_verified_identifier_ownership_and_public_projection(session: Session) -> None:
    first, second = user(session), user(session)
    email = f"{first}@school.example"
    verify = text(
        "INSERT INTO verified_identity_attributes (user_id,kind,normalized_value,verified_at) "
        "VALUES (:member,'email',:email,CURRENT_TIMESTAMP)"
    )
    session.execute(verify, {"member": first, "email": email})
    with rejected(session, "23505"):
        session.execute(verify, {"member": second, "email": email})
    with rejected(session, "23514"):
        session.execute(verify, {"member": second, "email": email.upper()})
    session.execute(
        text(
            "INSERT INTO password_credentials (user_id,password_hash) VALUES (:member,:encoded)"
        ),
        {"member": first, "encoded": "opaque-test-verifier"},
    )
    summary = identity_directory(session, institution_status(session)).user(first)
    assert summary is not None
    assert set(summary.model_dump()) == {"user_id", "display_name", "status"}


def test_guardian_link_requires_verified_consent_and_is_revocable(
    session: Session,
) -> None:
    first, second = school(session, "North"), school(session, "South")
    guardian, child = user(session), user(session)
    for member, name in ((guardian, "guardian"), (child, "student")):
        membership(session, first.tenant, member)
        role(session, first.tenant, member, name)
    directory = institution_directory(
        session, identity_directory(session, institution_status(session))
    )
    args = {"tenant": first.tenant, "guardian": guardian, "child": child}
    insert = text(
        "INSERT INTO guardian_links (tenant_id,user_id,student_id) VALUES (:tenant,:guardian,:child)"
    )
    with rejected(session, "23514"):
        session.execute(insert, {**args, "child": guardian})
    with rejected(session, "23503"):
        session.execute(insert, {**args, "tenant": second.tenant})
    session.execute(insert, args)
    where = " WHERE tenant_id=:tenant AND user_id=:guardian AND student_id=:child"
    assert not directory.guardian_link(first.tenant, guardian, child)
    with rejected(session, "23514"):
        session.execute(text("UPDATE guardian_links SET active=true" + where), args)
    session.execute(
        text(
            "UPDATE guardian_links SET active=true, verified_at=now(), consent_at=now()"
            + where
        ),
        args,
    )
    assert directory.guardian_link(first.tenant, guardian, child)
    assert not directory.guardian_link(second.tenant, guardian, child)
    session.execute(text("UPDATE guardian_links SET active=false" + where), args)
    assert not directory.guardian_link(first.tenant, guardian, child)


def test_default_lifecycle_and_scoped_department_head(session: Session) -> None:
    north, south = school(session, "North"), school(session, "South")
    member, department = uuid4(), uuid4()
    params = {
        "member": member,
        "north": north.tenant,
        "south": south.tenant,
        "department": department,
    }
    session.execute(
        text("INSERT INTO users (user_id, display_name) VALUES (:member, 'Head')"),
        params,
    )
    session.execute(
        text("INSERT INTO memberships (tenant_id, user_id) VALUES (:north, :member)"),
        params,
    )
    identities = identity_directory(session, institution_status(session))
    account = identities.user(member)
    membership_row = identities.membership(member, north.tenant)
    assert account is not None and account.status.value == "pending"
    assert membership_row is not None and membership_row.status.value == "invited"
    assert identities.active(Actor(actor_id=member), north.tenant) is None
    with rejected(session, "23514"):
        session.execute(
            text("UPDATE memberships SET status='unknown' WHERE user_id=:member"),
            params,
        )
    with rejected(session, "23514"):
        session.execute(
            text("UPDATE users SET status='unknown' WHERE user_id=:member"), params
        )
    session.execute(
        text("UPDATE users SET status='active' WHERE user_id=:member"), params
    )
    session.execute(
        text("UPDATE memberships SET status='active' WHERE user_id=:member"), params
    )
    for school_id in ("north", "south"):
        session.execute(
            text(
                f"INSERT INTO departments (tenant_id,id,name) VALUES (:{school_id},:department,'Science')"
            ),
            params,
        )
    with rejected(session, "23503"):
        session.execute(
            text(
                "INSERT INTO department_heads (tenant_id,user_id,department_id) VALUES (:south,:member,:department)"
            ),
            params,
        )
    session.execute(
        text(
            "INSERT INTO department_heads (tenant_id,user_id,department_id) VALUES (:north,:member,:department)"
        ),
        params,
    )
    directory = institution_directory(session, identities)
    assert not directory.heads_department(north.tenant, member, department)
    role(session, north.tenant, member, "department_head")
    assert directory.heads_department(north.tenant, member, department)
    assert not directory.heads_department(south.tenant, member, department)
