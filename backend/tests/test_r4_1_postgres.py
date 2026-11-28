"""Live PostgreSQL integration tests for R4.1 onboarding, invitations, and academic profiles."""

import os
import secrets
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from psycopg import sql
from pydantic import SecretStr
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.schema import CreateSchema, DropSchema
from viora.config import AuthSettings, Settings
from viora.database_roles import grant_application_privileges
from viora.db import Database
from viora.identity.public import AuthService, IdentityAttributeKind, provision_account
from viora.institution.bootstrap import bootstrap_institution
from viora.main import create_app

pytestmark = pytest.mark.postgres
PASSWORD = SecretStr("r41 integration passphrase 42")
ORIGIN = "https://app.viora.example"


@dataclass(frozen=True)
class R41Env:
    admin: Engine
    admin_db: Database
    app_db: Database
    app_url: str


@pytest.fixture(scope="module")
def env_r41() -> Iterator[R41Env]:
    raw = os.environ.get("VIORA_TEST_DATABASE_URL")
    if not raw:
        pytest.skip("Dedicated viora_test database required")
    url = make_url(raw)
    if url.database != "viora_test":
        pytest.fail("Requires dedicated viora_test database")
    schema, role = f"r41_{uuid4().hex}", f"r41_app_{uuid4().hex}"
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
        yield R41Env(admin, admin_db, app_db, app_url)
    finally:
        app_db.close()
        admin_db.close()
        with admin.begin() as conn:
            conn.execute(DropSchema(schema, cascade=True))
            conn.connection.driver_connection.execute(
                sql.SQL("DROP ROLE {}").format(sql.Identifier(role))
            )
        admin.dispose()


def _settings(env: R41Env) -> Settings:
    return Settings(
        database_url=SecretStr(env.app_url),
        environment="test",
        cors_origins=(ORIGIN,),
        auth=AuthSettings(signing_key=SecretStr("r" * 64)),
    )


def _login_headers(auth: AuthService, email: str, password: SecretStr = PASSWORD) -> dict[str, str]:
    pair = auth.login(IdentityAttributeKind.email, email, password)
    return {"Authorization": f"Bearer {pair.access_token.get_secret_value()}"}


