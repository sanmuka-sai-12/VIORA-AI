"""Real non-owner PostgreSQL RLS and authorization tests in a disposable schema."""

import os
import secrets
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from psycopg import sql
from pydantic import SecretStr
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session
from sqlalchemy.schema import CreateSchema, DropSchema
from viora.authorization.public import AuthorizationService
from viora.authorization.tenant import bind_tenant, require_application_role
from viora.config import Settings
from viora.contracts import Actor, ResourceRef
from viora.database_roles import grant_application_privileges
from viora.db import Database
from viora.security import AccessError

pytestmark = pytest.mark.postgres


@dataclass(frozen=True)
class SecurityDatabase:
    admin: Engine
    app: Database
    schema: str
    role: str


@pytest.fixture(scope="module")
def secured_database() -> Iterator[SecurityDatabase]:
    raw = os.environ.get("VIORA_TEST_DATABASE_URL")
    if not raw:
        pytest.skip("Dedicated viora_test database required")
    url = make_url(raw)
    if url.database != "viora_test":
        pytest.fail(
            "Destructive security fixtures require the dedicated viora_test database"
        )
    schema, role = f"security_{uuid4().hex}", f"app_{uuid4().hex}"
    password = secrets.token_urlsafe(32)
    scoped = url.update_query_dict({"options": f"-csearch_path={schema}"})
    admin = create_engine(scoped)
    app = Database(
        Settings(
            database_url=SecretStr(
                scoped.set(username=role, password=password).render_as_string(
                    hide_password=False
                )
            ),
            pool_size=1,
        )
    )
    with admin.begin() as connection:
        connection.execute(CreateSchema(schema))
        connection.connection.driver_connection.execute(
            sql.SQL(
                "CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE NOINHERIT"
            ).format(sql.Identifier(role), sql.Literal(password))
        )
    try:
        with pytest.MonkeyPatch.context() as patch:
            patch.setenv("DATABASE_URL", scoped.render_as_string(hide_password=False))
            patch.setenv(
                "MIGRATION_DATABASE_URL", scoped.render_as_string(hide_password=False)
            )
            config = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
            command.upgrade(config, "head")
            command.check(config)
        with admin.begin() as connection:
            grant_application_privileges(connection, role, schema)
        yield SecurityDatabase(admin, app, schema, role)
    finally:
        app.close()
        with admin.begin() as connection:
            connection.execute(DropSchema(schema, cascade=True))
            connection.connection.driver_connection.execute(
                sql.SQL("DROP ROLE {}").format(sql.Identifier(role))
            )
        admin.dispose()


@dataclass(frozen=True)
class School:
    tenant: UUID
    offering: UUID
    other_offering: UUID
    student: UUID
    other_student: UUID
    teacher: UUID
    guardian: UUID
    owner: UUID
    department: UUID
    section: UUID
    head: UUID
    coordinator: UUID


