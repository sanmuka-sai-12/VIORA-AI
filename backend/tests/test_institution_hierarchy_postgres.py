"""Live PostgreSQL integration tests for Steps 4.4 & 4.5: bootstrap, institution & academic hierarchy APIs."""

import os
import secrets
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from threading import Barrier
from unittest.mock import patch
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from psycopg import sql
from pydantic import SecretStr
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.schema import CreateSchema, DropSchema
from viora.authorization.public import AuthorizationService
from viora.config import AuthSettings, Settings
from viora.contracts import Actor, ResourceRef
from viora.database_roles import grant_application_privileges
from viora.db import Database
from viora.identity.public import (
    AccountStatus,
    AuthService,
    IdentityAttributeKind,
    provision_account,
)
from viora.institution.bootstrap import bootstrap_institution
from viora.main import create_app
from viora.security import AccessError

pytestmark = pytest.mark.postgres
PASSWORD = SecretStr("hierarchy test passphrase 42")


@dataclass(frozen=True)
class SecurityEnv:
    admin: Engine
    admin_db: Database
    app_db: Database
    app_url: str
    schema: str
    role: str


@pytest.fixture(scope="module")
def security_env() -> Iterator[SecurityEnv]:
    raw = os.environ.get("VIORA_TEST_DATABASE_URL")
    if not raw:
        pytest.skip("Dedicated viora_test database required")
    url = make_url(raw)
    if url.database != "viora_test":
        pytest.fail("Requires dedicated viora_test database")
    schema, role = f"hier_{uuid4().hex}", f"hier_app_{uuid4().hex}"
    password = secrets.token_urlsafe(32)
    scoped = url.update_query_dict({"options": f"-csearch_path={schema}"})
    scoped_str = scoped.render_as_string(hide_password=False)
    app_url = scoped.set(username=role, password=password).render_as_string(
        hide_password=False
    )
    admin = create_engine(scoped)
    admin_db = Database(Settings(database_url=SecretStr(scoped_str)))
    app_db = Database(Settings(database_url=SecretStr(app_url)))
    with admin.begin() as conn:
        conn.execute(CreateSchema(schema))
        conn.connection.driver_connection.execute(
            sql.SQL(
                "CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE NOINHERIT"
            ).format(sql.Identifier(role), sql.Literal(password))
        )
    try:
        with pytest.MonkeyPatch.context() as mp:
            mp.setenv("DATABASE_URL", scoped_str)
            mp.setenv("MIGRATION_DATABASE_URL", scoped_str)
            cfg = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
            command.upgrade(cfg, "head")
            command.check(cfg)
        with admin.begin() as conn:
            conn.execute(text("REVOKE CREATE ON SCHEMA public FROM PUBLIC"))
            grant_application_privileges(conn, role, schema)
        yield SecurityEnv(admin, admin_db, app_db, app_url, schema, role)
    finally:
        app_db.close()
        admin_db.close()
        with admin.begin() as conn:
            conn.execute(DropSchema(schema, cascade=True))
            conn.connection.driver_connection.execute(
                sql.SQL("DROP ROLE {}").format(sql.Identifier(role))
            )
        admin.dispose()


def _make_settings(env: SecurityEnv) -> Settings:
    return Settings(
        database_url=SecretStr(env.app_url),
        environment="test",
        cors_origins=("https://app.viora.example",),
        auth=AuthSettings(signing_key=SecretStr("z" * 64)),
    )


def _create_verified_user(
    env: SecurityEnv, *, status: AccountStatus = AccountStatus.active, verified: bool = True
) -> tuple[UUID, str]:
    uid = uuid4()
    email = f"u-{uid}@example.test"
    if verified:
        summary = provision_account(
            env.admin_db.sessions,
            email=email,
            display_name="Academic User",
            password=PASSWORD,
            status=status,
            user_id=uid,
        )
        return summary.user_id, email
    with env.admin.begin() as conn:
        conn.execute(
            text("INSERT INTO users (user_id, display_name, status) VALUES (:u, 'Unverified', :s)"),
            {"u": uid, "s": status.value},
        )
    return uid, email


def _bearer(env: SecurityEnv, settings: Settings, email: str) -> str:
    assert settings.auth is not None
    auth = AuthService(env.app_db.sessions, settings.auth)
    pair = auth.login(IdentityAttributeKind.email, email, PASSWORD)
    return pair.access_token.get_secret_value()


def _assign_member(
    env: SecurityEnv, tenant_id: UUID, user_id: UUID, role: str
) -> None:
    with env.admin.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO memberships (tenant_id, user_id, status) VALUES (:t, :u, 'active') "
                "ON CONFLICT (tenant_id, user_id) DO UPDATE SET status='active'"
            ),
            {"t": tenant_id, "u": user_id},
        )
        conn.execute(
            text(
                "INSERT INTO role_grants (tenant_id, user_id, role) VALUES (:t, :u, :r)"
            ),
            {"t": tenant_id, "u": user_id, "r": role},
        )


