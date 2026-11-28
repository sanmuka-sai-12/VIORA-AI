"""Live PostgreSQL integration tests for R4.3 subject-section classrooms, materials, announcements, and Personal Learning handoff."""

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
from viora.storage.public import local_artifact_store

pytestmark = pytest.mark.postgres
PASSWORD = SecretStr("r43 integration passphrase 42")
ORIGIN = "https://app.viora.example"


@dataclass(frozen=True)
class R43Env:
    admin: Engine
    admin_db: Database
    app_db: Database
    app_url: str


@pytest.fixture(scope="module")
def env_r43() -> Iterator[R43Env]:
    raw = os.environ.get("VIORA_TEST_DATABASE_URL")
    if not raw:
        pytest.skip("Dedicated viora_test database required")
    url = make_url(raw)
    if url.database != "viora_test":
        pytest.fail("Requires dedicated viora_test database")
    schema, role = f"r43_{uuid4().hex}", f"r43_app_{uuid4().hex}"
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
        yield R43Env(admin, admin_db, app_db, app_url)
    finally:
        app_db.close()
        admin_db.close()
        with admin.begin() as conn:
            conn.execute(DropSchema(schema, cascade=True))
            conn.connection.driver_connection.execute(
                sql.SQL("DROP ROLE {}").format(sql.Identifier(role))
            )
        admin.dispose()


def _settings(env: R43Env) -> Settings:
    return Settings(
        database_url=SecretStr(env.app_url),
        environment="test",
        cors_origins=(ORIGIN,),
        auth=AuthSettings(signing_key=SecretStr("u" * 64)),
    )


def _login_headers(auth: AuthService, email: str) -> dict[str, str]:
    pair = auth.login(IdentityAttributeKind.email, email, PASSWORD)
    return {"Authorization": f"Bearer {pair.access_token.get_secret_value()}"}