@pytest.fixture
def schools(secured_database: SecurityDatabase) -> tuple[School, School]:
    result: list[School] = []
    with secured_database.admin.begin() as c:
        for _ in range(2):
            ids = {
                key: uuid4()
                for key in (
                    "tenant",
                    "offering",
                    "other_offering",
                    "student",
                    "other_student",
                    "teacher",
                    "guardian",
                    "owner",
                    "department",
                    "section",
                    "head",
                    "coordinator",
                    "year",
                    "semester",
                    "subject",
                )
            }
            c.execute(
                text(
                    "INSERT INTO institutions VALUES (:tenant,'Security school',true)"
                ),
                ids,
            )
            for key, role in [
                ("teacher", "subject_teacher"),
                ("student", "student"),
                ("other_student", "student"),
                ("guardian", "guardian"),
                ("owner", "institution_owner"),
                ("head", "department_head"),
                ("coordinator", "class_coordinator"),
            ]:
                args = {**ids, "user": ids[key], "role": role}
                c.execute(
                    text(
                        "INSERT INTO users VALUES (:user,'Synthetic member','active')"
                    ),
                    args,
                )
                c.execute(
                    text("INSERT INTO memberships VALUES (:tenant,:user,'active')"),
                    args,
                )
                c.execute(
                    text(
                        "INSERT INTO role_grants (tenant_id,user_id,role) VALUES (:tenant,:user,:role)"
                    ),
                    args,
                )
            c.execute(
                text(
                    "INSERT INTO departments (tenant_id,id,name) VALUES (:tenant,:department,'Department')"
                ),
                ids,
            )
            c.execute(
                text(
                    "INSERT INTO academic_years (tenant_id,id,name) VALUES (:tenant,:year,'Year')"
                ),
                ids,
            )
            c.execute(
                text(
                    "INSERT INTO semesters (tenant_id,id,name,academic_year_id) VALUES (:tenant,:semester,'Term',:year)"
                ),
                ids,
            )
            c.execute(
                text(
                    "INSERT INTO sections (tenant_id,id,name,semester_id) VALUES (:tenant,:section,'Section',:semester)"
                ),
                ids,
            )
            c.execute(
                text(
                    "INSERT INTO subjects (tenant_id,id,name,department_id) VALUES (:tenant,:subject,'Subject',:department)"
                ),
                ids,
            )
            for key in ("offering", "other_offering"):
                c.execute(
                    text(
                        "INSERT INTO subject_offerings (tenant_id,id,name,section_id,subject_id) VALUES (:tenant,:id,'Offering',:section,:subject)"
                    ),
                    {**ids, "id": ids[key]},
                )
            c.execute(
                text(
                    "INSERT INTO teacher_assignments VALUES (:tenant,:teacher,:offering,true)"
                ),
                ids,
            )
            c.execute(
                text(
                    "INSERT INTO student_enrolments VALUES (:tenant,:student,:offering,true)"
                ),
                ids,
            )
            c.execute(
                text(
                    "INSERT INTO guardian_links VALUES (:tenant,:guardian,:student,true,now(),now())"
                ),
                ids,
            )
            c.execute(
                text(
                    "INSERT INTO department_heads VALUES (:tenant,:head,:department,true)"
                ),
                ids,
            )
            c.execute(
                text(
                    "INSERT INTO class_coordinators VALUES (:tenant,:coordinator,:section,true)"
                ),
                ids,
            )
            c.execute(
                text(
                    "INSERT INTO sources VALUES (:tenant,:offering,'Unscoped source')"
                ),
                ids,
            )
            result.append(
                School(**{name: ids[name] for name in School.__dataclass_fields__})
            )
    return result[0], result[1]


def resource(
    school: School, kind: str = "offering", target: UUID | None = None
) -> ResourceRef:
    return ResourceRef(
        tenant_id=school.tenant, kind=kind, resource_id=target or school.offering
    )


def allowed(db: SecurityDatabase, actor: UUID, ref: ResourceRef, action: str) -> bool:
    gate = AuthorizationService(db.app, None)
    try:
        with gate.reauthorize(Actor(actor_id=actor), action, ref):
            return True
    except AccessError as error:
        assert error.status == 403
        return False


def test_teacher_student_guardian_boundaries(
    secured_database: SecurityDatabase, schools: tuple[School, School]
) -> None:
    a, b = schools
    db = secured_database
    assert allowed(db, a.teacher, resource(a), "offering.read")
    assert allowed(db, a.teacher, resource(a), "offering.teach")
    assert not allowed(
        db, a.teacher, resource(a, target=a.other_offering), "offering.read"
    )
    assert not allowed(db, a.teacher, resource(b), "offering.read")
    assert allowed(db, a.student, resource(a), "offering.read")
    assert not allowed(db, a.student, resource(a), "offering.teach")
    assert not allowed(
        db, a.student, resource(a, target=a.other_offering), "offering.read"
    )
    assert allowed(
        db,
        a.guardian,
        resource(a, "student_summary", a.student),
        "student.summary.read",
    )
    assert not allowed(
        db,
        a.guardian,
        resource(a, "student_summary", a.other_student),
        "student.summary.read",
    )
    assert not allowed(
        db,
        a.guardian,
        resource(a, "student_summary", a.student),
        "student.records.read",
    )
    assert not allowed(db, a.guardian, resource(a), "offering.read")


