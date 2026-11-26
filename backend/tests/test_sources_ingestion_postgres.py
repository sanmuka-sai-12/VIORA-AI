"""Live PostgreSQL and HTTP end-to-end integration tests for Batch 2.2 source ingestion."""

import io
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
from pypdf import PdfWriter
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.schema import CreateSchema, DropSchema
from viora.authorization.public import AuthorizationService
from viora.config import AuthSettings, Settings
from viora.database_roles import grant_application_privileges
from viora.db import Database
from viora.identity.public import AuthService, IdentityAttributeKind
from viora.main import create_app
from viora.perf.public import build_synthetic_corpus
from viora.security import AccessError
from viora.sources.repository import SqlSourceRepository
from viora.sources.schemas import clamp_source_limits
from viora.sources.service import SourceIngestionService
from viora.storage.public import StorageConfigurationError, local_artifact_store

pytestmark = pytest.mark.postgres
ROOT = Path(__file__).resolve().parents[2]
PASSWORD = SecretStr("sources integration passphrase 42")


@dataclass(frozen=True)
class TenantActors:
    tenant: UUID
    offering: UUID
    other_offering: UUID
    owner: UUID
    teacher: UUID
    other_teacher: UUID
    co_teacher: UUID
    student: UUID
    other_student: UUID
    owner_token: str
    teacher_token: str
    co_teacher_token: str
    other_teacher_token: str
    student_token: str
    other_student_token: str


@dataclass(frozen=True)
class SourceTestEnv:
    admin: Engine
    app_db: Database
    settings: Settings
    storage_dir: Path
    a: TenantActors
    b: TenantActors


def _provision_school(
    conn, auth: AuthService, name: str
) -> TenantActors:
    ids = {
        key: uuid4()
        for key in (
            "tenant",
            "dept",
            "year",
            "sem",
            "sec",
            "subj",
            "offering",
            "other_offering",
            "owner",
            "teacher",
            "co_teacher",
            "other_teacher",
            "student",
            "other_student",
        )
    }
    conn.execute(
        text("INSERT INTO institutions VALUES (:tenant, :name, true)"),
        {**ids, "name": name},
    )
    role_map = [
        ("owner", "institution_owner"),
        ("teacher", "subject_teacher"),
        ("co_teacher", "subject_teacher"),
        ("other_teacher", "subject_teacher"),
        ("student", "student"),
        ("other_student", "student"),
    ]
    pw_hash = auth.passwords.hash(PASSWORD)
    emails: dict[str, str] = {}
    for key, role in role_map:
        uid = ids[key]
        email = f"{key}-{uid}@school.test"
        emails[key] = email
        conn.execute(
            text("INSERT INTO users VALUES (:u, :name, 'active')"),
            {"u": uid, "name": f"{key} user"},
        )
        conn.execute(
            text("INSERT INTO memberships VALUES (:t, :u, 'active')"),
            {"t": ids["tenant"], "u": uid},
        )
        conn.execute(
            text("INSERT INTO role_grants (tenant_id, user_id, role) VALUES (:t, :u, :r)"),
            {"t": ids["tenant"], "u": uid, "r": role},
        )
        conn.execute(
            text(
                "INSERT INTO verified_identity_attributes VALUES (:u, 'email', :email, now())"
            ),
            {"u": uid, "email": email},
        )
        conn.execute(
            text("INSERT INTO password_credentials VALUES (:u, :hash)"),
            {"u": uid, "hash": pw_hash},
        )

    conn.execute(
        text("INSERT INTO departments (tenant_id, id, name) VALUES (:tenant, :dept, 'CS')"),
        ids,
    )
    conn.execute(
        text("INSERT INTO academic_years (tenant_id, id, name) VALUES (:tenant, :year, '2026')"),
        ids,
    )
    conn.execute(
        text("INSERT INTO semesters (tenant_id, id, name, academic_year_id) VALUES (:tenant, :sem, 'Fall', :year)"),
        ids,
    )
    conn.execute(
        text("INSERT INTO sections (tenant_id, id, name, semester_id) VALUES (:tenant, :sec, 'Sec-A', :sem)"),
        ids,
    )
    conn.execute(
        text("INSERT INTO subjects (tenant_id, id, name, department_id) VALUES (:tenant, :subj, 'Physics', :dept)"),
        ids,
    )
    for key in ("offering", "other_offering"):
        conn.execute(
            text(
                "INSERT INTO subject_offerings (tenant_id, id, name, section_id, subject_id) VALUES (:tenant, :oid, :oname, :sec, :subj)"
            ),
            {**ids, "oid": ids[key], "oname": f"Offering-{key}"},
        )

    # Assigned teachers & enrolled students on `offering`, separate teacher on `other_offering`
    conn.execute(
        text("INSERT INTO teacher_assignments VALUES (:tenant, :teacher, :offering, true)"),
        ids,
    )
    conn.execute(
        text("INSERT INTO teacher_assignments VALUES (:tenant, :co_teacher, :offering, true)"),
        ids,
    )
    conn.execute(
        text("INSERT INTO teacher_assignments VALUES (:tenant, :other_teacher, :other_offering, true)"),
        ids,
    )
    conn.execute(
        text("INSERT INTO student_enrolments VALUES (:tenant, :student, :offering, true)"),
        ids,
    )
    conn.execute(
        text("INSERT INTO student_enrolments VALUES (:tenant, :other_student, :offering, true)"),
        ids,
    )

    return TenantActors(
        tenant=ids["tenant"],
        offering=ids["offering"],
        other_offering=ids["other_offering"],
        owner=ids["owner"],
        teacher=ids["teacher"],
        other_teacher=ids["other_teacher"],
        co_teacher=ids["co_teacher"],
        student=ids["student"],
        other_student=ids["other_student"],
        owner_token=emails["owner"],
        teacher_token=emails["teacher"],
        co_teacher_token=emails["co_teacher"],
        other_teacher_token=emails["other_teacher"],
        student_token=emails["student"],
        other_student_token=emails["other_student"],
    )