def test_r4_1_onboarding_invitations_profiles_and_isolation(env_r41: R41Env) -> None:
    cfg = _settings(env_r41)
    auth = AuthService(env_r41.admin_db.sessions, cfg.auth)  # type: ignore[arg-type]

    owner_a = provision_account(
        env_r41.admin_db.sessions,
        email="owner-a@r41.edu",
        display_name="Owner A",
        password=PASSWORD,
    )
    owner_b = provision_account(
        env_r41.admin_db.sessions,
        email="owner-b@r41.edu",
        display_name="Owner B",
        password=PASSWORD,
    )
    tenant_a = bootstrap_institution(
        env_r41.admin_db.sessions, name="College A", owner_user_id=owner_a.user_id
    ).tenant_id
    bootstrap_institution(
        env_r41.admin_db.sessions, name="College B", owner_user_id=owner_b.user_id
    )

    with TestClient(create_app(settings=cfg, database=env_r41.app_db)) as client:
        headers_a = _login_headers(auth, "owner-a@r41.edu")
        headers_b = _login_headers(auth, "owner-b@r41.edu")

        # Create academic structure in College A
        dept_res = client.post(
            f"/api/v1/tenants/{tenant_a}/departments",
            headers=headers_a,
            json={"name": "Computer Science"},
        )
        assert dept_res.status_code == 201, dept_res.text
        dept_id = dept_res.json()["id"]

        year_res = client.post(
            f"/api/v1/tenants/{tenant_a}/academic-years",
            headers=headers_a,
            json={"name": "2026-2027"},
        )
        assert year_res.status_code == 201, year_res.text
        year_id = year_res.json()["id"]

        sem_res = client.post(
            f"/api/v1/tenants/{tenant_a}/semesters",
            headers=headers_a,
            json={"name": "Semester 1", "academic_year_id": year_id},
        )
        assert sem_res.status_code == 201, sem_res.text
        sem_id = sem_res.json()["id"]

        sec_res = client.post(
            f"/api/v1/tenants/{tenant_a}/sections",
            headers=headers_a,
            json={"name": "CSE-A", "semester_id": sem_id},
        )
        assert sec_res.status_code == 201, sec_res.text
        sec_id = sec_res.json()["id"]

        # 1. Onboard Student 1 with initial password
        stu1_res = client.post(
            f"/api/v1/tenants/{tenant_a}/memberships/onboard",
            headers=headers_a,
            json={
                "email": "student1@r41.edu",
                "display_name": "Aarav Sharma",
                "role": "student",
                "initial_password": PASSWORD.get_secret_value(),
            },
        )
        assert stu1_res.status_code == 201, stu1_res.text
        stu1_data = stu1_res.json()
        assert stu1_data["account_created"] is True
        assert stu1_data["invite_token"] is None
        stu1_id = stu1_data["member"]["user_id"]

        # Update Student 1 profile
        prof1_res = client.patch(
            f"/api/v1/tenants/{tenant_a}/students/{stu1_id}/profile",
            headers=headers_a,
            json={
                "roll_number": "CS2026-001",
                "department_id": dept_id,
                "academic_year_id": year_id,
                "semester_id": sem_id,
                "section_id": sec_id,
                "phone": "+919876543210",
                "contact_email": "aarav.contact@r41.edu",
                "guardian_contact": "+919876500000",
            },
        )
        assert prof1_res.status_code == 200, prof1_res.text
        assert prof1_res.json()["roll_number"] == "CS2026-001"
        assert prof1_res.json()["department_name"] == "Computer Science"
        assert prof1_res.json()["section_name"] == "CSE-A"

        # 2. Invite Student 2 (no password -> invite token -> accept-invite -> login)
        stu2_res = client.post(
            f"/api/v1/tenants/{tenant_a}/memberships/onboard",
            headers=headers_a,
            json={
                "email": "student2@r41.edu",
                "display_name": "Diya Patel",
                "role": "student",
            },
        )
        assert stu2_res.status_code == 201, stu2_res.text
        stu2_data = stu2_res.json()
        assert stu2_data["member"]["status"] == "invited"
        invite_token = stu2_data["invite_token"]
        assert invite_token is not None
        stu2_id = stu2_data["member"]["user_id"]

        # Accept invite via HTTP endpoint
        invited_pw = "new-invited-secret-123"
        accept_res = client.post(
            "/api/v1/auth/accept-invite",
            headers={"Origin": ORIGIN, "X-Viora-CSRF": "1"},
            json={"token": invite_token, "new_password": invited_pw},
        )
        assert accept_res.status_code == 204, accept_res.text

        # Re-using invite token fails
        reuse_res = client.post(
            "/api/v1/auth/accept-invite",
            headers={"Origin": ORIGIN, "X-Viora-CSRF": "1"},
            json={"token": invite_token, "new_password": invited_pw},
        )
        assert reuse_res.status_code == 400

        # Invited student can now log in and read their own profile
        stu2_headers = _login_headers(auth, "student2@r41.edu", SecretStr(invited_pw))
        own_prof = client.get(
            f"/api/v1/tenants/{tenant_a}/students/{stu2_id}",
            headers=stu2_headers,
        )
        assert own_prof.status_code == 200, own_prof.text
        assert own_prof.json()["membership_status"] == "active"

        # Student 2 cannot read Student 1 profile or list directory or edit own profile
        assert (
            client.get(
                f"/api/v1/tenants/{tenant_a}/students/{stu1_id}",
                headers=stu2_headers,
            ).status_code
            == 403
        )
        assert (
            client.get(
                f"/api/v1/tenants/{tenant_a}/students",
                headers=stu2_headers,
            ).status_code
            == 403
        )
        assert (
            client.patch(
                f"/api/v1/tenants/{tenant_a}/students/{stu2_id}/profile",
                headers=stu2_headers,
                json={"roll_number": "HACK-999"},
            ).status_code
            == 403
        )

        # 3. Searchable student directory
        dir_all = client.get(
            f"/api/v1/tenants/{tenant_a}/students",
            headers=headers_a,
        )
        assert dir_all.status_code == 200, dir_all.text
        assert len(dir_all.json()["items"]) == 2

        dir_search = client.get(
            f"/api/v1/tenants/{tenant_a}/students",
            headers=headers_a,
            params={"q": "CS2026-001", "department_id": dept_id},
        )
        assert dir_search.status_code == 200, dir_search.text
        items = dir_search.json()["items"]
        assert len(items) == 1
        assert items[0]["user_id"] == stu1_id

        # 4. Onboard Faculty, edit profile, search faculty directory
        fac_res = client.post(
            f"/api/v1/tenants/{tenant_a}/memberships/onboard",
            headers=headers_a,
            json={
                "email": "faculty1@r41.edu",
                "display_name": "Dr. Meera Nair",
                "role": "subject_teacher",
                "initial_password": PASSWORD.get_secret_value(),
            },
        )
        assert fac_res.status_code == 201, fac_res.text
        fac_id = fac_res.json()["member"]["user_id"]

        fac_prof_res = client.patch(
            f"/api/v1/tenants/{tenant_a}/faculty/{fac_id}/profile",
            headers=headers_a,
            json={
                "employee_code": "FAC-101",
                "department_id": dept_id,
                "designation": "Associate Professor",
                "office_location": "Block B-204",
            },
        )
        assert fac_prof_res.status_code == 200, fac_prof_res.text
        assert fac_prof_res.json()["employee_code"] == "FAC-101"

        fac_dir = client.get(
            f"/api/v1/tenants/{tenant_a}/faculty",
            headers=headers_a,
            params={"q": "FAC-101"},
        )
        assert fac_dir.status_code == 200, fac_dir.text
        assert len(fac_dir.json()["items"]) == 1

        # Faculty can read directory/profile but cannot edit faculty/student profiles
        fac_headers = _login_headers(auth, "faculty1@r41.edu")
        assert (
            client.get(
                f"/api/v1/tenants/{tenant_a}/students", headers=fac_headers
            ).status_code
            == 200
        )
        assert (
            client.patch(
                f"/api/v1/tenants/{tenant_a}/students/{stu1_id}/profile",
                headers=fac_headers,
                json={"roll_number": "NOPE"},
            ).status_code
            == 403
        )

        # 5. Cross-institution access denial
        assert (
            client.get(
                f"/api/v1/tenants/{tenant_a}/students", headers=headers_b
            ).status_code
            == 403
        )
        assert (
            client.get(
                f"/api/v1/tenants/{tenant_a}/students/{stu1_id}", headers=headers_b
            ).status_code
            == 403
        )
        assert (
            client.patch(
                f"/api/v1/tenants/{tenant_a}/students/{stu1_id}/profile",
                headers=headers_b,
                json={"roll_number": "CROSS-TENANT"},
            ).status_code
            == 403
        )
        assert (
            client.post(
                f"/api/v1/tenants/{tenant_a}/memberships/onboard",
                headers=headers_b,
                json={
                    "email": "cross@r41.edu",
                    "display_name": "Cross User",
                    "role": "student",
                },
            ).status_code
            == 403
        )