def test_scoped_leadership_and_source_denial(
    secured_database: SecurityDatabase, schools: tuple[School, School]
) -> None:
    a, b = schools
    db = secured_database
    assert allowed(
        db, a.owner, resource(a, "institution", a.tenant), "institution.update_metadata"
    )
    assert not allowed(db, a.owner, resource(a), "offering.read")
    assert not allowed(
        db, a.owner, resource(b, "institution", b.tenant), "institution.read"
    )
    assert allowed(
        db, a.head, resource(a, "department", a.department), "department.read"
    )
    assert not allowed(
        db, a.head, resource(a, "department", uuid4()), "department.read"
    )
    assert allowed(db, a.coordinator, resource(a, "section", a.section), "section.read")
    assert not allowed(
        db, a.coordinator, resource(a, "section", uuid4()), "section.read"
    )
    for actor in (a.owner, a.teacher, a.student):
        assert not allowed(db, actor, resource(a, "source"), "source.read")


@pytest.mark.parametrize(
    "mutation",
    [
        "UPDATE memberships SET status='revoked' WHERE tenant_id=:tenant AND user_id=:teacher",
        "UPDATE role_grants SET revoked_at=now() WHERE tenant_id=:tenant AND user_id=:teacher",
        "UPDATE teacher_assignments SET active=false WHERE tenant_id=:tenant AND user_id=:teacher",
        "UPDATE institutions SET active=false WHERE tenant_id=:tenant",
        "UPDATE users SET status='disabled' WHERE user_id=:teacher",
    ],
)
def test_committed_revocation_next_request(
    secured_database: SecurityDatabase, schools: tuple[School, School], mutation: str
) -> None:
    a, _ = schools
    assert allowed(secured_database, a.teacher, resource(a), "offering.read")
    with secured_database.admin.begin() as c:
        c.execute(text(mutation), {"tenant": a.tenant, "teacher": a.teacher})
    assert not allowed(secured_database, a.teacher, resource(a), "offering.read")


def test_enrolment_and_guardian_revocation(
    secured_database: SecurityDatabase, schools: tuple[School, School]
) -> None:
    a, _ = schools
    assert allowed(secured_database, a.student, resource(a), "offering.read")
    assert allowed(
        secured_database,
        a.guardian,
        resource(a, "student_summary", a.student),
        "student.summary.read",
    )
    with secured_database.admin.begin() as c:
        c.execute(
            text("UPDATE student_enrolments SET active=false WHERE tenant_id=:tenant"),
            {"tenant": a.tenant},
        )
        c.execute(
            text("UPDATE guardian_links SET active=false WHERE tenant_id=:tenant"),
            {"tenant": a.tenant},
        )
    assert not allowed(secured_database, a.student, resource(a), "offering.read")
    assert not allowed(
        secured_database,
        a.guardian,
        resource(a, "student_summary", a.student),
        "student.summary.read",
    )


def test_non_owner_role_and_forced_rls(secured_database: SecurityDatabase) -> None:
    with secured_database.app.sessions.begin() as s:
        require_application_role(s)
        assert s.scalar(text("SELECT current_user")) == secured_database.role
        assert (
            s.scalar(
                text(
                    "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=current_schema() AND c.relrowsecurity AND c.relforcerowsecurity"
                )
            )
            == 18
        )
    with (
        Session(secured_database.admin) as s,
        pytest.raises(RuntimeError, match="non-owner"),
    ):
        require_application_role(s)


