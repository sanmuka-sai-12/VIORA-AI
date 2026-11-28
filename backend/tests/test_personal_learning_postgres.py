"""PostgreSQL + forced RLS + HTTP tests for R2.3 Personal Learning persistence & privacy."""

from __future__ import annotations

import os
import secrets
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
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
from viora.ai.public import FakeAIGateway
from viora.config import AuthSettings, Settings
from viora.database_roles import grant_application_privileges
from viora.db import Database
from viora.identity.public import AuthService, IdentityAttributeKind
from viora.knowledge.public import RawMindMapNode, RawMindMapPayload
from viora.main import create_app

pytestmark = pytest.mark.postgres
ROOT = Path(__file__).resolve().parents[2]
PASSWORD = SecretStr("personal learning pg integration passphrase 42")


@dataclass(frozen=True)
class _Actors:
    tenant: UUID
    student1: UUID
    student2: UUID
    teacher: UUID
    student1_email: str
    student1_token: str
    student2_token: str
    teacher_token: str


class _PgEnv:
    def __init__(self, tmp_path: Path) -> None:
        raw_admin = os.environ.get("VIORA_TEST_DATABASE_URL") or os.environ.get(
            "DATABASE_URL"
        )
        if not raw_admin:
            pytest.skip("PostgreSQL admin URL required for postgres integration test")
        self.schema = f"viora_pl_{secrets.token_hex(4)}"
        self.app_role = f"viora_pl_app_{secrets.token_hex(4)}"
        self.app_pass = secrets.token_urlsafe(16)
        self.admin = create_engine(raw_admin, future=True)
        with self.admin.begin() as c:
            c.execute(CreateSchema(self.schema))
            c.connection.driver_connection.execute(
                sql.SQL(
                    "CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE NOREPLICATION NOINHERIT"
                ).format(sql.Identifier(self.app_role), sql.Literal(self.app_pass))
            )
        migrate_url = make_url(raw_admin).update_query_dict(
            {"options": f"-csearch_path={self.schema}"}
        )
        app_url = make_url(raw_admin).update_query_dict(
            {"options": f"-csearch_path={self.schema},public"}
        )
        cfg = Config(str(ROOT / "alembic.ini"))
        cfg.set_main_option("script_location", str(ROOT / "backend" / "migrations"))
        os.environ["MIGRATION_DATABASE_URL"] = migrate_url.render_as_string(
            hide_password=False
        )
        try:
            command.upgrade(cfg, "head")
        finally:
            os.environ.pop("MIGRATION_DATABASE_URL", None)

        with self.admin.begin() as c:
            grant_application_privileges(c, self.app_role, self.schema)
        app_url = app_url.set(username=self.app_role, password=self.app_pass)
        self.settings = Settings(
            database_url=SecretStr(app_url.render_as_string(hide_password=False)),
            auth=AuthSettings(
                signing_key=SecretStr("personal-learning-signing-key-32-bytes!"),
                cookie_secure=False,
            ),
            environment="test",
            media_storage_dir=str(tmp_path / "storage"),
            embedding_provider="fake",
        )
        self.app_db = Database(self.settings)

    def close(self) -> None:
        self.app_db.close()
        with self.admin.begin() as c:
            c.execute(DropSchema(self.schema, cascade=True, if_exists=True))
            c.connection.driver_connection.execute(
                sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(self.app_role))
            )
        self.admin.dispose()


@pytest.fixture
def pg_env(tmp_path: Path) -> Iterator[_PgEnv]:
    env = _PgEnv(tmp_path)
    try:
        yield env
    finally:
        env.close()


