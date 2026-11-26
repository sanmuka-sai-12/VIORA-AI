"""Live PostgreSQL and HTTP end-to-end tests for Batch 2.4 durable workflows, recovery, caching, and isolation."""

from __future__ import annotations

import json
import os
import secrets
import threading
import time
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from PIL import Image
from psycopg import sql
from pydantic import SecretStr
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.schema import CreateSchema, DropSchema
from viora.authorization.public import AuthorizationService, bind_tenant
from viora.config import AuthSettings, Settings
from viora.contracts import ArtifactVersionRef, IdempotentRequest
from viora.database_roles import grant_application_privileges
from viora.db import Database
from viora.identity.public import AuthService, IdentityAttributeKind
from viora.main import create_app
from viora.sources.ocr import OcrLine, OcrPageResult
from viora.sources.public import (
    AdaptiveDocumentExtractor,
    RapidOcrBackend,
    clamp_source_limits,
)
from viora.storage.public import local_artifact_store
from viora.workflow.public import (
    DurableWorkflowService,
    SqlWorkflowRepository,
    SqlWorkflowWorkerStore,
    StaleWorkerFenceError,
)
from viora.workflow.worker import (
    ExtractionWorkerHandler,
    WorkflowWorkerRunner,
    build_worker_runner,
)

pytestmark = pytest.mark.postgres
ROOT = Path(__file__).resolve().parents[2]
CORPUS_DIR = ROOT / "benchmarks" / "educational_corpus"
MANIFEST_PATH = CORPUS_DIR / "ground_truth.json"
PASSWORD = SecretStr("batch 2.4 workflow passphrase 99")


def _load_sample(doc_id: str) -> tuple[dict, bytes]:
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    item = next(d for d in manifest["documents"] if d["doc_id"] == doc_id)
    return item, (CORPUS_DIR / item["relative_path"]).read_bytes()


class _DeterministicOcrFallback:
    """Fallback OCR backend used in CI environments where optional rapidocr is not installed."""

    @property
    def engine_id(self) -> str:
        return "test.ocr.fallback"

    @property
    def engine_version(self) -> str:
        return "1.0"

    def is_available(self) -> bool:
        return True

    def recognize_image(
        self, image: Image.Image, *, preprocess: bool = False
    ) -> OcrPageResult:
        _ = preprocess
        lines = (
            OcrLine(
                text="Chapter 4: Conservation of Mechanical Energy",
                confidence=0.95,
                x0=20.0,
                y0=20.0,
                x1=float(max(40, image.width - 20)),
                y1=50.0,
            ),
            OcrLine(
                text="K_initial + U_initial = K_final + U_final",
                confidence=0.92,
                x0=20.0,
                y0=65.0,
                x1=float(max(40, image.width - 20)),
                y1=95.0,
            ),
        )
        return OcrPageResult(
            lines=lines,
            image_width=image.width,
            image_height=image.height,
            engine_id=self.engine_id,
            engine_version=self.engine_version,
            mean_confidence=0.935,
        )


@dataclass(frozen=True)
class TenantActors:
    tenant: UUID
    offering: UUID
    other_offering: UUID
    owner: UUID
    teacher: UUID
    other_teacher: UUID
    student: UUID
    other_student: UUID
    owner_token: str
    teacher_token: str
    other_teacher_token: str
    student_token: str
    other_student_token: str


@dataclass(frozen=True)
class WorkflowEnv:
    admin: Engine
    app_db: Database
    settings: Settings
    storage_dir: Path
    a: TenantActors
    b: TenantActors


def _provision_school(conn, auth: AuthService, name: str) -> TenantActors:
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
        text("INSERT INTO departments (tenant_id, id, name) VALUES (:tenant, :dept, 'Physics')"),
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

    conn.execute(
        text("INSERT INTO teacher_assignments VALUES (:tenant, :teacher, :offering, true)"),
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
        student=ids["student"],
        other_student=ids["other_student"],
        owner_token=emails["owner"],
        teacher_token=emails["teacher"],
        other_teacher_token=emails["other_teacher"],
        student_token=emails["student"],
        other_student_token=emails["other_student"],
    )