def test_r4_3_classrooms_materials_announcements_and_personal_learning_handoff(
    env_r43: R43Env, tmp_path: Path
) -> None:
    cfg = _settings(env_r43)
    auth = AuthService(env_r43.admin_db.sessions, cfg.auth)  # type: ignore[arg-type]
    store = local_artifact_store(tmp_path)

    owner = provision_account(
        env_r43.admin_db.sessions,
        email="owner@r43.edu",
        display_name="College Head",
        password=PASSWORD,
    )
    tenant = bootstrap_institution(
        env_r43.admin_db.sessions, name="R43 Institute", owner_user_id=owner.user_id
    ).tenant_id

    with TestClient(
        create_app(settings=cfg, database=env_r43.app_db, artifact_store=store)
    ) as client:
        hdr = _login_headers(auth, "owner@r43.edu")

        dept_id = client.post(
            f"/api/v1/tenants/{tenant}/departments",
            headers=hdr,
            json={"name": "Computer Science"},
        ).json()["id"]
        year_id = client.post(
            f"/api/v1/tenants/{tenant}/academic-years",
            headers=hdr,
            json={"name": "2026-2027"},
        ).json()["id"]
        sem_id = client.post(
            f"/api/v1/tenants/{tenant}/semesters",
            headers=hdr,
            json={"name": "Semester 1", "academic_year_id": year_id},
        ).json()["id"]
        sec_id = client.post(
            f"/api/v1/tenants/{tenant}/sections",
            headers=hdr,
            json={"name": "CSE-A", "semester_id": sem_id},
        ).json()["id"]
        subj_id = client.post(
            f"/api/v1/tenants/{tenant}/subjects",
            headers=hdr,
            json={"name": "Operating Systems", "department_id": dept_id},
        ).json()["id"]
        off_id = client.post(
            f"/api/v1/tenants/{tenant}/offerings",
            headers=hdr,
            json={
                "name": "Operating Systems — CSE-A",
                "section_id": sec_id,
                "subject_id": subj_id,
            },
        ).json()["id"]

        teacher_id = client.post(
            f"/api/v1/tenants/{tenant}/memberships/onboard",
            headers=hdr,
            json={
                "email": "teacher@r43.edu",
                "display_name": "Prof. Edsger Dijkstra",
                "role": "subject_teacher",
                "initial_password": PASSWORD.get_secret_value(),
            },
        ).json()["member"]["user_id"]
        student_id = client.post(
            f"/api/v1/tenants/{tenant}/memberships/onboard",
            headers=hdr,
            json={
                "email": "student@r43.edu",
                "display_name": "Ananya Verma",
                "role": "student",
                "initial_password": PASSWORD.get_secret_value(),
            },
        ).json()["member"]["user_id"]
        outsider_id = client.post(
            f"/api/v1/tenants/{tenant}/memberships/onboard",
            headers=hdr,
            json={
                "email": "outsider@r43.edu",
                "display_name": "Not Enrolled",
                "role": "student",
                "initial_password": PASSWORD.get_secret_value(),
            },
        ).json()["member"]["user_id"]

        assert (
            client.post(
                f"/api/v1/tenants/{tenant}/offerings/{off_id}/teachers",
                headers=hdr,
                json={"user_id": teacher_id},
            ).status_code
            == 201
        )
        assert (
            client.post(
                f"/api/v1/tenants/{tenant}/offerings/{off_id}/enrolments",
                headers=hdr,
                json={"user_id": student_id},
            ).status_code
            == 201
        )

        teacher_hdr = _login_headers(auth, "teacher@r43.edu")
        student_hdr = _login_headers(auth, "student@r43.edu")
        outsider_hdr = _login_headers(auth, "outsider@r43.edu")
        assert outsider_id

        # 1. Classroom discovery
        t_cls = client.get(
            f"/api/v1/tenants/{tenant}/my-classrooms", headers=teacher_hdr
        ).json()
        assert [c["offering_id"] for c in t_cls["teaching_offerings"]] == [off_id]
        s_cls = client.get(
            f"/api/v1/tenants/{tenant}/my-classrooms", headers=student_hdr
        ).json()
        assert [c["offering_id"] for c in s_cls["enrolled_offerings"]] == [off_id]

        # 2. Direct upload, organize, publish & download without AI extraction
        up_res = client.post(
            f"/api/v1/tenants/{tenant}/offerings/{off_id}/sources",
            headers=teacher_hdr,
            json={
                "title": "Semaphores and Mutexes",
                "input_format": "typed_notes",
                "content": "A semaphore controls access to a shared resource using counters.",
                "origin_kind": "teacher_resource",
                "visibility": "offering_published",
                "folder_label": "Unit 2: Concurrency",
            },
        )
        assert up_res.status_code == 201, up_res.text
        src_data = up_res.json()["source"]
        src_id = src_data["source_id"]
        assert src_data["folder_label"] == "Unit 2: Concurrency"
        assert src_data["visibility"] == "offering_published"

        # Enrolled student can download original material; non-enrolled student is denied
        dl_ok = client.get(
            f"/api/v1/tenants/{tenant}/sources/{src_id}/download", headers=student_hdr
        )
        assert dl_ok.status_code == 200
        assert b"semaphore controls access" in dl_ok.content
        assert (
            client.get(
                f"/api/v1/tenants/{tenant}/sources/{src_id}/download",
                headers=outsider_hdr,
            ).status_code
            == 403
        )

        # 3. Classroom announcements CRUD & student read-only enforcement
        ann_res = client.post(
            f"/api/v1/tenants/{tenant}/offerings/{off_id}/announcements",
            headers=teacher_hdr,
            json={
                "title": "Lab 3 Posted",
                "body": "Please review Unit 2 notes before Friday.",
                "pinned": True,
            },
        )
        assert ann_res.status_code == 201, ann_res.text
        ann_id = ann_res.json()["id"]

        assert (
            client.patch(
                f"/api/v1/tenants/{tenant}/offerings/{off_id}/announcements/{ann_id}",
                headers=teacher_hdr,
                json={"title": "Lab 3 Updated"},
            ).status_code
            == 200
        )

        s_anns = client.get(
            f"/api/v1/tenants/{tenant}/offerings/{off_id}/announcements",
            headers=student_hdr,
        )
        assert s_anns.status_code == 200
        assert s_anns.json()["items"][0]["title"] == "Lab 3 Updated"

        # Student cannot create/edit/delete announcements; outsider cannot list
        assert (
            client.post(
                f"/api/v1/tenants/{tenant}/offerings/{off_id}/announcements",
                headers=student_hdr,
                json={"title": "No", "body": "Denied"},
            ).status_code
            == 403
        )
        assert (
            client.get(
                f"/api/v1/tenants/{tenant}/offerings/{off_id}/announcements",
                headers=outsider_hdr,
            ).status_code
            == 403
        )

        # Teacher can delete announcement
        assert (
            client.delete(
                f"/api/v1/tenants/{tenant}/offerings/{off_id}/announcements/{ann_id}",
                headers=teacher_hdr,
            ).status_code
            == 204
        )

        # 4. Learn with VIORA AI imports published material into student's private Personal Learning
        imp_res = client.post(
            f"/api/v1/tenants/{tenant}/learning/sources/{src_id}/import",
            headers=student_hdr,
        )
        assert imp_res.status_code == 201, imp_res.text
        assert imp_res.json()["title"] == "Semaphores and Mutexes"

        # Outsider cannot import
        assert (
            client.post(
                f"/api/v1/tenants/{tenant}/learning/sources/{src_id}/import",
                headers=outsider_hdr,
            ).status_code
            == 403
        )

        # 5. Teacher withdraws material -> student download & Learn with VIORA AI immediately denied (403)
        assert (
            client.patch(
                f"/api/v1/tenants/{tenant}/sources/{src_id}",
                headers=teacher_hdr,
                json={"visibility": "teacher_only"},
            ).status_code
            == 200
        )
        assert (
            client.get(
                f"/api/v1/tenants/{tenant}/sources/{src_id}/download",
                headers=student_hdr,
            ).status_code
            == 403
        )
        assert (
            client.post(
                f"/api/v1/tenants/{tenant}/learning/sources/{src_id}/import",
                headers=student_hdr,
            ).status_code
            == 403
        )