def test_institution_bootstrap_success_and_atomic_rollback(
    security_env: SecurityEnv,
) -> None:
    owner_id, owner_email = _create_verified_user(security_env)
    unverified_id, _ = _create_verified_user(security_env, verified=False)
    pending_id, _ = _create_verified_user(
        security_env, status=AccountStatus.pending, verified=True
    )

    # Rejects unverified, pending, unknown owners, and blank names cleanly
    for bad_owner, bad_name in [
        (unverified_id, "School A"),
        (pending_id, "School A"),
        (uuid4(), "School A"),
        (owner_id, "   "),
    ]:
        bad_tid = uuid4()
        with pytest.raises(ValueError):
            bootstrap_institution(
                security_env.admin_db.sessions,
                name=bad_name,
                owner_user_id=bad_owner,
                tenant_id=bad_tid,
            )
        with security_env.admin.connect() as conn:
            assert (
                conn.scalar(
                    text("SELECT count(*) FROM institutions WHERE tenant_id=:t"),
                    {"t": bad_tid},
                )
                == 0
            )

    # Injected audit failure rolls back institution, membership, and role atomically
    fail_tid = uuid4()
    with (
        patch(
            "viora.institution.bootstrap.change_audit",
            side_effect=RuntimeError("audit write failed"),
        ),
        pytest.raises(RuntimeError, match="audit write failed"),
    ):
        bootstrap_institution(
            security_env.admin_db.sessions,
            name="Rolled Back Academy",
            owner_user_id=owner_id,
            tenant_id=fail_tid,
        )
    with security_env.admin.connect() as conn:
        for tbl in ("institutions", "memberships", "role_grants", "security_events"):
            assert (
                conn.scalar(
                    text(f"SELECT count(*) FROM {tbl} WHERE tenant_id=:t"),
                    {"t": fail_tid},
                )
                == 0
            )

    # Successful bootstrap activates institution and writes change audit atomically
    info = bootstrap_institution(
        security_env.admin_db.sessions,
        name="Northbridge Institute",
        owner_user_id=owner_id,
    )
    assert info.active is True
    assert info.name == "Northbridge Institute"
    with security_env.admin.connect() as conn:
        assert (
            conn.scalar(
                text(
                    "SELECT count(*) FROM security_events WHERE tenant_id=:t AND event_kind='change' AND action='institution.bootstrap' AND allowed"
                ),
                {"t": info.tenant_id},
            )
            == 1
        )

    # Owner can log in and read/update institution metadata via HTTP
    settings = _make_settings(security_env)
    tok = _bearer(security_env, settings, owner_email)
    with TestClient(create_app(settings, security_env.app_db)) as client:
        got = client.get(
            f"/api/v1/institutions/{info.tenant_id}",
            headers={"Authorization": f"Bearer {tok}"},
        )
        assert got.status_code == 200
        assert got.json()["name"] == "Northbridge Institute"

        patched = client.patch(
            f"/api/v1/institutions/{info.tenant_id}",
            headers={"Authorization": f"Bearer {tok}"},
            json={"name": "Northbridge University"},
        )
        assert patched.status_code == 200
        assert patched.json()["name"] == "Northbridge University"