@pytest.fixture()
def env(tmp_path: Path) -> Iterator[WorkflowEnv]:
    raw = os.environ.get("VIORA_TEST_DATABASE_URL")
    if not raw:
        pytest.skip("Dedicated viora_test database required")
    url = make_url(raw)
    if url.database != "viora_test":
        pytest.fail("Requires dedicated viora_test database")

    schema = f"wf_{uuid4().hex}"
    role = f"wf_app_{uuid4().hex}"
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
        source_max_bytes=2 * 1024 * 1024,
        source_max_pdf_pages=30,
        source_max_text_chars=100_000,
        worker_concurrency=2,
        worker_page_workers=2,
        worker_lease_seconds=10,
        worker_heartbeat_seconds=2,
        auth=AuthSettings(signing_key=SecretStr("w" * 64)),
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
            a_raw = _provision_school(conn, auth, "Alpha School")
            b_raw = _provision_school(conn, auth, "Beta School")

        def login(email: str) -> str:
            return auth.login(
                IdentityAttributeKind.email, email, PASSWORD
            ).access_token.get_secret_value()

        a = TenantActors(
            **{
                **a_raw.__dict__,
                "owner_token": login(a_raw.owner_token),
                "teacher_token": login(a_raw.teacher_token),
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
                "other_teacher_token": login(b_raw.other_teacher_token),
                "student_token": login(b_raw.student_token),
                "other_student_token": login(b_raw.other_student_token),
            }
        )
        yield WorkflowEnv(admin, app_db, settings, storage_dir, a, b)
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


def _effective_ocr_backend():
    real = RapidOcrBackend()
    return real if real.is_available() else _DeterministicOcrFallback()