@pytest.fixture()
def env(tmp_path: Path) -> Iterator[SourceTestEnv]:
    raw = os.environ.get("VIORA_TEST_DATABASE_URL")
    if not raw:
        pytest.skip("Dedicated viora_test database required")
    url = make_url(raw)
    if url.database != "viora_test":
        pytest.fail("Requires dedicated viora_test database")

    schema = f"src_{uuid4().hex}"
    role = f"src_app_{uuid4().hex}"
    pw = secrets.token_urlsafe(32)
    scoped = url.update_query_dict({"options": f"-csearch_path={schema}"})
    scoped_str = scoped.render_as_string(hide_password=False)
    app_url = scoped.set(username=role, password=pw).render_as_string(
        hide_password=False
    )

    admin = create_engine(scoped)
    storage_dir = tmp_path / "private_media"
    settings = Settings(
        database_url=SecretStr(app_url),
        environment="test",
        media_storage_dir=str(storage_dir),
        source_max_bytes=512 * 1024,
        source_max_pdf_pages=20,
        source_max_text_chars=50_000,
        auth=AuthSettings(signing_key=SecretStr("s" * 64)),
    )
    app_db = Database(settings)

    with admin.begin() as conn:
        conn.execute(CreateSchema(schema))
        conn.connection.driver_connection.execute(
            sql.SQL(
                "CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE NOINHERIT"
            ).format(sql.Identifier(role), sql.Literal(pw))
        )
    try:
        with pytest.MonkeyPatch.context() as mp:
            mp.setenv("DATABASE_URL", scoped_str)
            mp.setenv("MIGRATION_DATABASE_URL", scoped_str)
            cfg = Config(str(ROOT / "alembic.ini"))
            command.upgrade(cfg, "head")
            command.check(cfg)
        with admin.begin() as conn:
            grant_application_privileges(conn, role, schema)

        auth = AuthService(app_db.sessions, settings.auth)  # type: ignore[arg-type]
        with admin.begin() as conn:
            a_raw = _provision_school(conn, auth, "Alpha Academy")
            b_raw = _provision_school(conn, auth, "Beta Institute")

        def login(email: str) -> str:
            return auth.login(
                IdentityAttributeKind.email, email, PASSWORD
            ).access_token.get_secret_value()

        a = TenantActors(
            **{
                **a_raw.__dict__,
                "owner_token": login(a_raw.owner_token),
                "teacher_token": login(a_raw.teacher_token),
                "co_teacher_token": login(a_raw.co_teacher_token),
                "other_teacher_token": login(a_raw.other_teacher_token),
                "student_token": login(a_raw.student_token),
                "other_student_token": login(a_raw.other_student_token),
            }
        )
        b = TenantActors(
            **{
                **b_raw.__dict__,
                "owner_token": login(b_raw.owner_token),
                "teacher_token": login(b_raw.teacher_token),
                "co_teacher_token": login(b_raw.co_teacher_token),
                "other_teacher_token": login(b_raw.other_teacher_token),
                "student_token": login(b_raw.student_token),
                "other_student_token": login(b_raw.other_student_token),
            }
        )
        yield SourceTestEnv(admin, app_db, settings, storage_dir, a, b)
    finally:
        app_db.close()
        with admin.begin() as conn:
            conn.execute(DropSchema(schema, cascade=True))
            conn.connection.driver_connection.execute(
                sql.SQL("DROP ROLE {}").format(sql.Identifier(role))
            )
        admin.dispose()


