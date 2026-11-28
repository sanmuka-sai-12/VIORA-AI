"""Live PostgreSQL integration tests for R4.4 student dashboard data and imported material revocation."""

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
from viora.ai.public import FakeAIGateway
from viora.config import AuthSettings, Settings
from viora.database_roles import grant_application_privileges
from viora.db import Database
from viora.identity.public import AuthService, IdentityAttributeKind, provision_account
from viora.institution.bootstrap import bootstrap_institution
from viora.main import create_app
from viora.storage.public import local_artifact_store

pytestmark = pytest.mark.postgres
PASSWORD = SecretStr("r44 integration passphrase 42")
ORIGIN = "https://app.viora.example"


@dataclass(frozen=True)
class R44Env:
    admin: Engine
    admin_db: Database
    app_db: Database
    app_url: str


@pytest.fixture(scope="module")
def env_r44() -> Iterator[R44Env]:
    raw = os.environ.get("VIORA_TEST_DATABASE_URL")
    if not raw:
        pytest.skip("Dedicated viora_test database required")
    url = make_url(raw)
    if url.database != "viora_test":
        pytest.fail("Requires dedicated viora_test database")
    schema, role = f"r44_{uuid4().hex}", f"r44_app_{uuid4().hex}"
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
        yield R44Env(admin, admin_db, app_db, app_url)
    finally:
        app_db.close()
        admin_db.close()
        with admin.begin() as conn:
            conn.execute(DropSchema(schema, cascade=True))
            conn.connection.driver_connection.execute(
                sql.SQL("DROP ROLE {}").format(sql.Identifier(role))
            )
        admin.dispose()


def _settings(env: R44Env) -> Settings:
    return Settings(
        database_url=SecretStr(env.app_url),
        environment="test",
        cors_origins=(ORIGIN,),
        auth=AuthSettings(signing_key=SecretStr("v" * 64)),
    )


def _login_headers(auth: AuthService, email: str) -> dict[str, str]:
    pair = auth.login(IdentityAttributeKind.email, email, PASSWORD)
    return {"Authorization": f"Bearer {pair.access_token.get_secret_value()}"}