def test_end_to_end_six_document_types_and_workflow_protocol(env: WorkflowEnv) -> None:
    a = env.a
    runner = build_worker_runner(
        env.settings,
        db=env.app_db,
        ocr_backend=_effective_ocr_backend(),
    )
    with TestClient(create_app(env.settings, env.app_db)) as client:
        # 1. Typed notes (fast synchronous + derived_extractions v1)
        _, md_bytes = _load_sample("01_markdown_notes")
        r_md = client.post(
            f"/api/v1/tenants/{a.tenant}/offerings/{a.offering}/sources",
            headers=_hdr(a.teacher_token),
            json={
                "title": "1. Typed Notes",
                "input_format": "typed_notes",
                "content": md_bytes.decode("utf-8"),
                "origin_kind": "teacher_resource",
                "visibility": "offering_published",
            },
        )
        assert r_md.status_code == 201
        md_src, md_ver = r_md.json()["source"]["source_id"], r_md.json()["version"]["version_id"]
        ext_md = client.get(
            f"/api/v1/tenants/{a.tenant}/sources/{md_src}/versions/{md_ver}/extraction",
            headers=_hdr(a.student_token),
        )
        assert ext_md.status_code == 200
        assert "Classical Kinematics" in ext_md.json()["document"]["normalized_text"]

        # 2-6. Upload digital PDF, multi-column/table PDF, scanned PDF, PNG/JPEG image, mixed PDF
        uploads = [
            ("2. Digital PDF", "02_single_column_textbook", "textbook.pdf", "application/pdf", "digital_pdf", "Huygens Wavefront Principle"),
            ("3. Table PDF", "05_tables_bordered_and_borderless", "tables.pdf", "application/pdf", "digital_pdf", "Helium"),
            ("4. Scanned PDF", "07_scanned_printed_page_pdf", "scanned.pdf", "application/pdf", "scanned_pdf", "Conservation of Mechanical Energy"),
            ("5. Direct PNG", "07_scanned_printed_page_png", "page.png", "image/png", "png", "Conservation of Mechanical Energy"),
            ("6. Mixed PDF", "11_mixed_digital_scanned", "mixed.pdf", "application/pdf", "digital_pdf", "Left Column: First Law"),
        ]
        created: list[tuple[str, str, str, str]] = []
        for title, doc_id, fname, ctype, fmt, marker in uploads:
            _, raw = _load_sample(doc_id)
            resp = client.post(
                f"/api/v1/tenants/{a.tenant}/offerings/{a.offering}/sources/files",
                headers=_hdr(a.teacher_token),
                data={"title": title, "input_format": fmt, "visibility": "offering_published"},
                files={"file": (fname, raw, ctype)},
            )
            assert resp.status_code == 201, resp.text
            payload = resp.json()
            assert payload["job_id"] is not None
            assert payload["job_state"] == "queued"
            assert payload["version"]["processing_status"] == "pending"
            created.append((payload["source"]["source_id"], payload["version"]["version_id"], payload["job_id"], marker))

        summary = runner.drain()
        assert summary["succeeded"] == 5

        for src_id, ver_id, job_id, marker in created:
            j = client.get(f"/api/v1/tenants/{a.tenant}/jobs/{job_id}", headers=_hdr(a.teacher_token))
            assert j.status_code == 200
            assert j.json()["state"] == "succeeded"
            ex = client.get(
                f"/api/v1/tenants/{a.tenant}/sources/{src_id}/versions/{ver_id}/extraction",
                headers=_hdr(a.student_token),
            )
            assert ex.status_code == 200
            assert marker in ex.json()["document"]["normalized_text"]
            assert ex.json()["extraction_version"] == 1

        # Verify Workflow Protocol implementation directly (DurableWorkflowService.submit)
        auth = AuthService(env.app_db.sessions, env.settings.auth)  # type: ignore[arg-type]
        gate = AuthorizationService(env.app_db, auth)
        wf = DurableWorkflowService(gate, SqlWorkflowRepository, bearer=a.teacher_token)
        src_id, ver_id, _, _ = created[0]
        ver_detail = client.get(
            f"/api/v1/tenants/{a.tenant}/sources/{src_id}/versions/{ver_id}",
            headers=_hdr(a.teacher_token),
        ).json()
        ref = ArtifactVersionRef.model_validate(ver_detail["storage_ref"])
        accepted = wf.submit(
            IdempotentRequest(
                actor_id=a.teacher,
                tenant_id=a.tenant,
                operation="source_extract",
                key="proto-submit-1",
                payload_hash=ref.sha256,
            ),
            ref,
        )
        assert accepted.job_id is not None
        assert runner.drain()["succeeded"] == 1

        # Triggering extraction on same version creates extraction_version == 2 (cache hit!)
        ex2 = client.get(
            f"/api/v1/tenants/{a.tenant}/sources/{src_id}/versions/{ver_id}/extraction",
            headers=_hdr(a.teacher_token),
        )
        assert ex2.status_code == 200
        assert ex2.json()["extraction_version"] == 2
        assert ex2.json()["cache_hit"] is True