def test_cross_tenant_reads_updates_deletes(
    secured_database: SecurityDatabase, schools: tuple[School, School]
) -> None:
    a, b = schools
    with secured_database.app.sessions.begin() as s:
        bind_tenant(s, a.tenant)
        assert s.execute(text("SELECT tenant_id FROM sources")).scalars().all() == [
            a.tenant
        ]
        assert (
            s.execute(
                text("UPDATE sources SET title='forbidden' WHERE tenant_id=:other"),
                {"other": b.tenant},
            ).rowcount
            == 0
        )
        assert (
            s.execute(
                text("DELETE FROM sources WHERE tenant_id=:other"), {"other": b.tenant}
            ).rowcount
            == 0
        )
        assert (
            s.execute(
                text("UPDATE sources SET title='allowed' WHERE tenant_id=:tenant"),
                {"tenant": a.tenant},
            ).rowcount
            == 1
        )


@pytest.mark.parametrize(
    "statement",
    [
        "INSERT INTO sources VALUES (:other,:new,'forbidden')",
        "UPDATE sources SET tenant_id=:other WHERE tenant_id=:tenant",
    ],
)
def test_cross_tenant_write_checks(
    secured_database: SecurityDatabase, schools: tuple[School, School], statement: str
) -> None:
    a, b = schools
    with pytest.raises(DBAPIError), secured_database.app.sessions.begin() as s:
        bind_tenant(s, a.tenant)
        s.execute(
            text(statement), {"tenant": a.tenant, "other": b.tenant, "new": uuid4()}
        )


def test_missing_context_and_pool_reuse(
    secured_database: SecurityDatabase, schools: tuple[School, School]
) -> None:
    a, b = schools
    pid: int | None = None
    for tenant in (a.tenant, None, b.tenant, None):
        with secured_database.app.sessions.begin() as s:
            current = s.scalar(text("SELECT pg_backend_pid()"))
            assert pid is None or pid == current
            pid = current
            if tenant:
                bind_tenant(s, tenant)
            assert s.execute(text("SELECT tenant_id FROM sources")).scalars().all() == (
                [tenant] if tenant else []
            )
    with pytest.raises(DBAPIError), secured_database.app.sessions.begin() as s:
        s.execute(
            text("INSERT INTO sources VALUES (:tenant,:source,'no context')"),
            {"tenant": a.tenant, "source": uuid4()},
        )
    with (
        pytest.raises(RuntimeError, match="switch tenants"),
        secured_database.app.sessions.begin() as s,
    ):
        bind_tenant(s, a.tenant)
        bind_tenant(s, b.tenant)
    with secured_database.app.sessions.begin() as s:
        assert s.scalar(text("SELECT count(*) FROM sources")) == 0
        assert s.scalar(text("SELECT count(*) FROM users")) > 0


def test_denial_audit_durable_and_changes_atomic(
    secured_database: SecurityDatabase, schools: tuple[School, School]
) -> None:
    a, _ = schools
    assert not allowed(
        secured_database, a.teacher, resource(a, "source"), "source.read"
    )
    with secured_database.app.sessions.begin() as s:
        bind_tenant(s, a.tenant)
        assert (
            s.scalar(text("SELECT count(*) FROM security_events WHERE NOT allowed"))
            == 1
        )
    gate = AuthorizationService(secured_database.app, None)
    with (
        pytest.raises(RuntimeError, match="rollback"),
        gate.reauthorize(
            Actor(actor_id=a.teacher), "offering.teach", resource(a)
        ) as scope,
    ):
        scope.session.execute(
            text(
                "UPDATE subject_offerings SET name='rolled back' WHERE tenant_id=:tenant"
            ),
            {"tenant": a.tenant},
        )
        scope.audit.change(scope.context, "offering.updated", resource(a))
        raise RuntimeError("rollback")
    with secured_database.admin.connect() as c:
        assert (
            c.scalar(
                text(
                    "SELECT count(*) FROM security_events WHERE tenant_id=:tenant AND event_kind='change'"
                ),
                {"tenant": a.tenant},
            )
            == 0
        )
        assert (
            c.scalar(
                text(
                    "SELECT count(*) FROM subject_offerings WHERE tenant_id=:tenant AND name='rolled back'"
                ),
                {"tenant": a.tenant},
            )
            == 0
        )
    with pytest.raises(DBAPIError), secured_database.app.sessions.begin() as s:
        bind_tenant(s, a.tenant)
        s.execute(text("DELETE FROM security_events"))