def test_academic_hierarchy_crud_lifecycle_uniqueness_and_isolation(
    security_env: SecurityEnv,
) -> None:
    owner_a, email_a = _create_verified_user(security_env)
    owner_b, email_b = _create_verified_user(security_env)
    school_a = bootstrap_institution(
        security_env.admin_db.sessions, name="Alpha College", owner_user_id=owner_a
    )
    school_b = bootstrap_institution(
        security_env.admin_db.sessions, name="Beta College", owner_user_id=owner_b
    )

    settings = _make_settings(security_env)
    tok_a = _bearer(security_env, settings, email_a)
    tok_b = _bearer(security_env, settings, email_b)
    hdr_a = {"Authorization": f"Bearer {tok_a}"}
    hdr_b = {"Authorization": f"Bearer {tok_b}"}

    with TestClient(create_app(settings, security_env.app_db)) as client:
        # 1. Create full hierarchy in School A
        campus_a = client.post(
            f"/api/v1/tenants/{school_a.tenant_id}/campuses",
            headers=hdr_a,
            json={"name": "Main Campus"},
        )
        assert campus_a.status_code == 201
        campus_a_id = campus_a.json()["id"]

        # Create second campus for pagination testing
        campus_a2 = client.post(
            f"/api/v1/tenants/{school_a.tenant_id}/campuses",
            headers=hdr_a,
            json={"name": "Annex Campus"},
        )
        assert campus_a2.status_code == 201

        # Paginated list
        page1 = client.get(
            f"/api/v1/tenants/{school_a.tenant_id}/campuses?limit=1",
            headers=hdr_a,
        )
        assert page1.status_code == 200
        assert len(page1.json()["items"]) == 1
        assert page1.json()["next_cursor"] is not None
        page2 = client.get(
            f"/api/v1/tenants/{school_a.tenant_id}/campuses?limit=1&cursor={page1.json()['next_cursor']}",
            headers=hdr_a,
        )
        assert page2.status_code == 200
        assert len(page2.json()["items"]) == 1
        assert page2.json()["items"][0]["id"] != page1.json()["items"][0]["id"]

        # Duplicate campus name rejected (case/whitespace insensitive)
        dup_campus = client.post(
            f"/api/v1/tenants/{school_a.tenant_id}/campuses",
            headers=hdr_a,
            json={"name": "  main campus "},
        )
        assert dup_campus.status_code == 409
        assert dup_campus.json()["code"] == "duplicate_record"

        # Department under campus
        dept_a = client.post(
            f"/api/v1/tenants/{school_a.tenant_id}/departments",
            headers=hdr_a,
            json={"name": "Computer Science", "campus_id": campus_a_id},
        )
        assert dept_a.status_code == 201
        dept_a_id = dept_a.json()["id"]

        # Programme & Subject under department
        prog_a = client.post(
            f"/api/v1/tenants/{school_a.tenant_id}/programmes",
            headers=hdr_a,
            json={"name": "BSc Computing", "department_id": dept_a_id},
        )
        assert prog_a.status_code == 201
        prog_a_id = prog_a.json()["id"]

        subj_a = client.post(
            f"/api/v1/tenants/{school_a.tenant_id}/subjects",
            headers=hdr_a,
            json={"name": "Algorithms", "department_id": dept_a_id},
        )
        assert subj_a.status_code == 201
        subj_a_id = subj_a.json()["id"]

        # Academic Year & Semester
        year_a = client.post(
            f"/api/v1/tenants/{school_a.tenant_id}/academic-years",
            headers=hdr_a,
            json={"name": "2026-2027"},
        )
        assert year_a.status_code == 201
        year_a_id = year_a.json()["id"]

        sem_a = client.post(
            f"/api/v1/tenants/{school_a.tenant_id}/semesters",
            headers=hdr_a,
            json={"name": "Term 1", "academic_year_id": year_a_id},
        )
        assert sem_a.status_code == 201
        sem_a_id = sem_a.json()["id"]

        # Section & Subject Offering
        sec_a = client.post(
            f"/api/v1/tenants/{school_a.tenant_id}/sections",
            headers=hdr_a,
            json={
                "name": "Section A",
                "semester_id": sem_a_id,
                "programme_id": prog_a_id,
            },
        )
        assert sec_a.status_code == 201
        sec_a_id = sec_a.json()["id"]

        off_a = client.post(
            f"/api/v1/tenants/{school_a.tenant_id}/offerings",
            headers=hdr_a,
            json={
                "name": "Algorithms - Sec A",
                "section_id": sec_a_id,
                "subject_id": subj_a_id,
            },
        )
        assert off_a.status_code == 201
        off_a_id = off_a.json()["id"]

        # Duplicate offering rejected
        dup_off = client.post(
            f"/api/v1/tenants/{school_a.tenant_id}/offerings",
            headers=hdr_a,
            json={
                "name": "algorithms - sec a",
                "section_id": sec_a_id,
                "subject_id": subj_a_id,
            },
        )
        assert dup_off.status_code == 409

        # Owner can read structural catalogue via readOffering, update offering,
        # but is STILL denied offering.read & offering.teach
        assert (
            client.get(
                f"/api/v1/tenants/{school_a.tenant_id}/offerings/{off_a_id}",
                headers=hdr_a,
            ).status_code
            == 200
        )
        gate = AuthorizationService(security_env.app_db, None)
        with (
            pytest.raises(AccessError) as denied_teach,
            gate.reauthorize(
                Actor(actor_id=owner_a),
                "offering.teach",
                ResourceRef(
                    tenant_id=school_a.tenant_id,
                    kind="offering",
                    resource_id=off_a_id,
                ),
            ),
        ):
            pass
        assert denied_teach.value.status == 403

        # 2. Cross-tenant isolation: School B owner cannot read/mutate School A or reference School A parents
        assert (
            client.get(
                f"/api/v1/institutions/{school_a.tenant_id}", headers=hdr_b
            ).status_code
            == 403
        )
        assert (
            client.get(
                f"/api/v1/tenants/{school_a.tenant_id}/campuses/{campus_a_id}",
                headers=hdr_b,
            ).status_code
            == 403
        )
        cross_dept = client.post(
            f"/api/v1/tenants/{school_b.tenant_id}/departments",
            headers=hdr_b,
            json={"name": "Cross Tenant Dept", "campus_id": campus_a_id},
        )
        assert cross_dept.status_code == 403

        # School B creating campus with same name "Main Campus" succeeds independently
        campus_b = client.post(
            f"/api/v1/tenants/{school_b.tenant_id}/campuses",
            headers=hdr_b,
            json={"name": "Main Campus"},
        )
        assert campus_b.status_code == 201

        # 3. Lifecycle referential integrity: cannot archive parent while active children exist
        assert (
            client.post(
                f"/api/v1/tenants/{school_a.tenant_id}/campuses/{campus_a_id}/lifecycle",
                headers=hdr_a,
                json={"active": False},
            ).status_code
            == 409
        )
        assert (
            client.post(
                f"/api/v1/tenants/{school_a.tenant_id}/sections/{sec_a_id}/lifecycle",
                headers=hdr_a,
                json={"active": False},
            ).status_code
            == 409
        )

        # Archive leaf offering -> then section -> cannot reactivate offering while section archived
        assert (
            client.post(
                f"/api/v1/tenants/{school_a.tenant_id}/offerings/{off_a_id}/lifecycle",
                headers=hdr_a,
                json={"active": False},
            ).json()["active"]
            is False
        )
        assert (
            client.post(
                f"/api/v1/tenants/{school_a.tenant_id}/sections/{sec_a_id}/lifecycle",
                headers=hdr_a,
                json={"active": False},
            ).json()["active"]
            is False
        )
        assert (
            client.post(
                f"/api/v1/tenants/{school_a.tenant_id}/offerings/{off_a_id}/lifecycle",
                headers=hdr_a,
                json={"active": True},
            ).status_code
            == 409
        )
        # Cannot create offering under archived section
        assert (
            client.post(
                f"/api/v1/tenants/{school_a.tenant_id}/offerings",
                headers=hdr_a,
                json={
                    "name": "Blocked Offering",
                    "section_id": sec_a_id,
                    "subject_id": subj_a_id,
                },
            ).status_code
            == 403
        )

        # Patch department clearing campus_id (explicit null) & renaming
        patched_dept = client.patch(
            f"/api/v1/tenants/{school_a.tenant_id}/departments/{dept_a_id}",
            headers=hdr_a,
            json={"name": "Computing Sciences", "campus_id": None},
        )
        assert patched_dept.status_code == 200
        assert patched_dept.json()["name"] == "Computing Sciences"
        assert patched_dept.json()["campus_id"] is None

        # Now campus_a has no active departments and can be archived cleanly
        archived_campus = client.post(
            f"/api/v1/tenants/{school_a.tenant_id}/campuses/{campus_a_id}/lifecycle",
            headers=hdr_a,
            json={"active": False},
        )
        assert archived_campus.status_code == 200
        assert archived_campus.json()["active"] is False