def _hdr(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _seed_actors(
    admin: Engine, schema: str, db: Database, settings: Settings, slug: str
) -> _Actors:
    auth = AuthService(db.sessions, settings.auth)  # type: ignore[arg-type]
    tenant_id = uuid4()
    s1, s2, teacher = uuid4(), uuid4(), uuid4()
    pw_hash = auth.passwords.hash(PASSWORD)
    emails = {
        s1: f"{slug}-s1-{s1}@uni.edu",
        s2: f"{slug}-s2-{s2}@uni.edu",
        teacher: f"{slug}-tch-{teacher}@uni.edu",
    }
    with admin.begin() as c:
        c.execute(text(f"SET LOCAL search_path TO {schema}, public"))
        c.execute(
            text("INSERT INTO institutions VALUES (:t, :n, true)"),
            {"t": tenant_id, "n": slug.upper()},
        )
        for uid, role in (
            (s1, "student"),
            (s2, "student"),
            (teacher, "subject_teacher"),
        ):
            c.execute(
                text("INSERT INTO users VALUES (:u, :n, 'active')"),
                {"u": uid, "n": role},
            )
            c.execute(
                text("INSERT INTO memberships VALUES (:t, :u, 'active')"),
                {"t": tenant_id, "u": uid},
            )
            c.execute(
                text(
                    "INSERT INTO role_grants (tenant_id, user_id, role) VALUES (:t, :u, :r)"
                ),
                {"t": tenant_id, "u": uid, "r": role},
            )
            c.execute(
                text(
                    "INSERT INTO verified_identity_attributes VALUES (:u, 'email', :e, now())"
                ),
                {"u": uid, "e": emails[uid]},
            )
            c.execute(
                text("INSERT INTO password_credentials VALUES (:u, :h)"),
                {"u": uid, "h": pw_hash},
            )

    return _Actors(
        tenant=tenant_id,
        student1=s1,
        student2=s2,
        teacher=teacher,
        student1_email=emails[s1],
        student1_token=auth.login(
            IdentityAttributeKind.email, emails[s1], PASSWORD
        ).access_token.get_secret_value(),
        student2_token=auth.login(
            IdentityAttributeKind.email, emails[s2], PASSWORD
        ).access_token.get_secret_value(),
        teacher_token=auth.login(
            IdentityAttributeKind.email, emails[teacher], PASSWORD
        ).access_token.get_secret_value(),
    )


def test_personal_learning_persistence_library_resume_rename_delete_and_privacy(
    pg_env: _PgEnv,
) -> None:
    fake_gw = FakeAIGateway(
        default_completion="The chain rule differentiates outer then inner functions (Calculus Sheet, p. 1).",
        structured_payloads={
            RawMindMapPayload: RawMindMapPayload(
                title="Chain Rule Map",
                nodes=[
                    RawMindMapNode(
                        id="n1",
                        parent_id=None,
                        label="Chain Rule",
                        explanation="Differentiates composite functions f(g(x)).",
                        key_formula="\\frac{d}{dx}f(g(x)) = f'(g(x))g'(x)",
                    ),
                    RawMindMapNode(
                        id="n2",
                        parent_id="n1",
                        label="Outer Derivative",
                        relation="step 1",
                        explanation="Differentiate outer function f keeping g(x) intact.",
                    ),
                    RawMindMapNode(
                        id="n3",
                        parent_id="n1",
                        label="Inner Derivative",
                        relation="step 2",
                        explanation="Multiply by g'(x).",
                    ),
                ],
            )
        },
    )
    app = create_app(settings=pg_env.settings, database=pg_env.app_db, ai_gateway=fake_gw)
    actors = _seed_actors(
        pg_env.admin, pg_env.schema, pg_env.app_db, pg_env.settings, "pl"
    )

    try:
        minimal_pdf = (
            b"%PDF-1.4\n"
            b"1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\n"
            b"2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj\n"
            b"3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 300 200] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >> endobj\n"
            b"4 0 obj << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> endobj\n"
            b"5 0 obj << /Length 58 >> stream\n"
            b"BT /F1 12 Tf 24 120 Td (Chain Rule derivative f'(g(x))) Tj ET\n"
            b"endstream\nendobj\n"
            b"xref\n0 6\n0000000000 65535 f \n0000000009 00000 n \n0000000058 00000 n \n0000000115 00000 n \n0000000241 00000 n \n0000000311 00000 n \n"
            b"trailer << /Size 6 /Root 1 0 R >>\nstartxref\n421\n%%EOF\n"
        )

        with TestClient(app) as client:
            # 1) Student 1 uploads a private PDF (saved automatically in Personal Library)
            up = client.post(
                f"/api/v1/tenants/{actors.tenant}/learning/attachments/file",
                headers=_hdr(actors.student1_token),
                files={"file": ("calculus.pdf", minimal_pdf, "application/pdf")},
                data={"title": "Calculus Sheet"},
            )
            assert up.status_code == 201
            att = up.json()

            # 2) Student 1 starts a conversation with that attachment -> session persisted automatically
            ask1 = client.post(
                f"/api/v1/tenants/{actors.tenant}/learning/ask",
                headers=_hdr(actors.student1_token),
                json={
                    "prompt": "Explain the chain rule from my sheet",
                    "mode": "explain",
                    "attachment_ids": [att["attachment_id"]],
                },
            )
            assert ask1.status_code == 200
            r1 = ask1.json()
            sid = r1["session_id"]
            assert sid is not None
            assert r1["sources"][0]["title"] == "Calculus Sheet"

            # 3) Student 1 sends follow-up using persisted session_id (no explicit history needed)
            ask2 = client.post(
                f"/api/v1/tenants/{actors.tenant}/learning/ask",
                headers=_hdr(actors.student1_token),
                json={
                    "prompt": "Show one worked example",
                    "mode": "question",
                    "session_id": sid,
                    "attachment_ids": [att["attachment_id"]],
                },
            )
            assert ask2.status_code == 200
            assert ask2.json()["session_id"] == sid
            assert len(fake_gw.recorded_calls[-1]["messages"]) >= 4

            # 4) Rename session
            ren = client.patch(
                f"/api/v1/tenants/{actors.tenant}/learning/sessions/{sid}",
                headers=_hdr(actors.student1_token),
                json={"title": "Calculus Chain Rule Review"},
            )
            assert ren.status_code == 200
            assert ren.json()["title"] == "Calculus Chain Rule Review"
            assert len(ren.json()["messages"]) == 4

            # 5) Privacy & forced RLS isolation: Student 2 & Teacher see ZERO sessions/library & get 404
            for other_hdr in (_hdr(actors.student2_token), _hdr(actors.teacher_token)):
                assert (
                    client.get(
                        f"/api/v1/tenants/{actors.tenant}/learning/sessions",
                        headers=other_hdr,
                    ).json()["items"]
                    == []
                )
                assert (
                    client.get(
                        f"/api/v1/tenants/{actors.tenant}/learning/materials",
                        headers=other_hdr,
                    ).json()["items"]
                    == []
                )
                assert (
                    client.get(
                        f"/api/v1/tenants/{actors.tenant}/learning/sessions/{sid}",
                        headers=other_hdr,
                    ).status_code
                    == 404
                )
                assert (
                    client.delete(
                        f"/api/v1/tenants/{actors.tenant}/learning/sessions/{sid}",
                        headers=other_hdr,
                    ).status_code
                    == 404
                )
                assert (
                    client.delete(
                        f"/api/v1/tenants/{actors.tenant}/learning/materials/{att['attachment_id']}",
                        headers=other_hdr,
                    ).status_code
                    == 404
                )

            # 6) Simulate fresh browser session / re-login -> resumes exact conversation & library!
            auth2 = AuthService(pg_env.app_db.sessions, pg_env.settings.auth)  # type: ignore[arg-type]
            relogin_tok = auth2.login(
                IdentityAttributeKind.email, actors.student1_email, PASSWORD
            ).access_token.get_secret_value()
            with TestClient(app) as fresh_client:
                sess_list = fresh_client.get(
                    f"/api/v1/tenants/{actors.tenant}/learning/sessions",
                    headers=_hdr(relogin_tok),
                ).json()["items"]
                assert len(sess_list) == 1
                assert sess_list[0]["title"] == "Calculus Chain Rule Review"
                assert sess_list[0]["message_count"] == 4

                detail = fresh_client.get(
                    f"/api/v1/tenants/{actors.tenant}/learning/sessions/{sid}",
                    headers=_hdr(relogin_tok),
                ).json()
                assert len(detail["messages"]) == 4
                assert len(detail["attachments"]) == 1

                # 6b) R3.1 Study Notes & Academic Insights explicit generation, persistence, source refs, regenerate, privacy, and delete
                notes_res = fresh_client.post(
                    f"/api/v1/tenants/{actors.tenant}/learning/resources/generate",
                    headers=_hdr(relogin_tok),
                    json={
                        "tool_kind": "study_notes",
                        "session_id": sid,
                        "topic": "Chain Rule",
                        "include_conversation": True,
                        "attachment_ids": [att["attachment_id"]],
                    },
                )
                assert notes_res.status_code == 200
                note_item = notes_res.json()
                assert note_item["session_id"] == sid
                assert note_item["tool_kind"] == "study_notes"
                assert note_item["source_scope"] == "combined"
                assert len(note_item["sources"]) >= 1
                assert note_item["sources"][0]["title"] == "Calculus Sheet"

                # Regenerate in place
                regen_res = fresh_client.post(
                    f"/api/v1/tenants/{actors.tenant}/learning/resources/generate",
                    headers=_hdr(relogin_tok),
                    json={
                        "tool_kind": "academic_insights",
                        "session_id": sid,
                        "topic": "Chain Rule Pitfalls",
                        "replace_resource_id": note_item["resource_id"],
                    },
                )
                assert regen_res.status_code == 200
                assert regen_res.json()["resource_id"] == note_item["resource_id"]
                assert regen_res.json()["tool_kind"] == "academic_insights"

                # Persisted on reopened session detail & list endpoint
                reopened = fresh_client.get(
                    f"/api/v1/tenants/{actors.tenant}/learning/sessions/{sid}",
                    headers=_hdr(relogin_tok),
                ).json()
                assert len(reopened["resources"]) == 1
                assert reopened["resources"][0]["title"] == "Academic Insights: Chain Rule Pitfalls"

                # Student-private isolation on resources
                assert (
                    fresh_client.get(
                        f"/api/v1/tenants/{actors.tenant}/learning/sessions/{sid}/resources",
                        headers=_hdr(actors.student2_token),
                    ).status_code
                    == 404
                )
                assert (
                    fresh_client.delete(
                        f"/api/v1/tenants/{actors.tenant}/learning/resources/{note_item['resource_id']}",
                        headers=_hdr(actors.student2_token),
                    ).status_code
                    == 404
                )

                # Delete study resource
                assert (
                    fresh_client.delete(
                        f"/api/v1/tenants/{actors.tenant}/learning/resources/{note_item['resource_id']}",
                        headers=_hdr(relogin_tok),
                    ).status_code
                    == 200
                )
                assert (
                    fresh_client.get(
                        f"/api/v1/tenants/{actors.tenant}/learning/sessions/{sid}/resources",
                        headers=_hdr(relogin_tok),
                    ).json()["items"]
                    == []
                )

                # 6c) R3.2 Mind Map explicit generation, structured graph persistence, source refs, reopen & delete
                mm_res = fresh_client.post(
                    f"/api/v1/tenants/{actors.tenant}/learning/resources/generate",
                    headers=_hdr(relogin_tok),
                    json={
                        "tool_kind": "mind_map",
                        "session_id": sid,
                        "topic": "Chain Rule",
                        "attachment_ids": [att["attachment_id"]],
                    },
                )
                assert mm_res.status_code == 200
                mm_item = mm_res.json()
                assert mm_item["tool_kind"] == "mind_map"
                assert mm_item["mind_map"]["root_id"] == "n1"
                assert len(mm_item["mind_map"]["nodes"]) == 3
                assert len(mm_item["sources"]) >= 1

                reopened_mm = fresh_client.get(
                    f"/api/v1/tenants/{actors.tenant}/learning/sessions/{sid}",
                    headers=_hdr(relogin_tok),
                ).json()
                assert len(reopened_mm["resources"]) == 1
                assert reopened_mm["resources"][0]["mind_map"]["root_id"] == "n1"

                assert (
                    fresh_client.delete(
                        f"/api/v1/tenants/{actors.tenant}/learning/resources/{mm_item['resource_id']}",
                        headers=_hdr(relogin_tok),
                    ).status_code
                    == 200
                )

                # 7) Delete material detaches it from session; delete session removes it from history
                assert (
                    fresh_client.delete(
                        f"/api/v1/tenants/{actors.tenant}/learning/materials/{att['attachment_id']}",
                        headers=_hdr(relogin_tok),
                    ).status_code
                    == 200
                )
                after_mat = fresh_client.get(
                    f"/api/v1/tenants/{actors.tenant}/learning/sessions/{sid}",
                    headers=_hdr(relogin_tok),
                ).json()
                assert after_mat["attachments"] == []

                assert (
                    fresh_client.delete(
                        f"/api/v1/tenants/{actors.tenant}/learning/sessions/{sid}",
                        headers=_hdr(relogin_tok),
                    ).status_code
                    == 200
                )
                assert (
                    fresh_client.get(
                        f"/api/v1/tenants/{actors.tenant}/learning/sessions",
                        headers=_hdr(relogin_tok),
                    ).json()["items"]
                    == []
                )
    finally:
        pg_env.app_db.close()