def test_r4_4_dashboard_metadata_and_imported_material_revocation(
    env_r44: R44Env, tmp_path: Path
) -> None:
    cfg = _settings(env_r44)
    auth = AuthService(env_r44.admin_db.sessions, cfg.auth)  # type: ignore[arg-type]
    store = local_artifact_store(tmp_path)
    fake_ai = FakeAIGateway(
        default_completion="Semaphore explanation from imported notes."
    )

    owner = provision_account(
        env_r44.admin_db.sessions,
        email="owner@r44.edu",
        display_name="College Head",
        password=PASSWORD,
    )
    tenant = bootstrap_institution(
        env_r44.admin_db.sessions, name="R44 Institute", owner_user_id=owner.user_id
    ).tenant_id

    with TestClient(
        create_app(
            settings=cfg,
            database=env_r44.app_db,
            ai_gateway=fake_ai,
            artifact_store=store,
        )
    ) as client:
        hdr = _login_headers(auth, "owner@r44.edu")

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
            json={"name": "Computer Networks", "department_id": dept_id},
        ).json()["id"]
        off_id = client.post(
            f"/api/v1/tenants/{tenant}/offerings",
            headers=hdr,
            json={
                "name": "Computer Networks — CSE-A",
                "section_id": sec_id,
                "subject_id": subj_id,
            },
        ).json()["id"]

        homeroom_id = client.post(
            f"/api/v1/tenants/{tenant}/memberships/onboard",
            headers=hdr,
            json={
                "email": "homeroom@r44.edu",
                "display_name": "Prof. Homeroom Guide",
                "role": "class_coordinator",
                "initial_password": PASSWORD.get_secret_value(),
            },
        ).json()["member"]["user_id"]
        teacher_id = client.post(
            f"/api/v1/tenants/{tenant}/memberships/onboard",
            headers=hdr,
            json={
                "email": "teacher@r44.edu",
                "display_name": "Prof. Vint Cerf",
                "role": "subject_teacher",
                "initial_password": PASSWORD.get_secret_value(),
            },
        ).json()["member"]["user_id"]
        student_id = client.post(
            f"/api/v1/tenants/{tenant}/memberships/onboard",
            headers=hdr,
            json={
                "email": "student@r44.edu",
                "display_name": "Rohan Kulkarni",
                "role": "student",
                "initial_password": PASSWORD.get_secret_value(),
            },
        ).json()["member"]["user_id"]

        assert (
            client.patch(
                f"/api/v1/tenants/{tenant}/students/{student_id}/profile",
                headers=hdr,
                json={
                    "roll_number": "CS-042",
                    "department_id": dept_id,
                    "academic_year_id": year_id,
                    "semester_id": sem_id,
                    "section_id": sec_id,
                },
            ).status_code
            == 200
        )
        assert (
            client.post(
                f"/api/v1/tenants/{tenant}/sections/{sec_id}/coordinators",
                headers=hdr,
                json={"user_id": homeroom_id},
            ).status_code
            == 201
        )
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

        teacher_hdr = _login_headers(auth, "teacher@r44.edu")
        student_hdr = _login_headers(auth, "student@r44.edu")

        # 1. Student my-classrooms includes homeroom teacher & subject faculty names
        cls_res = client.get(
            f"/api/v1/tenants/{tenant}/my-classrooms", headers=student_hdr
        )
        assert cls_res.status_code == 200, cls_res.text
        cls_data = cls_res.json()
        assert cls_data["homeroom_teacher_names"] == ["Prof. Homeroom Guide"]
        assert cls_data["enrolled_offerings"][0]["teacher_names"] == ["Prof. Vint Cerf"]

        # 2. Teacher publishes study material, student imports into Personal Learning
        up_res = client.post(
            f"/api/v1/tenants/{tenant}/offerings/{off_id}/sources",
            headers=teacher_hdr,
            json={
                "title": "TCP Congestion Control",
                "input_format": "typed_notes",
                "content": "TCP uses AIMD and slow start to avoid network congestion.",
                "origin_kind": "teacher_resource",
                "visibility": "offering_published",
            },
        )
        assert up_res.status_code == 201, up_res.text
        src_id = up_res.json()["source"]["source_id"]

        imp_res = client.post(
            f"/api/v1/tenants/{tenant}/learning/sources/{src_id}/import",
            headers=student_hdr,
        )
        assert imp_res.status_code == 201, imp_res.text
        imported_att_id = imp_res.json()["attachment_id"]

        # Student also uploads a private note
        priv_res = client.post(
            f"/api/v1/tenants/{tenant}/learning/attachments/text",
            headers=student_hdr,
            json={
                "title": "My Private Notes",
                "content": "Private personal study summary for networking.",
            },
        )
        assert priv_res.status_code == 201, priv_res.text
        private_att_id = priv_res.json()["attachment_id"]

        # Both work before withdrawal (including within a persistent session)
        ask_ok = client.post(
            f"/api/v1/tenants/{tenant}/learning/ask",
            headers=student_hdr,
            json={
                "prompt": "Explain slow start",
                "mode": "question",
                "attachment_ids": [imported_att_id],
            },
        )
        assert ask_ok.status_code == 200, ask_ok.text
        imported_session_id = ask_ok.json()["session_id"]

        # 3. Teacher withdraws material -> reusing imported attachment or session context in new AI request is denied (403)
        assert (
            client.patch(
                f"/api/v1/tenants/{tenant}/sources/{src_id}",
                headers=teacher_hdr,
                json={"visibility": "teacher_only"},
            ).status_code
            == 200
        )
        ask_withdrawn = client.post(
            f"/api/v1/tenants/{tenant}/learning/ask",
            headers=student_hdr,
            json={
                "prompt": "Explain slow start again",
                "mode": "question",
                "attachment_ids": [imported_att_id],
            },
        )
        assert ask_withdrawn.status_code == 403
        assert ask_withdrawn.json()["code"] == "material_access_revoked"

        # Conversation context in session grounded in withdrawn material is also denied even with a private attachment
        ask_session_ctx = client.post(
            f"/api/v1/tenants/{tenant}/learning/ask",
            headers=student_hdr,
            json={
                "session_id": imported_session_id,
                "prompt": "Continue from our previous discussion",
                "mode": "question",
                "attachment_ids": [private_att_id],
            },
        )
        assert ask_session_ctx.status_code == 403
        assert ask_session_ctx.json()["code"] == "material_access_revoked"

        # Legacy import (origin_source_id IS NULL) is also caught via content_sha256 + title match
        with env_r44.admin.begin() as conn:
            conn.execute(
                text(
                    "UPDATE personal_learning_materials SET origin_source_id = NULL "
                    "WHERE attachment_id = :aid"
                ),
                {"aid": imported_att_id},
            )
        ask_legacy = client.post(
            f"/api/v1/tenants/{tenant}/learning/ask",
            headers=student_hdr,
            json={
                "prompt": "Explain slow start from legacy import",
                "mode": "question",
                "attachment_ids": [imported_att_id],
            },
        )
        assert ask_legacy.status_code == 403
        assert ask_legacy.json()["code"] == "material_access_revoked"

        gen_withdrawn = client.post(
            f"/api/v1/tenants/{tenant}/learning/resources/generate",
            headers=student_hdr,
            json={
                "tool_kind": "study_notes",
                "topic": "TCP",
                "attachment_ids": [imported_att_id],
            },
        )
        assert gen_withdrawn.status_code == 403
        assert gen_withdrawn.json()["code"] == "material_access_revoked"

        # 4. Re-publish, then withdraw student enrolment -> also denied (403)
        assert (
            client.patch(
                f"/api/v1/tenants/{tenant}/sources/{src_id}",
                headers=teacher_hdr,
                json={"visibility": "offering_published"},
            ).status_code
            == 200
        )
        assert (
            client.post(
                f"/api/v1/tenants/{tenant}/offerings/{off_id}/enrolments/{student_id}/withdraw",
                headers=hdr,
            ).status_code
            == 200
        )
        ask_unenrolled = client.post(
            f"/api/v1/tenants/{tenant}/learning/ask",
            headers=student_hdr,
            json={
                "prompt": "Explain slow start after unenrol",
                "mode": "question",
                "attachment_ids": [imported_att_id],
            },
        )
        assert ask_unenrolled.status_code == 403
        assert ask_unenrolled.json()["code"] == "material_access_revoked"

        # Student's own private material still works!
        ask_private = client.post(
            f"/api/v1/tenants/{tenant}/learning/ask",
            headers=student_hdr,
            json={
                "prompt": "Summarize my notes",
                "mode": "question",
                "attachment_ids": [private_att_id],
            },
        )
        assert ask_private.status_code == 200, ask_private.text