def test_role_scoped_lists_mutations_revocation_and_audit_rollback(
    security_env: SecurityEnv,
) -> None:
    owner_id, owner_email = _create_verified_user(security_env)
    head_id, head_email = _create_verified_user(security_env)
    coord_id, coord_email = _create_verified_user(security_env)
    teacher_id, teacher_email = _create_verified_user(security_env)
    student_id, student_email = _create_verified_user(security_env)
    guardian_id, guardian_email = _create_verified_user(security_env)

    school = bootstrap_institution(
        security_env.admin_db.sessions, name="Scoped Academy", owner_user_id=owner_id
    )
    tid = school.tenant_id
    for uid, role in [
        (head_id, "department_head"),
        (coord_id, "class_coordinator"),
        (teacher_id, "subject_teacher"),
        (student_id, "student"),
        (guardian_id, "guardian"),
    ]:
        _assign_member(security_env, tid, uid, role)

    settings = _make_settings(security_env)
    owner_hdr = {"Authorization": f"Bearer {_bearer(security_env, settings, owner_email)}"}
    head_hdr = {"Authorization": f"Bearer {_bearer(security_env, settings, head_email)}"}
    coord_hdr = {"Authorization": f"Bearer {_bearer(security_env, settings, coord_email)}"}
    teacher_hdr = {"Authorization": f"Bearer {_bearer(security_env, settings, teacher_email)}"}
    student_hdr = {"Authorization": f"Bearer {_bearer(security_env, settings, student_email)}"}
    guardian_hdr = {"Authorization": f"Bearer {_bearer(security_env, settings, guardian_email)}"}

    with TestClient(create_app(settings, security_env.app_db)) as client:
        d1 = client.post(
            f"/api/v1/tenants/{tid}/departments", headers=owner_hdr, json={"name": "Math"}
        ).json()["id"]
        d2 = client.post(
            f"/api/v1/tenants/{tid}/departments", headers=owner_hdr, json={"name": "History"}
        ).json()["id"]
        p1 = client.post(
            f"/api/v1/tenants/{tid}/programmes",
            headers=owner_hdr,
            json={"name": "BSc Math", "department_id": d1},
        ).json()["id"]
        p2 = client.post(
            f"/api/v1/tenants/{tid}/programmes",
            headers=owner_hdr,
            json={"name": "BA History", "department_id": d2},
        ).json()["id"]
        s1 = client.post(
            f"/api/v1/tenants/{tid}/subjects",
            headers=owner_hdr,
            json={"name": "Calculus", "department_id": d1},
        ).json()["id"]
        s2 = client.post(
            f"/api/v1/tenants/{tid}/subjects",
            headers=owner_hdr,
            json={"name": "World History", "department_id": d2},
        ).json()["id"]
        yr = client.post(
            f"/api/v1/tenants/{tid}/academic-years", headers=owner_hdr, json={"name": "2026"}
        ).json()["id"]
        sem = client.post(
            f"/api/v1/tenants/{tid}/semesters",
            headers=owner_hdr,
            json={"name": "Fall", "academic_year_id": yr},
        ).json()["id"]
        sec1 = client.post(
            f"/api/v1/tenants/{tid}/sections",
            headers=owner_hdr,
            json={"name": "Sec 1", "semester_id": sem, "programme_id": p1},
        ).json()["id"]
        sec2 = client.post(
            f"/api/v1/tenants/{tid}/sections",
            headers=owner_hdr,
            json={"name": "Sec 2", "semester_id": sem, "programme_id": p2},
        ).json()["id"]
        off1 = client.post(
            f"/api/v1/tenants/{tid}/offerings",
            headers=owner_hdr,
            json={"name": "Calc Sec 1", "section_id": sec1, "subject_id": s1},
        ).json()["id"]
        off2 = client.post(
            f"/api/v1/tenants/{tid}/offerings",
            headers=owner_hdr,
            json={"name": "Hist Sec 2", "section_id": sec2, "subject_id": s2},
        ).json()["id"]

        with security_env.admin.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO department_heads (tenant_id, user_id, department_id, active) VALUES (:t, :u, :d, true)"
                ),
                {"t": tid, "u": head_id, "d": d1},
            )
            conn.execute(
                text(
                    "INSERT INTO class_coordinators (tenant_id, user_id, section_id, active) VALUES (:t, :u, :s, true)"
                ),
                {"t": tid, "u": coord_id, "s": sec1},
            )
            conn.execute(
                text(
                    "INSERT INTO teacher_assignments (tenant_id, user_id, offering_id, active) VALUES (:t, :u, :o, true)"
                ),
                {"t": tid, "u": teacher_id, "o": off1},
            )
            conn.execute(
                text(
                    "INSERT INTO student_enrolments (tenant_id, user_id, offering_id, active) VALUES (:t, :u, :o, true)"
                ),
                {"t": tid, "u": student_id, "o": off1},
            )

        # Department head sees only d1/p1/s1, can update d1 name, denied on d2, denied creation/lifecycle
        assert [x["id"] for x in client.get(f"/api/v1/tenants/{tid}/departments", headers=head_hdr).json()["items"]] == [d1]
        assert [x["id"] for x in client.get(f"/api/v1/tenants/{tid}/programmes", headers=head_hdr).json()["items"]] == [p1]
        assert [x["id"] for x in client.get(f"/api/v1/tenants/{tid}/subjects", headers=head_hdr).json()["items"]] == [s1]
        assert client.get(f"/api/v1/tenants/{tid}/departments/{d2}", headers=head_hdr).status_code == 403
        assert client.patch(f"/api/v1/tenants/{tid}/departments/{d1}", headers=head_hdr, json={"name": "Pure Math"}).status_code == 200
        assert client.patch(f"/api/v1/tenants/{tid}/departments/{d1}", headers=head_hdr, json={"campus_id": None}).status_code == 403
        assert client.post(f"/api/v1/tenants/{tid}/programmes", headers=head_hdr, json={"name": "Denied", "department_id": d1}).status_code == 403
        assert client.get(f"/api/v1/tenants/{tid}/campuses", headers=head_hdr).status_code == 403

        # Coordinator sees only sec1/off1, can update sec1 name, denied on sec2 and mutations
        assert [x["id"] for x in client.get(f"/api/v1/tenants/{tid}/sections", headers=coord_hdr).json()["items"]] == [sec1]
        assert [x["id"] for x in client.get(f"/api/v1/tenants/{tid}/offerings", headers=coord_hdr).json()["items"]] == [off1]
        assert client.get(f"/api/v1/tenants/{tid}/sections/{sec2}", headers=coord_hdr).status_code == 403
        assert client.patch(f"/api/v1/tenants/{tid}/sections/{sec1}", headers=coord_hdr, json={"name": "Sec 1A"}).status_code == 200
        assert client.patch(f"/api/v1/tenants/{tid}/sections/{sec1}", headers=coord_hdr, json={"programme_id": None}).status_code == 403
        assert client.post(f"/api/v1/tenants/{tid}/offerings", headers=coord_hdr, json={"name": "Blocked", "section_id": sec1, "subject_id": s1}).status_code == 403

        # Teacher & student see only off1; guardian denied all hierarchy routes
        assert [x["id"] for x in client.get(f"/api/v1/tenants/{tid}/offerings", headers=teacher_hdr).json()["items"]] == [off1]
        assert [x["id"] for x in client.get(f"/api/v1/tenants/{tid}/offerings", headers=student_hdr).json()["items"]] == [off1]
        assert client.get(f"/api/v1/tenants/{tid}/offerings/{off2}", headers=teacher_hdr).status_code == 403
        assert client.get(f"/api/v1/tenants/{tid}/offerings/{off2}", headers=student_hdr).status_code == 403
        assert client.get(f"/api/v1/tenants/{tid}/offerings", headers=guardian_hdr).status_code == 403
        assert client.post(f"/api/v1/tenants/{tid}/campuses", headers=teacher_hdr, json={"name": "Forbidden"}).status_code == 403
        assert client.post(f"/api/v1/tenants/{tid}/campuses", headers=student_hdr, json={"name": "Forbidden"}).status_code == 403

        # Duplicate failure rolls back both entity and change audit atomically
        with security_env.admin.connect() as conn:
            changes_before = conn.scalar(
                text(
                    "SELECT count(*) FROM security_events WHERE tenant_id=:t AND event_kind='change' AND action='department.create'"
                ),
                {"t": tid},
            )
        dup_resp = client.post(
            f"/api/v1/tenants/{tid}/departments",
            headers=owner_hdr,
            json={"name": "Pure Math"},
        )
        assert dup_resp.status_code == 409
        with security_env.admin.connect() as conn:
            changes_after = conn.scalar(
                text(
                    "SELECT count(*) FROM security_events WHERE tenant_id=:t AND event_kind='change' AND action='department.create'"
                ),
                {"t": tid},
            )
            assert changes_after == changes_before
            # Denied probe recorded durable allowed=False audit rows
            assert (
                conn.scalar(
                    text(
                        "SELECT count(*) FROM security_events WHERE tenant_id=:t AND NOT allowed"
                    ),
                    {"t": tid},
                )
                >= 1
            )

        # Revoking membership takes effect immediately on next request
        with security_env.admin.begin() as conn:
            conn.execute(
                text(
                    "UPDATE memberships SET status='revoked' WHERE tenant_id=:t AND user_id=:u"
                ),
                {"t": tid, "u": head_id},
            )
        assert (
            client.get(
                f"/api/v1/tenants/{tid}/departments", headers=head_hdr
            ).status_code
            == 403
        )


