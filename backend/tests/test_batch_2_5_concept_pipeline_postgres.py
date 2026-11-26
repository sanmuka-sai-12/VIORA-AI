"""Live PostgreSQL + HTTP + Worker integration tests for Batch 2.5 concept extraction."""

from __future__ import annotations

import json
import os
import secrets
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
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
from sqlalchemy.exc import DBAPIError
from sqlalchemy.schema import CreateSchema, DropSchema
from viora.ai.public import (
    AIRequestConfig,
    AIResponseFormatError,
    AITransientError,
    ChatMessage,
    FakeAIGateway,
)
from viora.authorization.public import bind_tenant
from viora.config import AuthSettings, Settings
from viora.contracts import TenantContext
from viora.database_roles import grant_application_privileges
from viora.db import Database
from viora.identity.public import AuthService, IdentityAttributeKind
from viora.knowledge.public import (
    RawConceptCandidate,
    RawConceptExtractionBatch,
    RawRelationshipCandidate,
)
from viora.main import create_app
from viora.sources.ocr import OcrLine, OcrPageResult
from viora.workflow.public import SqlWorkflowWorkerStore, StaleWorkerFenceError
from viora.workflow.worker import WorkflowWorkerRunner, build_worker_runner

pytestmark = pytest.mark.postgres
ROOT = Path(__file__).resolve().parents[2]
CORPUS_DIR = ROOT / "benchmarks" / "educational_corpus"
MANIFEST_PATH = CORPUS_DIR / "ground_truth.json"
PASSWORD = SecretStr("batch 2.5 integration passphrase 99")


def _load_sample(doc_id: str) -> bytes:
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    item = next(d for d in manifest["documents"] if d["doc_id"] == doc_id)
    return (CORPUS_DIR / item["relative_path"]).read_bytes()


class _OcrFallback:
    engine_id = "test.ocr.fallback"
    engine_version = "1.0"

    def is_available(self) -> bool:
        return True

    def recognize_image(self, image, *, preprocess: bool = False) -> OcrPageResult:
        _ = preprocess
        return OcrPageResult(
            lines=(
                OcrLine(
                    text="Conservation of Mechanical Energy: K + U = constant.",
                    confidence=0.94,
                    x0=10.0,
                    y0=10.0,
                    x1=float(max(40, image.width - 10)),
                    y1=40.0,
                ),
            ),
            image_width=image.width,
            image_height=image.height,
            engine_id=self.engine_id,
            engine_version=self.engine_version,
            mean_confidence=0.94,
        )