def test_same_transaction_recheck_observes_committed_revocation(
    secured_database: SecurityDatabase, schools: tuple[School, School]
) -> None:
    a, _ = schools
    gate = AuthorizationService(secured_database.app, None)
    with gate.reauthorize(
        Actor(actor_id=a.teacher), "offering.read", resource(a)
    ) as scope:
        with secured_database.admin.begin() as c:
            c.execute(
                text(
                    "UPDATE teacher_assignments SET active=false WHERE tenant_id=:tenant"
                ),
                {"tenant": a.tenant},
            )
        with pytest.raises(AccessError):
            scope.recheck("offering.read", resource(a))


def test_role_cannot_bypass_rls_or_modify_global_identity(
    secured_database: SecurityDatabase, schools: tuple[School, School]
) -> None:
    a, _ = schools
    for statement in (
        "ALTER TABLE sources DISABLE ROW LEVEL SECURITY",
        "TRUNCATE sources",
        "UPDATE users SET status='disabled'",
        "SET ROLE viora",
    ):
        with pytest.raises(DBAPIError), secured_database.app.sessions.begin() as s:
            bind_tenant(s, a.tenant)
            s.execute(text(statement))
    with pytest.raises(DBAPIError), secured_database.app.sessions.begin() as s:
        s.execute(text("SET LOCAL row_security=off"))
        s.execute(text("SELECT * FROM sources"))


def test_all_tenant_tables_deny_without_context(
    secured_database: SecurityDatabase,
) -> None:
    from viora.database_roles import TENANT_TABLES

    with secured_database.app.sessions.begin() as s:
        for table in (*TENANT_TABLES, "security_events"):
            assert s.scalar(text(f"SELECT count(*) FROM {table}")) == 0


def test_institution_admin_is_scoped(
    secured_database: SecurityDatabase, schools: tuple[School, School]
) -> None:
    a, _ = schools
    with secured_database.admin.begin() as c:
        c.execute(
            text(
                "UPDATE role_grants SET role='institution_admin' WHERE tenant_id=:tenant AND user_id=:owner"
            ),
            {"tenant": a.tenant, "owner": a.owner},
        )
    assert allowed(
        secured_database,
        a.owner,
        resource(a, "institution", a.tenant),
        "institution.read",
    )
    assert not allowed(secured_database, a.owner, resource(a), "offering.teach")


def test_real_authenticated_member_cannot_read_unscoped_source(
    secured_database: SecurityDatabase, schools: tuple[School, School]
) -> None:
    from fastapi.testclient import TestClient
    from viora.config import AuthSettings
    from viora.identity.public import AuthService, IdentityAttributeKind
    from viora.main import create_app

    a, _ = schools
    settings = Settings(
        database_url=SecretStr(
            secured_database.app.engine.url.render_as_string(hide_password=False)
        ),
        environment="test",
        auth=AuthSettings(signing_key=SecretStr("t" * 64)),
    )
    assert settings.auth is not None
    auth = AuthService(secured_database.app.sessions, settings.auth)
    password = SecretStr("test password for member")
    with secured_database.admin.begin() as c:
        c.execute(
            text(
                "INSERT INTO verified_identity_attributes VALUES (:user,'email',:email,now())"
            ),
            {"user": a.teacher, "email": f"{a.teacher}@example.test"},
        )
        c.execute(
            text("INSERT INTO password_credentials VALUES (:user,:hash)"),
            {"user": a.teacher, "hash": auth.passwords.hash(password)},
        )
    pair = auth.login(
        IdentityAttributeKind.email, f"{a.teacher}@example.test", password
    )
    with TestClient(create_app(settings, secured_database.app)) as client:
        response = client.get(
            f"/api/v1/tenants/{a.tenant}/sources/{a.offering}",
            headers={"Authorization": f"Bearer {pair.access_token.get_secret_value()}"},
        )
        assert response.status_code == 403