def test_remaining_entity_updates_and_bootstrap_cli_guard(
    security_env: SecurityEnv,
) -> None:
    from viora.institution.bootstrap import main as bootstrap_cli

    with pytest.MonkeyPatch.context() as mp:
        mp.delenv("BOOTSTRAP_DATABASE_URL", raising=False)
        mp.delenv("MIGRATION_DATABASE_URL", raising=False)
        assert (
            bootstrap_cli(["--name", "No Env", "--owner-user-id", str(uuid4())]) == 1
        )

    owner_id, owner_email = _create_verified_user(security_env)
    school = bootstrap_institution(
        security_env.admin_db.sessions, name="Full Lifecycle", owner_user_id=owner_id
    )
    tid = school.tenant_id
    settings = _make_settings(security_env)
    hdr = {"Authorization": f"Bearer {_bearer(security_env, settings, owner_email)}"}

    with TestClient(create_app(settings, security_env.app_db)) as client:
        c = client.post(
            f"/api/v1/tenants/{tid}/campuses", headers=hdr, json={"name": "C1"}
        ).json()["id"]
        assert (
            client.patch(
                f"/api/v1/tenants/{tid}/campuses/{c}", headers=hdr, json={"name": "C1 Renamed"}
            ).json()["name"]
            == "C1 Renamed"
        )
        d = client.post(
            f"/api/v1/tenants/{tid}/departments",
            headers=hdr,
            json={"name": "D1", "campus_id": c},
        ).json()["id"]
        p = client.post(
            f"/api/v1/tenants/{tid}/programmes",
            headers=hdr,
            json={"name": "P1", "department_id": d},
        ).json()["id"]
        assert (
            client.patch(
                f"/api/v1/tenants/{tid}/programmes/{p}",
                headers=hdr,
                json={"name": "P1 Renamed", "department_id": None},
            ).json()["department_id"]
            is None
        )
        s = client.post(
            f"/api/v1/tenants/{tid}/subjects",
            headers=hdr,
            json={"name": "Sub1", "department_id": d},
        ).json()["id"]
        assert (
            client.patch(
                f"/api/v1/tenants/{tid}/subjects/{s}",
                headers=hdr,
                json={"name": "Sub1 Renamed", "department_id": None},
            ).json()["department_id"]
            is None
        )
        y1 = client.post(
            f"/api/v1/tenants/{tid}/academic-years", headers=hdr, json={"name": "Y1"}
        ).json()["id"]
        y2 = client.post(
            f"/api/v1/tenants/{tid}/academic-years", headers=hdr, json={"name": "Y2"}
        ).json()["id"]
        assert (
            client.patch(
                f"/api/v1/tenants/{tid}/academic-years/{y1}",
                headers=hdr,
                json={"name": "Y1 Renamed"},
            ).json()["name"]
            == "Y1 Renamed"
        )
        sem = client.post(
            f"/api/v1/tenants/{tid}/semesters",
            headers=hdr,
            json={"name": "Sem1", "academic_year_id": y1},
        ).json()["id"]
        assert (
            client.patch(
                f"/api/v1/tenants/{tid}/semesters/{sem}",
                headers=hdr,
                json={"name": "Sem1 Renamed", "academic_year_id": y2},
            ).json()["academic_year_id"]
            == y2
        )
        sec = client.post(
            f"/api/v1/tenants/{tid}/sections",
            headers=hdr,
            json={"name": "Sec1", "semester_id": sem, "programme_id": p},
        ).json()["id"]
        off = client.post(
            f"/api/v1/tenants/{tid}/offerings",
            headers=hdr,
            json={"name": "Off1", "section_id": sec, "subject_id": s},
        ).json()["id"]
        assert (
            client.patch(
                f"/api/v1/tenants/{tid}/offerings/{off}",
                headers=hdr,
                json={"name": "Off1 Renamed"},
            ).json()["name"]
            == "Off1 Renamed"
        )
        # Archive from leaf up to roots cleanly
        for kind, eid in [
            ("offerings", off),
            ("sections", sec),
            ("semesters", sem),
            ("academic-years", y1),
            ("academic-years", y2),
            ("programmes", p),
            ("subjects", s),
            ("departments", d),
            ("campuses", c),
        ]:
            resp = client.post(
                f"/api/v1/tenants/{tid}/{kind}/{eid}/lifecycle",
                headers=hdr,
                json={"active": False},
            )
            assert resp.status_code == 200
            assert resp.json()["active"] is False