class _DeterministicConceptGateway(FakeAIGateway):
    """Test AIGateway that reads cited [ev:...] IDs directly from prompt chunks or injects failure modes."""

    def __init__(self) -> None:
        super().__init__(model_name="@cf/meta/llama-3.1-8b-instruct-fast")
        self.fail_sequence: list[Exception] = []
        self.live_calls = 0

    def complete_structured(
        self,
        context: TenantContext,
        messages: list[ChatMessage],
        response_model,
        config: AIRequestConfig | None = None,
    ):
        self.live_calls += 1
        if self.fail_sequence:
            raise self.fail_sequence.pop(0)
        user_txt = messages[-1].content
        ev_ids: list[str] = []
        for marker in ("[ev:",):
            pos = 0
            while True:
                idx = user_txt.find(marker, pos)
                if idx == -1:
                    break
                end = user_txt.find("]", idx)
                if end != -1:
                    for eid in user_txt[idx + 4 : end].split(","):
                        if eid.strip() and eid.strip() not in ev_ids:
                            ev_ids.append(eid.strip())
                    pos = end + 1
                else:
                    break
        is_topic = "<untrusted_topic_request>" in user_txt
        ev_text_by_id: dict[str, str] = {}
        if "<untrusted_source_data>" in user_txt:
            body = user_txt.split("<untrusted_source_data>", 1)[1].split("</untrusted_source_data>", 1)[0]
            for raw_ln in body.splitlines():
                ln = raw_ln.strip()
                if "[ev:" not in ln or not ln.endswith("]"):
                    continue
                pos = ln.rfind("[ev:")
                txt = ln[:pos].strip().lstrip("#").strip()
                if not txt:
                    continue
                for eid in ln[pos + 4 : -1].split(","):
                    clean_eid = eid.strip()
                    if clean_eid and clean_eid not in ev_text_by_id:
                        ev_text_by_id[clean_eid] = txt
        cited = ev_ids[:1]
        snippet = ev_text_by_id.get(cited[0], "") if cited else ""
        second_ev = ev_ids[1:2] or cited
        second_snippet = ev_text_by_id.get(second_ev[0], "") if second_ev else ""
        batch = RawConceptExtractionBatch(
            section_summary="Grounded educational summary",
            concepts=[
                RawConceptCandidate(
                    name="Core Concept A",
                    canonical_summary=snippet or "Primary extracted concept.",
                    definition=snippet or "Defined cleanly from extracted text.",
                    grounding="learning_request" if is_topic else "source_explicit",
                    evidence_ids=cited,
                    prerequisites=["Foundational Concept"],
                ),
                RawConceptCandidate(
                    name="Foundational Concept",
                    canonical_summary=second_snippet or "Supporting prerequisite.",
                    grounding="learning_request" if is_topic else "source_synthesized",
                    evidence_ids=second_ev,
                ),
            ],
            relationships=[
                RawRelationshipCandidate(
                    source_concept="Foundational Concept",
                    relation_type="prerequisite_of",
                    target_concept="Core Concept A",
                    grounding="learning_request" if is_topic else "source_explicit",
                    evidence_ids=cited,
                )
            ],
        )
        self.structured_payloads[response_model] = batch
        return super().complete_structured(context, messages, response_model, config)


@dataclass(frozen=True)
class TenantActors:
    tenant: UUID
    offering: UUID
    teacher: UUID
    student: UUID
    student2: UUID
    teacher_token: str
    student_token: str
    student2_token: str


@dataclass(frozen=True)
class KnowledgeEnv:
    admin: Engine
    app_db: Database
    settings: Settings
    a: TenantActors
    b: TenantActors


def _provision(conn, auth: AuthService, name: str) -> TenantActors:
    ids = {
        k: uuid4()
        for k in (
            "tenant",
            "dept",
            "year",
            "sem",
            "sec",
            "subj",
            "offering",
            "teacher",
            "student",
            "student2",
        )
    }
    pw_hash = auth.passwords.hash(PASSWORD)
    conn.execute(text("INSERT INTO institutions VALUES (:tenant, :n, true)"), {"tenant": ids["tenant"], "n": name})
    emails = {}
    for role_key, role in (
        ("teacher", "subject_teacher"),
        ("student", "student"),
        ("student2", "student"),
    ):
        uid = ids[role_key]
        em = f"{role_key}-{uid}@school.test"
        emails[role_key] = em
        conn.execute(text("INSERT INTO users VALUES (:u, :r, 'active')"), {"u": uid, "r": role_key})
        conn.execute(text("INSERT INTO memberships VALUES (:t, :u, 'active')"), {"t": ids["tenant"], "u": uid})
        conn.execute(text("INSERT INTO role_grants (tenant_id, user_id, role) VALUES (:t, :u, :r)"), {"t": ids["tenant"], "u": uid, "r": role})
        conn.execute(text("INSERT INTO verified_identity_attributes VALUES (:u, 'email', :e, now())"), {"u": uid, "e": em})
        conn.execute(text("INSERT INTO password_credentials VALUES (:u, :h)"), {"u": uid, "h": pw_hash})

    conn.execute(text("INSERT INTO departments (tenant_id, id, name) VALUES (:tenant, :dept, 'Physics')"), ids)
    conn.execute(text("INSERT INTO academic_years (tenant_id, id, name) VALUES (:tenant, :year, '2026')"), ids)
    conn.execute(text("INSERT INTO semesters (tenant_id, id, name, academic_year_id) VALUES (:tenant, :sem, 'S1', :year)"), ids)
    conn.execute(text("INSERT INTO sections (tenant_id, id, name, semester_id) VALUES (:tenant, :sec, 'Sec1', :sem)"), ids)
    conn.execute(text("INSERT INTO subjects (tenant_id, id, name, department_id) VALUES (:tenant, :subj, 'Phys101', :dept)"), ids)
    conn.execute(text("INSERT INTO subject_offerings (tenant_id, id, name, section_id, subject_id) VALUES (:tenant, :offering, 'Phys101-A', :sec, :subj)"), ids)
    conn.execute(text("INSERT INTO teacher_assignments VALUES (:tenant, :teacher, :offering, true)"), ids)
    conn.execute(text("INSERT INTO student_enrolments VALUES (:tenant, :student, :offering, true)"), ids)
    conn.execute(text("INSERT INTO student_enrolments VALUES (:tenant, :student2, :offering, true)"), ids)
    return TenantActors(
        tenant=ids["tenant"],
        offering=ids["offering"],
        teacher=ids["teacher"],
        student=ids["student"],
        student2=ids["student2"],
        teacher_token=emails["teacher"],
        student_token=emails["student"],
        student2_token=emails["student2"],
    )