def test_crash_expired_lease_stale_fence_concurrent_claims_and_retries(env: WorkflowEnv) -> None:
    a = env.a
    _, tbl_bytes = _load_sample("05_tables_bordered_and_borderless")
    with TestClient(create_app(env.settings, env.app_db)) as client:
        job_ids: list[str] = []
        src_vers: list[tuple[str, str]] = []
        for idx in range(4):
            up = client.post(
                f"/api/v1/tenants/{a.tenant}/offerings/{a.offering}/sources/files",
                headers=_hdr(a.teacher_token),
                data={"title": f"Concurrent Doc {idx}"},
                files={"file": (f"doc_{idx}.pdf", tbl_bytes + f"\n%pad-{idx}".encode(), "application/pdf")},
            )
            assert up.status_code == 201
            job_ids.append(up.json()["job_id"])
            src_vers.append((up.json()["source"]["source_id"], up.json()["version"]["version_id"]))

        # 1. Simulate crash of Worker A after claiming job 0, then Worker B recovers expired lease
        with env.app_db.sessions.begin() as s:
            claimed_a = SqlWorkflowWorkerStore.claim_next_job(
                s, worker_id="worker-A", lease_seconds=10, now=datetime.now(UTC)
            )
        assert claimed_a is not None
        assert claimed_a.fencing_token == 1

        # Expire lease in DB to simulate Worker A crash
        with env.admin.begin() as conn:
            conn.execute(
                text(
                    "UPDATE workflow_jobs SET lease_expires_at = :exp WHERE tenant_id = :t AND job_id = :j"
                ),
                {"exp": datetime.now(UTC) - timedelta(seconds=5), "t": a.tenant, "j": claimed_a.job_id},
            )

        # Worker B recovers the expired lease with fencing_token == 2
        with env.app_db.sessions.begin() as s:
            claimed_b = SqlWorkflowWorkerStore.claim_next_job(
                s, worker_id="worker-B", lease_seconds=10, now=datetime.now(UTC)
            )
        assert claimed_b is not None
        assert claimed_b.job_id == claimed_a.job_id
        assert claimed_b.fencing_token == 2

        # Stale Worker A wakes up and tries to renew lease & finalize -> REJECTED!
        with env.app_db.sessions.begin() as s:
            bind_tenant(s, a.tenant)
            res = SqlWorkflowWorkerStore.renew_lease(
                s,
                tenant_id=a.tenant,
                job_id=claimed_a.job_id,
                worker_id="worker-A",
                fencing_token=1,
                lease_seconds=10,
                stage="ocr",
                progress_metadata={},
                checkpoint_data=None,
                now=datetime.now(UTC),
            )
            assert res.renewed is False

        with pytest.raises(StaleWorkerFenceError), env.app_db.sessions.begin() as s:
            bind_tenant(s, a.tenant)
            SqlWorkflowWorkerStore.finalize_succeeded(
                s,
                tenant_id=a.tenant,
                job_id=claimed_a.job_id,
                worker_id="worker-A",
                fencing_token=1,
                extraction_id=uuid4(),
                result_metadata={},
                progress_metadata={},
                now=datetime.now(UTC),
            )

        # Execute Worker B's recovered job cleanly
        runner_b = build_worker_runner(env.settings, db=env.app_db, worker_id="worker-B")
        assert runner_b._execute_claimed_job(claimed_b) == "succeeded"

        # 2. Concurrent FOR UPDATE SKIP LOCKED claiming across 2 workers for the remaining 3 jobs
        runner_1 = build_worker_runner(env.settings, db=env.app_db, worker_id="w-1")
        runner_2 = build_worker_runner(env.settings, db=env.app_db, worker_id="w-2")
        with ThreadPoolExecutor(max_workers=2) as pool:
            f1 = pool.submit(runner_1.drain)
            f2 = pool.submit(runner_2.drain)
            c1, c2 = f1.result(), f2.result()
        assert c1["succeeded"] + c2["succeeded"] == 3

        # 3. Transient retry -> retry_wait -> succeeded; permanent exhaustion -> failed
        up_retry = client.post(
            f"/api/v1/tenants/{a.tenant}/offerings/{a.offering}/sources/files",
            headers=_hdr(a.teacher_token),
            data={"title": "Retryable Source"},
            files={"file": ("retry.pdf", tbl_bytes + b"\n%retry", "application/pdf")},
        )
        r_job_id = UUID(up_retry.json()["job_id"])

        class _FlakyExtractor(AdaptiveDocumentExtractor):
            def __init__(self) -> None:
                super().__init__()
                self.calls = 0

            def extract(self, **kwargs):
                self.calls += 1
                if self.calls == 1:
                    raise OSError("Simulated transient I/O glitch at C:\\secret\\internal\\path")
                return super().extract(**kwargs)

        store = local_artifact_store(env.storage_dir, max_artifact_bytes=env.settings.source_max_bytes)
        limits = clamp_source_limits(
            max_bytes=env.settings.source_max_bytes,
            max_pdf_pages=env.settings.source_max_pdf_pages,
            max_text_chars=env.settings.source_max_text_chars,
            min_text_chars=env.settings.source_min_text_chars,
        )
        flaky_handler = ExtractionWorkerHandler(
            db=env.app_db,
            access=AuthorizationService(env.app_db, None),
            artifact_store=store,
            limits=limits,
            extractor=_FlakyExtractor(),
        )
        flaky_runner = WorkflowWorkerRunner(
            db=env.app_db, handler=flaky_handler, worker_id="flaky-w", retry_base_seconds=0.1
        )
        assert flaky_runner.claim_and_process_one() == "retry_wait"
        j_wait = client.get(f"/api/v1/tenants/{a.tenant}/jobs/{r_job_id}", headers=_hdr(a.teacher_token)).json()
        assert j_wait["state"] == "retry_wait"
        assert j_wait["attempt_count"] == 1
        # Sanitized error never leaks internal Windows path
        assert "C:\\" not in (j_wait["last_error_message"] or "")
        time.sleep(0.25)
        assert flaky_runner.claim_and_process_one() == "succeeded"
        j_ok = client.get(f"/api/v1/tenants/{a.tenant}/jobs/{r_job_id}", headers=_hdr(a.teacher_token)).json()
        assert j_ok["state"] == "succeeded"
        assert j_ok["attempt_count"] == 2