def _hdr(token: str, **extra: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", **extra}


def test_source_ownership_authorization_isolation_and_permissions(
    env: SourceTestEnv,
) -> None:
    a, b = env.a, env.b
    with TestClient(create_app(env.settings, env.app_db)) as client:
        # 1. Admin / owner without teacher assignment MUST NOT create or read or list sources.
        resp = client.post(
            f"/api/v1/tenants/{a.tenant}/offerings/{a.offering}/sources",
            headers=_hdr(a.owner_token),
            json={
                "title": "Admin Probe",
                "input_format": "plain_text",
                "content": "Should be rejected by policy",
                "origin_kind": "teacher_resource",
            },
        )
        assert resp.status_code == 403

        resp = client.get(
            f"/api/v1/tenants/{a.tenant}/offerings/{a.offering}/sources",
            headers=_hdr(a.owner_token),
        )
        assert resp.status_code == 403

        # 2. Assigned teacher creates a draft teacher_only resource + published resource.
        draft = client.post(
            f"/api/v1/tenants/{a.tenant}/offerings/{a.offering}/sources",
            headers=_hdr(a.teacher_token),
            json={
                "title": "Teacher Draft Guide",
                "input_format": "typed_notes",
                "content": "# Kinematics\nAcceleration is dv/dt.",
                "origin_kind": "teacher_resource",
                "visibility": "teacher_only",
            },
        )
        assert draft.status_code == 201, draft.text
        draft_id = draft.json()["source"]["source_id"]
        draft_ver = draft.json()["version"]["version_id"]

        pub = client.post(
            f"/api/v1/tenants/{a.tenant}/offerings/{a.offering}/sources",
            headers=_hdr(a.teacher_token),
            json={
                "title": "Published Syllabus",
                "input_format": "topic_description",
                "content": "Newtonian motion and energy conservation.",
                "origin_kind": "teacher_resource",
                "visibility": "offering_published",
            },
        )
        assert pub.status_code == 201
        pub_id = pub.json()["source"]["source_id"]
        pub_ver = pub.json()["version"]["version_id"]

        # 3. Assigned co-teacher in same offering CAN access draft and published; unassigned teacher CANNOT.
        assert (
            client.get(
                f"/api/v1/tenants/{a.tenant}/sources/{draft_id}",
                headers=_hdr(a.co_teacher_token),
            ).status_code
            == 200
        )
        assert (
            client.get(
                f"/api/v1/tenants/{a.tenant}/sources/{draft_id}",
                headers=_hdr(a.other_teacher_token),
            ).status_code
            == 403
        )
        assert (
            client.get(
                f"/api/v1/tenants/{a.tenant}/sources/{pub_id}",
                headers=_hdr(a.other_teacher_token),
            ).status_code
            == 403
        )

        # 4. Cross-tenant actors (Tenant B) rejected on EVERY endpoint.
        assert (
            client.get(
                f"/api/v1/tenants/{a.tenant}/sources/{pub_id}",
                headers=_hdr(b.teacher_token),
            ).status_code
            == 403
        )
        assert (
            client.get(
                f"/api/v1/tenants/{a.tenant}/sources/{pub_id}/versions/{pub_ver}",
                headers=_hdr(b.owner_token),
            ).status_code
            == 403
        )

        # 5. Institution owner STILL denied even after resources exist; CAN inspect offering policy metadata.
        assert (
            client.get(
                f"/api/v1/tenants/{a.tenant}/sources/{pub_id}",
                headers=_hdr(a.owner_token),
            ).status_code
            == 403
        )
        assert (
            client.get(
                f"/api/v1/tenants/{a.tenant}/sources/{pub_id}/versions/{pub_ver}",
                headers=_hdr(a.owner_token),
            ).status_code
            == 403
        )
        assert (
            client.get(
                f"/api/v1/tenants/{a.tenant}/offerings/{a.offering}/source-policy",
                headers=_hdr(a.owner_token),
            ).status_code
            == 200
        )

        # 6. Enrolled student sees ONLY published teacher resource, NOT teacher_only draft, CANNOT mutate.
        assert (
            client.get(
                f"/api/v1/tenants/{a.tenant}/sources/{draft_id}",
                headers=_hdr(a.student_token),
            ).status_code
            == 403
        )
        assert (
            client.get(
                f"/api/v1/tenants/{a.tenant}/sources/{draft_id}/versions/{draft_ver}",
                headers=_hdr(a.student_token),
            ).status_code
            == 403
        )
        assert (
            client.get(
                f"/api/v1/tenants/{a.tenant}/sources/{pub_id}",
                headers=_hdr(a.student_token),
            ).status_code
            == 200
        )
        ver_resp = client.get(
            f"/api/v1/tenants/{a.tenant}/sources/{pub_id}/versions/{pub_ver}",
            headers=_hdr(a.student_token),
        )
        assert ver_resp.status_code == 200
        assert ver_resp.json()["extracted_document"]["quality"]["usable_for_digital_ingestion"] is True
        assert (
            client.patch(
                f"/api/v1/tenants/{a.tenant}/sources/{pub_id}",
                headers=_hdr(a.student_token),
                json={"title": "Tampered"},
            ).status_code
            == 403
        )
        st_list = client.get(
            f"/api/v1/tenants/{a.tenant}/offerings/{a.offering}/sources",
            headers=_hdr(a.student_token),
        )
        assert st_list.status_code == 200
        assert [item["source_id"] for item in st_list.json()["items"]] == [pub_id]

        # 7. Student submissions: allowed by policy, isolated from official content & other students.
        assert (
            client.post(
                f"/api/v1/tenants/{a.tenant}/offerings/{a.offering}/sources",
                headers=_hdr(a.student_token),
                json={
                    "title": "Unauthorized Official Material",
                    "input_format": "plain_text",
                    "content": "Trying to publish official content.",
                    "origin_kind": "teacher_resource",
                    "visibility": "offering_published",
                },
            ).status_code
            == 403
        )
        s_priv = client.post(
            f"/api/v1/tenants/{a.tenant}/offerings/{a.offering}/sources",
            headers=_hdr(a.student_token),
            json={
                "title": "My Private Doubt",
                "input_format": "plain_text",
                "content": "Why does centripetal acceleration point inward?",
                "origin_kind": "student_submission",
                "visibility": "owner_private",
            },
        )
        assert s_priv.status_code == 201
        s_priv_id = s_priv.json()["source"]["source_id"]

        s_rev = client.post(
            f"/api/v1/tenants/{a.tenant}/offerings/{a.offering}/sources",
            headers=_hdr(a.student_token),
            json={
                "title": "Question for Teacher",
                "input_format": "plain_text",
                "content": "Please check my derivation step 2.",
                "origin_kind": "student_submission",
                "visibility": "teacher_review",
            },
        )
        assert s_rev.status_code == 201
        s_rev_id = s_rev.json()["source"]["source_id"]

        # Other student CANNOT see s_priv or s_rev!
        assert (
            client.get(
                f"/api/v1/tenants/{a.tenant}/sources/{s_priv_id}",
                headers=_hdr(a.other_student_token),
            ).status_code
            == 403
        )
        assert (
            client.get(
                f"/api/v1/tenants/{a.tenant}/sources/{s_rev_id}",
                headers=_hdr(a.other_student_token),
            ).status_code
            == 403
        )
        # Teacher CAN see teacher_review but NOT owner_private; cannot mutate student work.
        assert (
            client.get(
                f"/api/v1/tenants/{a.tenant}/sources/{s_rev_id}",
                headers=_hdr(a.teacher_token),
            ).status_code
            == 200
        )
        assert (
            client.get(
                f"/api/v1/tenants/{a.tenant}/sources/{s_priv_id}",
                headers=_hdr(a.teacher_token),
            ).status_code
            == 403
        )
        assert (
            client.patch(
                f"/api/v1/tenants/{a.tenant}/sources/{s_rev_id}",
                headers=_hdr(a.teacher_token),
                json={"lifecycle_state": "archived"},
            ).status_code
            == 403
        )

        # Policy disables student submissions and teacher viewing of student submissions.
        pol = client.patch(
            f"/api/v1/tenants/{a.tenant}/offerings/{a.offering}/source-policy",
            headers=_hdr(a.owner_token),
            json={
                "student_submissions_enabled": False,
                "teachers_can_view_student_submissions": False,
            },
        )
        assert pol.status_code == 200
        assert (
            client.post(
                f"/api/v1/tenants/{a.tenant}/offerings/{a.offering}/sources",
                headers=_hdr(a.student_token),
                json={
                    "title": "Blocked Doubt",
                    "input_format": "plain_text",
                    "content": "Should be denied by offering policy.",
                    "origin_kind": "student_submission",
                },
            ).status_code
            == 403
        )
        assert (
            client.get(
                f"/api/v1/tenants/{a.tenant}/sources/{s_rev_id}",
                headers=_hdr(a.teacher_token),
            ).status_code
            == 403
        )

        # Archiving published source immediately removes student access!
        assert (
            client.patch(
                f"/api/v1/tenants/{a.tenant}/sources/{pub_id}",
                headers=_hdr(a.teacher_token),
                json={"lifecycle_state": "archived"},
            ).status_code
            == 200
        )
        assert (
            client.get(
                f"/api/v1/tenants/{a.tenant}/sources/{pub_id}",
                headers=_hdr(a.student_token),
            ).status_code
            == 403
        )

        # No unrestricted raw download route exists.
        assert (
            client.get(
                f"/api/v1/tenants/{a.tenant}/sources/{pub_id}/download",
                headers=_hdr(a.teacher_token),
            ).status_code
            == 404
        )


def test_revocations_idempotency_deduplication_and_immutable_versions(
    env: SourceTestEnv,
) -> None:
    a = env.a
    corpus = build_synthetic_corpus()
    with TestClient(create_app(env.settings, env.app_db)) as client:
        # 1. PDF upload + immutable v1 -> v2 progression
        up1 = client.post(
            f"/api/v1/tenants/{a.tenant}/offerings/{a.offering}/sources/files",
            headers=_hdr(a.teacher_token, **{"Idempotency-Key": "pdf-key-1"}),
            data={"title": "Wave Optics", "visibility": "offering_published"},
            files={"file": ("optics.pdf", corpus.small_pdf, "application/pdf")},
        )
        assert up1.status_code == 201, up1.text
        src_id = up1.json()["source"]["source_id"]
        v1_id = up1.json()["version"]["version_id"]
        v1_sha = up1.json()["version"]["content_sha256"]

        # Exact idempotent replay -> 200, same version ID, no duplicate stored
        replay = client.post(
            f"/api/v1/tenants/{a.tenant}/offerings/{a.offering}/sources/files",
            headers=_hdr(a.teacher_token, **{"Idempotency-Key": "pdf-key-1"}),
            data={"title": "Wave Optics", "visibility": "offering_published"},
            files={"file": ("optics.pdf", corpus.small_pdf, "application/pdf")},
        )
        assert replay.status_code == 200
        assert replay.json()["idempotent_replay"] is True
        assert replay.json()["version"]["version_id"] == v1_id

        # Same Idempotency-Key with DIFFERENT payload -> 409 conflict
        conflict = client.post(
            f"/api/v1/tenants/{a.tenant}/offerings/{a.offering}/sources/files",
            headers=_hdr(a.teacher_token, **{"Idempotency-Key": "pdf-key-1"}),
            data={"title": "Different Title", "visibility": "offering_published"},
            files={"file": ("optics.pdf", corpus.small_pdf, "application/pdf")},
        )
        assert conflict.status_code == 409

        # Co-teacher uploading identical PDF without idempotency key -> 409 duplicate_source
        dup = client.post(
            f"/api/v1/tenants/{a.tenant}/offerings/{a.offering}/sources/files",
            headers=_hdr(a.co_teacher_token),
            data={"title": "Optics Copy"},
            files={"file": ("copy.pdf", corpus.small_pdf, "application/pdf")},
        )
        assert dup.status_code == 409

        # Teacher in another offering uploading IDENTICAL PDF succeeds (no cross-offering leak!)
        other_up = client.post(
            f"/api/v1/tenants/{a.tenant}/offerings/{a.other_offering}/sources/files",
            headers=_hdr(a.other_teacher_token),
            data={"title": "Other Offering Optics"},
            files={"file": ("optics.pdf", corpus.small_pdf, "application/pdf")},
        )
        assert other_up.status_code == 201

        # Add immutable version v2 (multipage PDF)
        up2 = client.post(
            f"/api/v1/tenants/{a.tenant}/sources/{src_id}/versions/files",
            headers=_hdr(a.teacher_token),
            files={"file": ("optics_v2.pdf", corpus.multipage_pdf, "application/pdf")},
        )
        assert up2.status_code == 201
        v2_id = up2.json()["version"]["version_id"]
        assert up2.json()["version"]["version_number"] == 2
        assert up2.json()["version"]["content_sha256"] != v1_sha

        # Uploading identical bytes as existing version on same source -> 409 duplicate_version
        dup_v = client.post(
            f"/api/v1/tenants/{a.tenant}/sources/{src_id}/versions/files",
            headers=_hdr(a.teacher_token),
            files={"file": ("optics_again.pdf", corpus.small_pdf, "application/pdf")},
        )
        assert dup_v.status_code == 409

        # Both v1 and v2 remain immutable & independently retrievable
        vers = client.get(
            f"/api/v1/tenants/{a.tenant}/sources/{src_id}/versions",
            headers=_hdr(a.student_token),
        )
        assert vers.status_code == 200
        assert [v["version_number"] for v in vers.json()["items"]] == [2, 1]
        v1_detail = client.get(
            f"/api/v1/tenants/{a.tenant}/sources/{src_id}/versions/{v1_id}",
            headers=_hdr(a.student_token),
        )
        assert v1_detail.status_code == 200
        assert v1_detail.json()["content_sha256"] == v1_sha
        assert v1_detail.json()["version_id"] == v1_id
        assert (
            client.get(
                f"/api/v1/tenants/{a.tenant}/sources/{src_id}/versions/{v2_id}",
                headers=_hdr(a.student_token),
            ).status_code
            == 200
        )

        # Student A & Student B submitting identical content with identical Idempotency-Key
        # must remain strictly isolated without dedupe leakage.
        for tok in (a.student_token, a.other_student_token):
            st_sub = client.post(
                f"/api/v1/tenants/{a.tenant}/offerings/{a.offering}/sources",
                headers=_hdr(tok, **{"Idempotency-Key": "same-doubt-key"}),
                json={
                    "title": "Common Question",
                    "input_format": "plain_text",
                    "content": "What is Planck's constant?",
                    "origin_kind": "student_submission",
                },
            )
            assert st_sub.status_code == 201

        # Verify PostgreSQL restricted role blocks UPDATE/DELETE on source_versions!
        with pytest.raises(DBAPIError), env.app_db.sessions.begin() as s:
            from viora.authorization.tenant import bind_tenant

            bind_tenant(s, a.tenant)
            s.execute(
                text("UPDATE source_versions SET char_count=999 WHERE version_id=:v"),
                {"v": v1_id},
            )

        # Committed teacher revocation immediately revokes access on next request.
        with env.admin.begin() as conn:
            conn.execute(
                text(
                    "UPDATE teacher_assignments SET active=false WHERE tenant_id=:t AND user_id=:u AND offering_id=:o"
                ),
                {"t": a.tenant, "u": a.teacher, "o": a.offering},
            )
            conn.execute(
                text(
                    "UPDATE student_enrolments SET active=false WHERE tenant_id=:t AND user_id=:u AND offering_id=:o"
                ),
                {"t": a.tenant, "u": a.student, "o": a.offering},
            )
        assert (
            client.get(
                f"/api/v1/tenants/{a.tenant}/sources/{src_id}",
                headers=_hdr(a.teacher_token),
            ).status_code
            == 403
        )
        assert (
            client.get(
                f"/api/v1/tenants/{a.tenant}/sources/{src_id}",
                headers=_hdr(a.student_token),
            ).status_code
            == 403
        )


def test_http_invalid_spoofed_encrypted_and_oversized_uploads_leave_no_artifacts(
    env: SourceTestEnv,
) -> None:
    a = env.a
    corpus = build_synthetic_corpus()
    # Encrypted PDF
    w = PdfWriter()
    w.add_blank_page(width=612, height=792)
    w.encrypt("locked")
    ebuf = io.BytesIO()
    w.write(ebuf)
    encrypted_pdf = ebuf.getvalue()

    with TestClient(create_app(env.settings, env.app_db)) as client:
        # 1. Spoofed PDF filename containing plain text -> 415
        r1 = client.post(
            f"/api/v1/tenants/{a.tenant}/offerings/{a.offering}/sources/files",
            headers=_hdr(a.teacher_token),
            data={"title": "Spoof"},
            files={"file": ("spoof.pdf", b"not actually a pdf", "application/pdf")},
        )
        assert r1.status_code == 415

        # 2. Encrypted PDF -> 422
        r2 = client.post(
            f"/api/v1/tenants/{a.tenant}/offerings/{a.offering}/sources/files",
            headers=_hdr(a.teacher_token),
            data={"title": "Secret"},
            files={"file": ("secret.pdf", encrypted_pdf, "application/pdf")},
        )
        assert r2.status_code == 422
        assert r2.json()["code"] == "encrypted_pdf"

        # 3. Scanned / zero-text PDF -> 201 accepted promptly with queued background job
        r3 = client.post(
            f"/api/v1/tenants/{a.tenant}/offerings/{a.offering}/sources/files",
            headers=_hdr(a.teacher_token),
            data={"title": "Blank Scan"},
            files={"file": ("scan.pdf", corpus.notext_pdf, "application/pdf")},
        )
        assert r3.status_code == 201
        assert r3.json()["job_id"] is not None
        assert r3.json()["job_state"] == "queued"
        assert r3.json()["version"]["processing_status"] == "pending"

        # 4. Oversized byte payload -> 413 payload_too_large
        r4 = client.post(
            f"/api/v1/tenants/{a.tenant}/offerings/{a.offering}/sources/files",
            headers=_hdr(a.teacher_token),
            data={"title": "Huge"},
            files={"file": ("huge.txt", b"X" * (600 * 1024), "text/plain")},
        )
        assert r4.status_code == 413

        # 5. Exceeds page limit (21 > 20) -> 413 page_limit_exceeded
        pw_over = PdfWriter()
        for _ in range(21):
            pw_over.add_blank_page(width=612, height=792)
        obuf = io.BytesIO()
        pw_over.write(obuf)
        r5 = client.post(
            f"/api/v1/tenants/{a.tenant}/offerings/{a.offering}/sources/files",
            headers=_hdr(a.teacher_token),
            data={"title": "Too Many Pages"},
            files={"file": ("many.pdf", obuf.getvalue(), "application/pdf")},
        )
        assert r5.status_code == 413
        assert r5.json()["code"] == "page_limit_exceeded"

        # Confirm only the valid scanned PDF (r3) persisted; rejected uploads left zero artifacts
        lst = client.get(
            f"/api/v1/tenants/{a.tenant}/offerings/{a.offering}/sources",
            headers=_hdr(a.teacher_token),
        )
        assert lst.status_code == 200
        assert [item["title"] for item in lst.json()["items"]] == ["Blank Scan"]
        files_on_disk = (
            list(env.storage_dir.rglob("*.blob"))
            if env.storage_dir.exists()
            else []
        )
        assert len(files_on_disk) == 1


def test_partial_failure_db_rollback_and_orphan_artifact_cleanup(
    env: SourceTestEnv,
) -> None:
    a = env.a
    store = local_artifact_store(
        env.storage_dir, max_artifact_bytes=env.settings.source_max_bytes
    )
    limits = clamp_source_limits(
        max_bytes=env.settings.source_max_bytes,
        max_pdf_pages=env.settings.source_max_pdf_pages,
        max_text_chars=env.settings.source_max_text_chars,
        min_text_chars=env.settings.source_min_text_chars,
    )
    service = SourceIngestionService(store, limits)
    auth = AuthService(env.app_db.sessions, env.settings.auth)  # type: ignore[arg-type]
    gate = AuthorizationService(env.app_db, auth)

    # 1. Inject DB failure AFTER ArtifactStore.put_bytes succeeds -> must clean up orphan file!
    def simulate_db_crash() -> None:
        raise RuntimeError("Simulated database crash before commit")

    with pytest.raises(RuntimeError, match="Simulated database crash"):
        service.register_text_source(
            access=gate,
            repo_factory=SqlSourceRepository,
            bearer=a.teacher_token,
            tenant_id=a.tenant,
            offering_id=a.offering,
            title="Crashed Tx",
            input_format="plain_text",
            content="Data written to disk before DB crashed.",
            origin_kind="teacher_resource",
            visibility="teacher_only",
            original_filename=None,
            idempotency_key=None,
            before_db_persist_hook=simulate_db_crash,
        )

    assert list(env.storage_dir.rglob("*.blob")) == []
    with env.admin.connect() as conn:
        assert (
            conn.scalar(
                text("SELECT count(*) FROM sources WHERE tenant_id=:t AND title='Crashed Tx'"),
                {"t": a.tenant},
            )
            == 0
        )

    # 2. Ambiguous commit outcome: DB commit succeeds, then post-commit error occurs.
    # Storage compensation MUST NOT delete the committed artifact!
    def simulate_ambiguous_commit() -> None:
        raise RuntimeError("Simulated connection drop after COMMIT")

    with pytest.raises(RuntimeError, match="Simulated connection drop after COMMIT"):
        service.register_text_source(
            access=gate,
            repo_factory=SqlSourceRepository,
            bearer=a.teacher_token,
            tenant_id=a.tenant,
            offering_id=a.offering,
            title="Committed Ambiguous Tx",
            input_format="plain_text",
            content="Artifact committed to PostgreSQL before response failed.",
            origin_kind="teacher_resource",
            visibility="teacher_only",
            original_filename=None,
            idempotency_key="ambiguous-commit-key",
            after_db_commit_hook=simulate_ambiguous_commit,
        )

    committed_blobs = list(env.storage_dir.rglob("*.blob"))
    assert len(committed_blobs) == 1
    with env.admin.connect() as conn:
        assert (
            conn.scalar(
                text("SELECT count(*) FROM sources WHERE tenant_id=:t AND title='Committed Ambiguous Tx'"),
                {"t": a.tenant},
            )
            == 1
        )

    # 3. Inject Storage failure -> DB rolls back cleanly with 503 storage_unavailable!
    class FailingStore:
        def put_bytes(self, *args, **kwargs):
            raise StorageConfigurationError("Disk full simulation")

        def delete_uncommitted(self, *args, **kwargs) -> bool:
            return False

    failing_service = SourceIngestionService(FailingStore(), limits)  # type: ignore[arg-type]
    with pytest.raises(AccessError) as exc:
        failing_service.register_text_source(
            access=gate,
            repo_factory=SqlSourceRepository,
            bearer=a.teacher_token,
            tenant_id=a.tenant,
            offering_id=a.offering,
            title="Disk Error Tx",
            input_format="plain_text",
            content="Should not remain in database.",
            origin_kind="teacher_resource",
            visibility="teacher_only",
            original_filename=None,
            idempotency_key=None,
        )
    assert exc.value.status == 503
    assert exc.value.code == "storage_unavailable"
    with env.admin.connect() as conn:
        assert (
            conn.scalar(
                text("SELECT count(*) FROM sources WHERE tenant_id=:t AND title='Disk Error Tx'"),
                {"t": a.tenant},
            )
            == 0
        )


def test_concurrent_idempotent_requests_and_multipart_prebuffering_limit(
    env: SourceTestEnv,
) -> None:
    from concurrent.futures import ThreadPoolExecutor

    a = env.a
    store = local_artifact_store(
        env.storage_dir, max_artifact_bytes=env.settings.source_max_bytes
    )
    limits = clamp_source_limits(
        max_bytes=env.settings.source_max_bytes,
        max_pdf_pages=env.settings.source_max_pdf_pages,
        max_text_chars=env.settings.source_max_text_chars,
        min_text_chars=env.settings.source_min_text_chars,
    )
    service = SourceIngestionService(store, limits)
    auth = AuthService(env.app_db.sessions, env.settings.auth)  # type: ignore[arg-type]
    gate = AuthorizationService(env.app_db, auth)

    def submit_once() -> tuple[UUID, UUID, bool]:
        res = service.register_text_source(
            access=gate,
            repo_factory=SqlSourceRepository,
            bearer=a.teacher_token,
            tenant_id=a.tenant,
            offering_id=a.offering,
            title="Concurrent Idempotent Source",
            input_format="plain_text",
            content="Concurrent idempotent registration payload.",
            origin_kind="teacher_resource",
            visibility="teacher_only",
            original_filename="concurrent.txt",
            idempotency_key="concurrent-key-1",
        )
        return res.source.source_id, res.version.version_id, res.idempotent_replay

    with ThreadPoolExecutor(max_workers=2) as pool:
        fut1 = pool.submit(submit_once)
        fut2 = pool.submit(submit_once)
        r1 = fut1.result()
        r2 = fut2.result()

    assert r1[0] == r2[0]
    assert r1[1] == r2[1]
    assert sorted([r1[2], r2[2]]) == [False, True]
    assert len(list(env.storage_dir.rglob("*.blob"))) == 1

    # Multipart request exceeding max_multipart_bytes is rejected at ASGI middleware before buffering
    with TestClient(create_app(env.settings, env.app_db)) as client:
        oversized = b"A" * (env.settings.source_max_bytes + 300 * 1024)
        resp = client.post(
            f"/api/v1/tenants/{a.tenant}/offerings/{a.offering}/sources/files",
            headers=_hdr(a.teacher_token),
            data={"title": "Too Big Multipart"},
            files={"file": ("huge.txt", oversized, "text/plain")},
        )
        assert resp.status_code == 413
        assert resp.json()["code"] == "payload_too_large"