@pytest.fixture()
def env(tmp_path: Path) -> Iterator[KnowledgeEnv]:
    raw = os.environ.get("VIORA_TEST_DATABASE_URL")
    if not raw:
        pytest.skip("Dedicated viora_test database required")
    url = make_url(raw)
    if url.database != "viora_test":
        pytest.fail("Requires viora_test database")

    schema = f"kn_{uuid4().hex}"
    role = f"kn_app_{uuid4().hex}"
    pw = secrets.token_urlsafe(24)
    scoped = url.update_query_dict({"options": f"-csearch_path={schema}"})
    scoped_str = scoped.render_as_string(hide_password=False)
    app_url = scoped.set(username=role, password=pw).render_as_string(hide_password=False)
    admin = create_engine(scoped)
    settings = Settings(
        database_url=SecretStr(app_url),
        environment="test",
        media_storage_dir=str(tmp_path / "media"),
        source_max_bytes=2 * 1024 * 1024,
        worker_concurrency=2,
        worker_page_workers=1,
        worker_lease_seconds=10,
        auth=AuthSettings(signing_key=SecretStr("k" * 64)),
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
            a_raw = _provision(conn, auth, "Alpha Academy")
            b_raw = _provision(conn, auth, "Beta Academy")

        def login(em: str) -> str:
            return auth.login(IdentityAttributeKind.email, em, PASSWORD).access_token.get_secret_value()

        a = TenantActors(**{**a_raw.__dict__, "teacher_token": login(a_raw.teacher_token), "student_token": login(a_raw.student_token), "student2_token": login(a_raw.student2_token)})
        b = TenantActors(**{**b_raw.__dict__, "teacher_token": login(b_raw.teacher_token), "student_token": login(b_raw.student_token), "student2_token": login(b_raw.student2_token)})
        yield KnowledgeEnv(admin, app_db, settings, a, b)
    finally:
        app_db.close()
        with admin.begin() as conn:
            conn.execute(DropSchema(schema, cascade=True))
            conn.connection.driver_connection.execute(
                sql.SQL("DROP ROLE {}").format(sql.Identifier(role))
            )
        admin.dispose()


def _hdr(tok: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {tok}"}


def test_end_to_end_concept_extraction_caching_auth_and_recovery(env: KnowledgeEnv) -> None:
    a, b = env.a, env.b
    gw = _DeterministicConceptGateway()
    runner = build_worker_runner(
        env.settings,
        db=env.app_db,
        ocr_backend=_OcrFallback(),
        ai_gateway=gw,
    )
    try:
        with TestClient(create_app(env.settings, env.app_db)) as client:
            # 1. Ingest notes, digital PDF, tables PDF, OCR PNG, and topic_description
            md_up = client.post(
                f"/api/v1/tenants/{a.tenant}/offerings/{a.offering}/sources",
                headers=_hdr(a.teacher_token),
                json={
                    "title": "Kinematics Notes",
                    "input_format": "typed_notes",
                    "content": _load_sample("01_markdown_notes").decode("utf-8"),
                    "visibility": "offering_published",
                },
            ).json()
            topic_up = client.post(
                f"/api/v1/tenants/{a.tenant}/offerings/{a.offering}/sources",
                headers=_hdr(a.teacher_token),
                json={
                    "title": "Electromagnetism Topic",
                    "input_format": "topic_description",
                    "content": "Introduce Faraday induction and Lenz law.",
                    "visibility": "offering_published",
                },
            ).json()
            pdf_up = client.post(
                f"/api/v1/tenants/{a.tenant}/offerings/{a.offering}/sources/files",
                headers=_hdr(a.teacher_token),
                data={"title": "Tables PDF", "visibility": "offering_published"},
                files={"file": ("tables.pdf", _load_sample("05_tables_bordered_and_borderless"), "application/pdf")},
            ).json()
            png_up = client.post(
                f"/api/v1/tenants/{a.tenant}/offerings/{a.offering}/sources/files",
                headers=_hdr(a.teacher_token),
                data={"title": "OCR Scan", "input_format": "png", "visibility": "offering_published"},
                files={"file": ("scan.png", _load_sample("07_scanned_printed_page_png"), "image/png")},
            ).json()
            assert runner.drain()["succeeded"] == 2

            targets = [
                (md_up["source"]["source_id"], md_up["version"]["version_id"], "source_supported"),
                (topic_up["source"]["source_id"], topic_up["version"]["version_id"], "learning_request"),
                (pdf_up["source"]["source_id"], pdf_up["version"]["version_id"], "source_supported"),
                (png_up["source"]["source_id"], png_up["version"]["version_id"], "source_supported"),
            ]
            # Enqueue concept_extract for all 4 (must NOT consume credits/paid calls during enqueue)
            job_ids: list[str] = []
            for sid, vid, _ in targets:
                res = client.post(
                    f"/api/v1/tenants/{a.tenant}/sources/{sid}/versions/{vid}/concepts/extract",
                    headers=_hdr(a.teacher_token),
                )
                assert res.status_code == 202
                job_ids.append(res.json()["job_id"])

            # Duplicate enqueue coalesces to existing active job
            dup_res = client.post(
                f"/api/v1/tenants/{a.tenant}/sources/{targets[0][0]}/versions/{targets[0][1]}/concepts/extract",
                headers=_hdr(a.teacher_token),
            )
            assert dup_res.status_code == 202
            assert dup_res.json()["job_id"] == job_ids[0]
            assert dup_res.json()["idempotent_replay"] is True

            assert runner.drain()["succeeded"] == 4
            calls_after_miss = gw.live_calls
            assert calls_after_miss >= 4

            for (sid, vid, expected_grounding), jid in zip(targets, job_ids, strict=True):
                c_res = client.get(
                    f"/api/v1/tenants/{a.tenant}/sources/{sid}/versions/{vid}/concepts",
                    headers=_hdr(a.teacher_token),
                )
                assert c_res.status_code == 200
                body = c_res.json()
                assert body["published_to_students"] is False
                assert body["knowledge"]["published_to_students"] is False
                assert body["knowledge"]["concepts"][0]["grounding"] == expected_grounding
                assert body["total_tokens"] > 0
                assert body["input_fingerprint"]
                job_info = client.get(
                    f"/api/v1/tenants/{a.tenant}/jobs/{jid}",
                    headers=_hdr(a.teacher_token),
                ).json()
                assert job_info["result_extraction_id"] == body["concept_extraction_id"]

                # Enrolled students CANNOT view unreviewed/unpublished concepts even on offering_published sources
                assert (
                    client.get(
                        f"/api/v1/tenants/{a.tenant}/sources/{sid}/versions/{vid}/concepts",
                        headers=_hdr(a.student_token),
                    ).status_code
                    == 403
                )
                # Cross-tenant isolation strictly returns 403
                assert (
                    client.get(
                        f"/api/v1/tenants/{a.tenant}/sources/{sid}/versions/{vid}/concepts",
                        headers=_hdr(b.teacher_token),
                    ).status_code
                    == 403
                )

            # 1b. Batch 2.6: Structured outline, versioned review/approval, DB invariant & student publication
            md_sid, md_vid, _ = targets[0]
            md_ext_id = client.get(
                f"/api/v1/tenants/{a.tenant}/sources/{md_sid}/versions/{md_vid}/concepts",
                headers=_hdr(a.teacher_token),
            ).json()["concept_extraction_id"]
            topic_sid, topic_vid, _ = targets[1]
            topic_ext_id = client.get(
                f"/api/v1/tenants/{a.tenant}/sources/{topic_sid}/versions/{topic_vid}/concepts",
                headers=_hdr(a.teacher_token),
            ).json()["concept_extraction_id"]

            outline_res = client.get(
                f"/api/v1/tenants/{a.tenant}/sources/{md_sid}/versions/{md_vid}/concepts/outline",
                headers=_hdr(a.teacher_token),
            )
            assert outline_res.status_code == 200
            outline_body = outline_res.json()
            assert outline_body["teacher_approved"] is False
            assert outline_body["published_to_students"] is False
            assert len(outline_body["outline"]["modules"]) >= 1

            # Student cannot view draft outline, submit review, publish, or read unpublished package
            assert (
                client.get(
                    f"/api/v1/tenants/{a.tenant}/sources/{md_sid}/versions/{md_vid}/concepts/outline",
                    headers=_hdr(a.student_token),
                ).status_code
                == 403
            )
            assert (
                client.get(
                    f"/api/v1/tenants/{a.tenant}/sources/{md_sid}/versions/{md_vid}/concepts/published",
                    headers=_hdr(a.student_token),
                ).status_code
                == 404
            )
            assert (
                client.post(
                    f"/api/v1/tenants/{a.tenant}/sources/{md_sid}/versions/{md_vid}/concepts/review",
                    headers=_hdr(a.student_token),
                    json={"concept_extraction_id": md_ext_id, "decision": "approved"},
                ).status_code
                == 403
            )
            assert (
                client.post(
                    f"/api/v1/tenants/{a.tenant}/sources/{md_sid}/versions/{md_vid}/concepts/publish",
                    headers=_hdr(a.student_token),
                    json={"concept_extraction_id": md_ext_id, "publish": True},
                ).status_code
                == 403
            )

            # Publishing without teacher approval fails with 409; approving topic_description without ack fails with 409
            assert (
                client.post(
                    f"/api/v1/tenants/{a.tenant}/sources/{md_sid}/versions/{md_vid}/concepts/publish",
                    headers=_hdr(a.teacher_token),
                    json={"concept_extraction_id": md_ext_id, "publish": True},
                ).status_code
                == 409
            )
            assert (
                client.post(
                    f"/api/v1/tenants/{a.tenant}/sources/{topic_sid}/versions/{topic_vid}/concepts/review",
                    headers=_hdr(a.teacher_token),
                    json={"concept_extraction_id": topic_ext_id, "decision": "approved"},
                ).status_code
                == 409
            )

            # Versioned review v1 (changes_requested) -> v2 (approved) -> publish -> enrolled student accesses package
            rev1 = client.post(
                f"/api/v1/tenants/{a.tenant}/sources/{md_sid}/versions/{md_vid}/concepts/review",
                headers=_hdr(a.teacher_token),
                json={
                    "concept_extraction_id": md_ext_id,
                    "decision": "changes_requested",
                    "review_notes": "Verify unit dimensions",
                },
            )
            assert rev1.status_code == 200
            assert rev1.json()["review_status"] == "changes_requested"
            assert rev1.json()["teacher_approved"] is False

            # DB CHECK invariant prevents direct publication without approved review
            with pytest.raises(DBAPIError), env.app_db.sessions.begin() as s:
                bind_tenant(s, a.tenant)
                s.execute(
                    text("UPDATE knowledge_publications SET published_to_students=true WHERE tenant_id=:t AND version_id=:v"),
                    {"t": a.tenant, "v": UUID(md_vid)},
                )

            rev2 = client.post(
                f"/api/v1/tenants/{a.tenant}/sources/{md_sid}/versions/{md_vid}/concepts/review",
                headers=_hdr(a.teacher_token),
                json={
                    "concept_extraction_id": md_ext_id,
                    "decision": "approved",
                    "review_notes": "Verified against source notes",
                },
            )
            assert rev2.status_code == 200
            assert rev2.json()["teacher_approved"] is True
            assert rev2.json()["published_to_students"] is False

            pub_res = client.post(
                f"/api/v1/tenants/{a.tenant}/sources/{md_sid}/versions/{md_vid}/concepts/publish",
                headers=_hdr(a.teacher_token),
                json={"concept_extraction_id": md_ext_id, "publish": True},
            )
            assert pub_res.status_code == 200
            assert pub_res.json()["published_to_students"] is True

            student_pkg = client.get(
                f"/api/v1/tenants/{a.tenant}/sources/{md_sid}/versions/{md_vid}/concepts/published",
                headers=_hdr(a.student_token),
            )
            assert student_pkg.status_code == 200
            pkg_json = student_pkg.json()
            assert pkg_json["teacher_approved"] is True
            assert pkg_json["published_to_students"] is True
            assert pkg_json["approved_review_id"] == rev2.json()["approved_review_id"]

            # Cross-tenant student/teacher still strictly denied
            assert (
                client.get(
                    f"/api/v1/tenants/{a.tenant}/sources/{md_sid}/versions/{md_vid}/concepts/published",
                    headers=_hdr(b.teacher_token),
                ).status_code
                == 403
            )

            # Submitting subsequent changes_requested review v3 auto-unpublishes cleanly
            rev3 = client.post(
                f"/api/v1/tenants/{a.tenant}/sources/{md_sid}/versions/{md_vid}/concepts/review",
                headers=_hdr(a.teacher_token),
                json={"concept_extraction_id": md_ext_id, "decision": "changes_requested"},
            )
            assert rev3.status_code == 200
            assert rev3.json()["published_to_students"] is False
            assert (
                client.get(
                    f"/api/v1/tenants/{a.tenant}/sources/{md_sid}/versions/{md_vid}/concepts/published",
                    headers=_hdr(a.student_token),
                ).status_code
                == 404
            )
            reviews_list = client.get(
                f"/api/v1/tenants/{a.tenant}/sources/{md_sid}/versions/{md_vid}/concepts/reviews",
                headers=_hdr(a.teacher_token),
            )
            assert reviews_list.status_code == 200
            assert len(reviews_list.json()["items"]) == 3

            # 2. Same-tenant replay hits concept_extraction_cache with ZERO LLM calls
            re_trig = client.post(
                f"/api/v1/tenants/{a.tenant}/sources/{targets[0][0]}/versions/{targets[0][1]}/concepts/extract",
                headers=_hdr(a.teacher_token),
            )
            assert re_trig.status_code == 202
            assert runner.drain()["succeeded"] == 1
            assert gw.live_calls == calls_after_miss
            c2 = client.get(
                f"/api/v1/tenants/{a.tenant}/sources/{targets[0][0]}/versions/{targets[0][1]}/concepts",
                headers=_hdr(a.teacher_token),
            ).json()
            assert c2["concept_version"] == 2
            assert c2["cache_hit"] is True
            # Newer concept_version=2 is not approved yet and cannot be published using old or unapproved state
            assert (
                client.post(
                    f"/api/v1/tenants/{a.tenant}/sources/{md_sid}/versions/{md_vid}/concepts/publish",
                    headers=_hdr(a.teacher_token),
                    json={"concept_extraction_id": c2["concept_extraction_id"], "publish": True},
                ).status_code
                == 409
            )
            # Re-approve v1 and test explicit unpublish (publish=false)
            client.post(
                f"/api/v1/tenants/{a.tenant}/sources/{md_sid}/versions/{md_vid}/concepts/review",
                headers=_hdr(a.teacher_token),
                json={"concept_extraction_id": md_ext_id, "decision": "approved"},
            )
            client.post(
                f"/api/v1/tenants/{a.tenant}/sources/{md_sid}/versions/{md_vid}/concepts/publish",
                headers=_hdr(a.teacher_token),
                json={"concept_extraction_id": md_ext_id, "publish": True},
            )
            unpub = client.post(
                f"/api/v1/tenants/{a.tenant}/sources/{md_sid}/versions/{md_vid}/concepts/publish",
                headers=_hdr(a.teacher_token),
                json={"concept_extraction_id": md_ext_id, "publish": False},
            )
            assert unpub.status_code == 200
            assert unpub.json()["published_to_students"] is False
            assert (
                client.get(
                    f"/api/v1/tenants/{a.tenant}/sources/{md_sid}/versions/{md_vid}/concepts/published",
                    headers=_hdr(a.student_token),
                ).status_code
                == 404
            )

            # 3. Cross-tenant identical source NEVER hits Tenant A AI cache
            b_md = client.post(
                f"/api/v1/tenants/{b.tenant}/offerings/{b.offering}/sources",
                headers=_hdr(b.teacher_token),
                json={
                    "title": "Beta Kinematics",
                    "input_format": "typed_notes",
                    "content": _load_sample("01_markdown_notes").decode("utf-8"),
                    "visibility": "offering_published",
                },
            ).json()
            client.post(
                f"/api/v1/tenants/{b.tenant}/sources/{b_md['source']['source_id']}/versions/{b_md['version']['version_id']}/concepts/extract",
                headers=_hdr(b.teacher_token),
            )
            before_b = gw.live_calls
            assert runner.drain()["succeeded"] == 1
            assert gw.live_calls > before_b
            b_concepts = client.get(
                f"/api/v1/tenants/{b.tenant}/sources/{b_md['source']['source_id']}/versions/{b_md['version']['version_id']}/concepts",
                headers=_hdr(b.teacher_token),
            ).json()
            assert b_concepts["cache_hit"] is False
            assert b_concepts["knowledge"]["concepts"][0]["evidence_bindings"][0]["source_id"] == b_md["source"]["source_id"]

            # 4. Student submission cache isolation: Student 1 vs Student 2 must not share AI cache
            s1_sub = client.post(
                f"/api/v1/tenants/{a.tenant}/offerings/{a.offering}/sources",
                headers=_hdr(a.student_token),
                json={
                    "title": "Student 1 Essay",
                    "input_format": "plain_text",
                    "content": "Unique student reflection about inertia and mass.",
                    "origin_kind": "student_submission",
                    "visibility": "owner_private",
                },
            ).json()
            s2_sub = client.post(
                f"/api/v1/tenants/{a.tenant}/offerings/{a.offering}/sources",
                headers=_hdr(a.student2_token),
                json={
                    "title": "Student 2 Essay",
                    "input_format": "plain_text",
                    "content": "Unique student reflection about inertia and mass.",
                    "origin_kind": "student_submission",
                    "visibility": "owner_private",
                },
            ).json()
            client.post(
                f"/api/v1/tenants/{a.tenant}/sources/{s1_sub['source']['source_id']}/versions/{s1_sub['version']['version_id']}/concepts/extract",
                headers=_hdr(a.student_token),
            )
            client.post(
                f"/api/v1/tenants/{a.tenant}/sources/{s2_sub['source']['source_id']}/versions/{s2_sub['version']['version_id']}/concepts/extract",
                headers=_hdr(a.student2_token),
            )
            calls_before_students = gw.live_calls
            assert runner.drain()["succeeded"] == 2
            assert gw.live_calls >= calls_before_students + 2

            # 5. Provider transient retry & permanent failure handling + revoked teacher check
            md2 = client.post(
                f"/api/v1/tenants/{a.tenant}/offerings/{a.offering}/sources",
                headers=_hdr(a.teacher_token),
                json={
                    "title": "Retry Notes",
                    "input_format": "typed_notes",
                    "content": "Angular momentum equals r cross p.",
                    "visibility": "teacher_only",
                },
            ).json()
            retry_j = client.post(
                f"/api/v1/tenants/{a.tenant}/sources/{md2['source']['source_id']}/versions/{md2['version']['version_id']}/concepts/extract",
                headers=_hdr(a.teacher_token),
            ).json()["job_id"]
            gw.fail_sequence = [AITransientError("Provider timeout")]
            runner.retry_base_seconds = 0.05
            assert runner.drain()["retry_wait"] == 1
            j_wait = client.get(f"/api/v1/tenants/{a.tenant}/jobs/{retry_j}", headers=_hdr(a.teacher_token)).json()
            assert j_wait["state"] == "retry_wait"

            # Permanent failure check
            gw.fail_sequence = [AIResponseFormatError("Bad JSON schema")]
            with env.admin.begin() as conn:
                conn.execute(
                    text("UPDATE workflow_jobs SET next_run_at = now() - interval '1 second' WHERE tenant_id=:t AND job_id=:j"),
                    {"t": a.tenant, "j": retry_j},
                )
            assert runner.drain()["failed"] == 1

            # Append-only RLS on concept_extractions
            with pytest.raises(DBAPIError), env.app_db.sessions.begin() as s:
                bind_tenant(s, a.tenant)
                s.execute(text("UPDATE concept_extractions SET published_to_students=true"))

            # Stale lease fencing test on concept_extract job
            md3 = client.post(
                f"/api/v1/tenants/{a.tenant}/offerings/{a.offering}/sources",
                headers=_hdr(a.teacher_token),
                json={
                    "title": "Fenced Notes",
                    "input_format": "typed_notes",
                    "content": "Entropy increases in isolated spontaneous processes.",
                    "visibility": "teacher_only",
                },
            ).json()
            fence_j = client.post(
                f"/api/v1/tenants/{a.tenant}/sources/{md3['source']['source_id']}/versions/{md3['version']['version_id']}/concepts/extract",
                headers=_hdr(a.teacher_token),
            ).json()["job_id"]
            with env.app_db.sessions.begin() as s:
                c_a = SqlWorkflowWorkerStore.claim_next_job(
                    s, worker_id="cf-worker-1", lease_seconds=10, now=datetime.now(UTC)
                )
                assert c_a is not None
            with env.admin.begin() as conn:
                conn.execute(
                    text("UPDATE workflow_jobs SET lease_expires_at=:exp WHERE tenant_id=:t AND job_id=:j"),
                    {"exp": datetime.now(UTC) - timedelta(seconds=5), "t": a.tenant, "j": fence_j},
                )
            with env.app_db.sessions.begin() as s:
                c_b = SqlWorkflowWorkerStore.claim_next_job(
                    s, worker_id="cf-worker-2", lease_seconds=10, now=datetime.now(UTC)
                )
                assert c_b is not None and c_b.fencing_token == 2

            # Revoked teacher authorization blocks execution cleanly
            with env.admin.begin() as conn:
                conn.execute(
                    text("UPDATE teacher_assignments SET active=false WHERE tenant_id=:t AND user_id=:u AND offering_id=:o"),
                    {"t": a.tenant, "u": a.teacher, "o": a.offering},
                )
            res_fenced = WorkflowWorkerRunner(db=env.app_db, handler=runner.handler, concept_handler=runner.concept_handler)._execute_claimed_job(c_b)
            assert res_fenced == "failed"
            j_rev = client.get(f"/api/v1/tenants/{a.tenant}/jobs/{fence_j}", headers=_hdr(a.teacher_token))
            assert j_rev.status_code == 403
            _ = StaleWorkerFenceError
    finally:
        runner.close()