def test_archived_resources_blocked_for_assignments_and_concurrent_offering_dedup(
    security_env: SecurityEnv,
) -> None:
    owner_id, owner_email = _create_verified_user(security_env)
    head_id, head_email = _create_verified_user(security_env)
    coord_id, coord_email = _create_verified_user(security_env)
    teacher_id, teacher_email = _create_verified_user(security_env)
    student_id, student_email = _create_verified_user(security_env)

    school = bootstrap_institution(
        security_env.admin_db.sessions, name="Archive Guard", owner_user_id=owner_id
    )
    tid = school.tenant_id
    for uid, role in [
        (head_id, "department_head"),
        (coord_id, "class_coordinator"),
        (teacher_id, "subject_teacher"),
        (student_id, "student"),
    ]:
        _assign_member(security_env, tid, uid, role)

    settings = _make_settings(security_env)
    owner_hdr = {"Authorization": f"Bearer {_bearer(security_env, settings, owner_email)}"}
    head_hdr = {"Authorization": f"Bearer {_bearer(security_env, settings, head_email)}"}
    coord_hdr = {"Authorization": f"Bearer {_bearer(security_env, settings, coord_email)}"}
    teacher_hdr = {"Authorization": f"Bearer {_bearer(security_env, settings, teacher_email)}"}
    student_hdr = {"Authorization": f"Bearer {_bearer(security_env, settings, student_email)}"}

    with TestClient(create_app(settings, security_env.app_db)) as client:
        d = client.post(
            f"/api/v1/tenants/{tid}/departments", headers=owner_hdr, json={"name": "Bio"}
        ).json()["id"]
        p = client.post(
            f"/api/v1/tenants/{tid}/programmes",
            headers=owner_hdr,
            json={"name": "BSc Bio", "department_id": d},
        ).json()["id"]
        s = client.post(
            f"/api/v1/tenants/{tid}/subjects",
            headers=owner_hdr,
            json={"name": "Genetics", "department_id": d},
        ).json()["id"]
        yr = client.post(
            f"/api/v1/tenants/{tid}/academic-years", headers=owner_hdr, json={"name": "2027"}
        ).json()["id"]
        sem = client.post(
            f"/api/v1/tenants/{tid}/semesters",
            headers=owner_hdr,
            json={"name": "Spring", "academic_year_id": yr},
        ).json()["id"]
        sec = client.post(
            f"/api/v1/tenants/{tid}/sections",
            headers=owner_hdr,
            json={"name": "Bio-A", "semester_id": sem, "programme_id": p},
        ).json()["id"]

        # Concurrent duplicate offering creation yields one 201 and one 409
        barrier = Barrier(2)

        def create_concurrent() -> int:
            with TestClient(create_app(settings, security_env.app_db)) as c:
                barrier.wait(timeout=5)
                r = c.post(
                    f"/api/v1/tenants/{tid}/offerings",
                    headers=owner_hdr,
                    json={"name": "Genetics A", "section_id": sec, "subject_id": s},
                )
                return r.status_code

        with ThreadPoolExecutor(max_workers=2) as pool:
            statuses = sorted(
                fut.result()
                for fut in [pool.submit(create_concurrent), pool.submit(create_concurrent)]
            )
        assert statuses == [201, 409]
        off = client.get(
            f"/api/v1/tenants/{tid}/offerings", headers=owner_hdr
        ).json()["items"][0]["id"]

        with security_env.admin.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO department_heads (tenant_id, user_id, department_id, active) VALUES (:t, :u, :d, true)"
                ),
                {"t": tid, "u": head_id, "d": d},
            )
            conn.execute(
                text(
                    "INSERT INTO class_coordinators (tenant_id, user_id, section_id, active) VALUES (:t, :u, :s, true)"
                ),
                {"t": tid, "u": coord_id, "s": sec},
            )
            conn.execute(
                text(
                    "INSERT INTO teacher_assignments (tenant_id, user_id, offering_id, active) VALUES (:t, :u, :o, true)"
                ),
                {"t": tid, "u": teacher_id, "o": off},
            )
            conn.execute(
                text(
                    "INSERT INTO student_enrolments (tenant_id, user_id, offering_id, active) VALUES (:t, :u, :o, true)"
                ),
                {"t": tid, "u": student_id, "o": off},
            )

        # Archive offering: teacher, student, coordinator lose access even with active assignments
        assert (
            client.post(
                f"/api/v1/tenants/{tid}/offerings/{off}/lifecycle",
                headers=owner_hdr,
                json={"active": False},
            ).status_code
            == 200
        )
        for hdr in (teacher_hdr, student_hdr, coord_hdr):
            assert (
                client.get(
                    f"/api/v1/tenants/{tid}/offerings/{off}", headers=hdr
                ).status_code
                == 403
            )
            assert (
                client.get(
                    f"/api/v1/tenants/{tid}/offerings", headers=hdr
                ).json()["items"]
                == []
            )

        # Archive section -> coordinator loses access & list entry
        assert (
            client.post(
                f"/api/v1/tenants/{tid}/sections/{sec}/lifecycle",
                headers=owner_hdr,
                json={"active": False},
            ).status_code
            == 200
        )
        assert (
            client.get(
                f"/api/v1/tenants/{tid}/sections/{sec}", headers=coord_hdr
            ).status_code
            == 403
        )
        assert (
            client.get(
                f"/api/v1/tenants/{tid}/sections", headers=coord_hdr
            ).json()["items"]
            == []
        )

        # Archive programme & subject -> head loses access to archived programme/subject
        assert (
            client.post(
                f"/api/v1/tenants/{tid}/programmes/{p}/lifecycle",
                headers=owner_hdr,
                json={"active": False},
            ).status_code
            == 200
        )
        assert (
            client.post(
                f"/api/v1/tenants/{tid}/subjects/{s}/lifecycle",
                headers=owner_hdr,
                json={"active": False},
            ).status_code
            == 200
        )
        assert (
            client.get(
                f"/api/v1/tenants/{tid}/programmes/{p}", headers=head_hdr
            ).status_code
            == 403
        )
        assert (
            client.get(
                f"/api/v1/tenants/{tid}/subjects/{s}", headers=head_hdr
            ).status_code
            == 403
        )
        assert (
            client.get(
                f"/api/v1/tenants/{tid}/programmes", headers=head_hdr
            ).json()["items"]
            == []
        )
        assert (
            client.get(
                f"/api/v1/tenants/{tid}/subjects", headers=head_hdr
            ).json()["items"]
            == []
        )

        # Archive department -> head loses department access & list entry
        assert (
            client.post(
                f"/api/v1/tenants/{tid}/departments/{d}/lifecycle",
                headers=owner_hdr,
                json={"active": False},
            ).status_code
            == 200
        )
        assert (
            client.get(
                f"/api/v1/tenants/{tid}/departments/{d}", headers=head_hdr
            ).status_code
            == 403
        )
        assert (
            client.get(
                f"/api/v1/tenants/{tid}/departments", headers=head_hdr
            ).json()["items"]
            == []
        )