def test_cancellation_revocation_duplicate_coalescing_and_cache_isolation(env: WorkflowEnv) -> None:
    a, b = env.a, env.b
    _, pdf_bytes = _load_sample("02_single_column_textbook")
    with TestClient(create_app(env.settings, env.app_db)) as client:
        # 1. Queued cancellation + manual retry
        up_c = client.post(
            f"/api/v1/tenants/{a.tenant}/offerings/{a.offering}/sources/files",
            headers=_hdr(a.teacher_token),
            data={"title": "Cancel Then Retry"},
            files={"file": ("cancel.pdf", pdf_bytes, "application/pdf")},
        )
        c_src, c_ver, c_job = (
            up_c.json()["source"]["source_id"],
            up_c.json()["version"]["version_id"],
            up_c.json()["job_id"],
        )
        # Student cannot see teacher_only job or extraction
        assert (
            client.get(
                f"/api/v1/tenants/{a.tenant}/jobs/{c_job}",
                headers=_hdr(a.student_token),
            ).status_code
            == 403
        )
        # Cross-tenant cannot see or cancel
        assert (
            client.post(
                f"/api/v1/tenants/{a.tenant}/jobs/{c_job}/cancel",
                headers=_hdr(b.teacher_token),
            ).status_code
            == 403
        )
        can = client.post(
            f"/api/v1/tenants/{a.tenant}/jobs/{c_job}/cancel",
            headers=_hdr(a.teacher_token),
        )
        assert can.status_code == 200
        assert can.json()["state"] == "cancelled"

        runner = build_worker_runner(env.settings, db=env.app_db, ocr_backend=_effective_ocr_backend())
        assert runner.drain()["claimed"] == 0

        # Retry cancelled job -> re-queues and succeeds
        ret = client.post(
            f"/api/v1/tenants/{a.tenant}/jobs/{c_job}/retry",
            headers=_hdr(a.teacher_token),
        )
        assert ret.status_code == 200
        assert ret.json()["state"] == "queued"
        assert runner.drain()["succeeded"] == 1

        # 2. Cooperative cancellation while running
        t_running = client.post(
            f"/api/v1/tenants/{a.tenant}/sources/{c_src}/versions/{c_ver}/extract",
            headers=_hdr(a.teacher_token),
        )
        assert t_running.status_code == 202
        run_job_id = t_running.json()["job_id"]

        entered = threading.Event()
        proceed = threading.Event()

        class _SlowExtractor(AdaptiveDocumentExtractor):
            def extract(self, **kwargs):
                entered.set()
                proceed.wait(timeout=5.0)
                cb = kwargs.get("cancel_check")
                if cb and cb():
                    from viora.sources.public import ExtractionCancelledError

                    raise ExtractionCancelledError("Observed cooperative cancel")
                return super().extract(**kwargs)

        slow_handler = ExtractionWorkerHandler(
            db=env.app_db,
            access=AuthorizationService(env.app_db, None),
            artifact_store=local_artifact_store(env.storage_dir, max_artifact_bytes=env.settings.source_max_bytes),
            limits=clamp_source_limits(max_bytes=env.settings.source_max_bytes),
            extractor=_SlowExtractor(),
            heartbeat_seconds=1,
        )
        slow_runner = WorkflowWorkerRunner(db=env.app_db, handler=slow_handler)
        t_bg = threading.Thread(target=slow_runner.claim_and_process_one)
        t_bg.start()
        assert entered.wait(timeout=5.0)
        assert (
            client.post(
                f"/api/v1/tenants/{a.tenant}/jobs/{run_job_id}/cancel",
                headers=_hdr(a.teacher_token),
            ).status_code
            == 200
        )
        # Wait briefly for heartbeat to observe cancel_requested=True
        time.sleep(1.3)
        proceed.set()
        t_bg.join(timeout=5.0)
        j_run_can = client.get(
            f"/api/v1/tenants/{a.tenant}/jobs/{run_job_id}",
            headers=_hdr(a.teacher_token),
        ).json()
        assert j_run_can["state"] == "cancelled"

        # 3. Duplicate concurrent extraction requests coalesce without duplicate logical outputs
        with ThreadPoolExecutor(max_workers=2) as pool:
            f1 = pool.submit(
                lambda: client.post(
                    f"/api/v1/tenants/{a.tenant}/sources/{c_src}/versions/{c_ver}/extract",
                    headers=_hdr(a.teacher_token),
                )
            )
            f2 = pool.submit(
                lambda: client.post(
                    f"/api/v1/tenants/{a.tenant}/sources/{c_src}/versions/{c_ver}/extract",
                    headers=_hdr(a.teacher_token),
                )
            )
            r1, r2 = f1.result(), f2.result()
        assert r1.status_code == 202 and r2.status_code == 202
        assert r1.json()["job_id"] == r2.json()["job_id"]
        assert runner.drain()["succeeded"] == 1

        # 4. Cache isolation across student submissions, offerings, and tenants!
        # Student 1 submits identical pdf_bytes -> MUST be a cache miss (isolated from teacher cache!)
        st1 = client.post(
            f"/api/v1/tenants/{a.tenant}/offerings/{a.offering}/sources/files",
            headers=_hdr(a.student_token),
            data={"title": "Student 1 PDF", "origin_kind": "student_submission"},
            files={"file": ("s1.pdf", pdf_bytes, "application/pdf")},
        )
        assert st1.status_code == 201
        # Tenant B teacher submits identical pdf_bytes -> MUST be a cache miss (isolated from Tenant A!)
        tb = client.post(
            f"/api/v1/tenants/{b.tenant}/offerings/{b.offering}/sources/files",
            headers=_hdr(b.teacher_token),
            data={"title": "Tenant B PDF"},
            files={"file": ("b.pdf", pdf_bytes, "application/pdf")},
        )
        assert tb.status_code == 201
        assert runner.drain()["succeeded"] == 2
        for tok, tid, sid, vid in (
            (a.student_token, a.tenant, st1.json()["source"]["source_id"], st1.json()["version"]["version_id"]),
            (b.teacher_token, b.tenant, tb.json()["source"]["source_id"], tb.json()["version"]["version_id"]),
        ):
            ex = client.get(
                f"/api/v1/tenants/{tid}/sources/{sid}/versions/{vid}/extraction",
                headers=_hdr(tok),
            ).json()
            assert ex["cache_hit"] is False
            assert ex["source_id"] == sid
            assert ex["version_id"] == vid
            assert all(
                ev["source_id"] == sid and ev["version_id"] == vid
                for ev in ex["document"]["evidence_references"]
            )

        # 5. Revocation BEFORE processing prevents extraction/publication!
        rev_up = client.post(
            f"/api/v1/tenants/{a.tenant}/offerings/{a.offering}/sources/files",
            headers=_hdr(a.teacher_token),
            data={"title": "To Be Revoked", "visibility": "offering_published"},
            files={"file": ("revoked.pdf", pdf_bytes + b"\n%rev", "application/pdf")},
        )
        assert rev_up.status_code == 201
        rev_src, rev_ver, rev_job = (
            rev_up.json()["source"]["source_id"],
            rev_up.json()["version"]["version_id"],
            rev_up.json()["job_id"],
        )
        with env.admin.begin() as conn:
            conn.execute(
                text(
                    "UPDATE teacher_assignments SET active=false WHERE tenant_id=:t AND user_id=:u AND offering_id=:o"
                ),
                {"t": a.tenant, "u": a.teacher, "o": a.offering},
            )
        assert runner.drain()["failed"] == 1
        j_rev = client.get(
            f"/api/v1/tenants/{a.tenant}/jobs/{rev_job}",
            headers=_hdr(a.student_token),
        ).json()
        assert j_rev["state"] == "failed"
        assert j_rev["last_error_code"] == "authorization_revoked"
        assert (
            client.get(
                f"/api/v1/tenants/{a.tenant}/sources/{rev_src}/versions/{rev_ver}/extraction",
                headers=_hdr(a.student_token),
            ).status_code
            == 404
        )

        # 6. Append-only RLS on derived_extractions + temp orphan cleanup
        with pytest.raises(DBAPIError), env.app_db.sessions.begin() as s:
            bind_tenant(s, a.tenant)
            s.execute(text("UPDATE derived_extractions SET char_count=99999"))

        stale_tmp = env.storage_dir / ".tmp-crashed-write"
        stale_tmp.write_bytes(b"partial")
        os.utime(stale_tmp, (time.time() - 600, time.time() - 600))
        orphan_dir = env.storage_dir / "tenants" / str(a.tenant) / str(uuid4())
        orphan_dir.mkdir(parents=True, exist_ok=True)
        orphan_vid = uuid4()
        orphan_blob = orphan_dir / f"{orphan_vid}.blob"
        orphan_meta = orphan_dir / f"{orphan_vid}.meta.json"
        orphan_blob.write_bytes(b"orphan-crash")
        orphan_meta.write_text("{}", encoding="utf-8")
        old_ts = time.time() - 7200
        os.utime(orphan_blob, (old_ts, old_ts))
        os.utime(orphan_meta, (old_ts, old_ts))
        reconcile = runner.reconcile_orphans(min_age_seconds=3600)
        assert reconcile["temps_removed"] == 1
        assert reconcile["blobs_removed"] == 1
        assert reconcile["metas_removed"] == 1
        assert len(list(env.storage_dir.rglob("*.blob"))) >= 3
        assert (
            client.get(
                f"/api/v1/tenants/{a.tenant}/jobs/{rev_job}",
                headers=_hdr(b.teacher_token),
            ).status_code
            == 403
        )
        assert runner.handler.extractor.page_workers * runner.concurrency <= max(
            2, os.cpu_count() or 2
        )
