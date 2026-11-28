"""Live PostgreSQL + RLS + Worker + ArtifactStore + HTTP tests for Batch 3.5A lesson blueprints."""

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
from viora.ai.public import AIRequestConfig, ChatMessage, FakeAIGateway
from viora.authorization.public import bind_tenant
from viora.config import AuthSettings, Settings
from viora.contracts import TenantContext
from viora.database_roles import grant_application_privileges
from viora.db import Database
from viora.identity.public import AuthService, IdentityAttributeKind
from viora.knowledge.public import (
    DeterministicFakeEmbedder,
    RawBlueprintAssessment,
    RawBlueprintExplanation,
    RawBlueprintNarration,
    RawBlueprintObjective,
    RawBlueprintScene,
    RawBlueprintWorkedExample,
    RawConceptCandidate,
    RawConceptExtractionBatch,
    RawLessonBlueprintBatch,
    RawNarrationCue,
    RawRelationshipCandidate,
    RawSceneAction,
    RawSceneObject,
)
from viora.main import create_app
from viora.storage.public import local_artifact_store
from viora.workflow.worker import WorkflowWorkerRunner, build_worker_runner

pytestmark = pytest.mark.postgres
ROOT = Path(__file__).resolve().parents[2]
PASSWORD = SecretStr("phase 3 blueprint integration passphrase 42")


class _ScriptedBlueprintGateway(FakeAIGateway):
    """Handles concept extraction + lesson blueprint generation + optional mid-inference hook."""

    def __init__(self, *, on_blueprint_inference=None) -> None:
        super().__init__(model_name="fake-blueprint-model")
        self.on_blueprint_inference = on_blueprint_inference

    def complete_structured(
        self,
        context: TenantContext,
        messages: list[ChatMessage],
        response_model,
        config: AIRequestConfig | None = None,
    ):
        user_txt = messages[-1].content
        if response_model is RawConceptExtractionBatch:
            evs: list[str] = []
            pos = 0
            while True:
                idx = user_txt.find("[ev:", pos)
                if idx == -1:
                    break
                end = user_txt.find("]", idx)
                if end == -1:
                    break
                for eid in user_txt[idx + 4 : end].split(","):
                    if eid.strip() and eid.strip() not in evs:
                        evs.append(eid.strip())
                pos = end + 1
            cited = evs[:1] or ["ev-1"]
            self.structured_payloads[response_model] = RawConceptExtractionBatch(
                section_summary="Arrays and binary search",
                concepts=[
                    RawConceptCandidate(
                        name="Sorted Array",
                        canonical_summary="A sorted array arranges keys monotonically.",
                        grounding="source_explicit",
                        evidence_ids=cited,
                    ),
                    RawConceptCandidate(
                        name="Binary Search",
                        canonical_summary="Binary search halves the sorted interval.",
                        grounding="source_explicit",
                        evidence_ids=cited,
                        prerequisites=["Sorted Array"],
                    ),
                    RawConceptCandidate(
                        name="Rejected Heuristic",
                        canonical_summary="Rejected speculation must never enter blueprint.",
                        grounding="uncertain",
                        evidence_ids=[],
                    ),
                ],
                relationships=[
                    RawRelationshipCandidate(
                        source_concept="Sorted Array",
                        relation_type="prerequisite_of",
                        target_concept="Binary Search",
                        grounding="source_explicit",
                        evidence_ids=cited,
                    )
                ],
            )
            return super().complete_structured(context, messages, response_model, config)

        if response_model is RawLessonBlueprintBatch:
            if callable(self.on_blueprint_inference):
                self.on_blueprint_inference()
            cids: list[str] = []
            for line in user_txt.splitlines():
                if "id=" in line:
                    cid = line.split("id=", 1)[1].split(" ", 1)[0].strip()
                    if cid:
                        cids.append(cid)
            ev_id = "ev-1"
            ev_idx = user_txt.find("ev=[")
            if ev_idx != -1:
                ev_end = user_txt.find("]", ev_idx)
                raw_ev = user_txt[ev_idx + 4 : ev_end].split(",", 1)[0].strip()
                if raw_ev:
                    ev_id = raw_ev
            first_cid = cids[0]
            second_cid = cids[1] if len(cids) > 1 else first_cid
            self.structured_payloads[response_model] = RawLessonBlueprintBatch(
                lesson_summary="Ordered lesson blueprint for sorted arrays and binary search.",
                objectives=[
                    RawBlueprintObjective(concept_id=first_cid, statement="Verify sorted precondition.", bloom_level="understand", evidence_ids=[ev_id]),
                    RawBlueprintObjective(concept_id=second_cid, statement="Trace binary search halving.", bloom_level="apply", evidence_ids=[ev_id]),
                ],
                explanations=[
                    RawBlueprintExplanation(concept_id=second_cid, explanation="Binary search halves the sorted interval.", key_points=["O(log n)"], evidence_ids=[ev_id]),
                    RawBlueprintExplanation(concept_id=first_cid, explanation="A sorted array arranges keys monotonically.", key_points=["Prerequisite"], evidence_ids=[ev_id]),
                ],
                worked_examples=[
                    RawBlueprintWorkedExample(
                        concept_id=second_cid,
                        title="Search 7 in [1, 4, 7, 9]",
                        problem_statement="Find 7 in [1, 4, 7, 9].",
                        steps=["mid=1 -> 4 < 7", "mid=2 -> 7 == 7"],
                        final_answer="Index 2",
                        evidence_ids=[ev_id],
                    )
                ],
                scenes=[
                    RawBlueprintScene(
                        scene_id="bs-flow",
                        concept_ids=[second_cid],
                        template="process_flow",
                        visual_intent="Animate interval halving.",
                        timing_anchors=["start", "step", "end"],
                        objects=[RawSceneObject(id="arr", kind="array_cells", label="[1,4,7,9]")],
                        actions=[RawSceneAction(kind="highlight", target_object_id="arr", anchor="step")],
                        evidence_ids=[ev_id],
                    )
                ],
                narrations=[
                    RawBlueprintNarration(
                        scene_id="bs-flow",
                        concept_ids=[second_cid],
                        script_text="Inspect the middle element and discard half the array.",
                        caption_text="Discard half the array.",
                        cues=[RawNarrationCue(cue_id="c1", anchor="step", spoken_phrase="discard half")],
                        evidence_ids=[ev_id],
                    )
                ],
                assessments=[
                    RawBlueprintAssessment(
                        concept_id=second_cid,
                        question_type="mcq",
                        stem="What precondition does binary search require?",
                        options=["Unsorted input", "Sorted input"],
                        correct_option_index=1,
                        correct_answer="Sorted input",
                        explanation="Binary search relies on monotonic ordering.",
                        evidence_ids=[ev_id],
                    )
                ],
            )
            return super().complete_structured(context, messages, response_model, config)

        return super().complete_structured(context, messages, response_model, config)


@dataclass(frozen=True)
class _TenantActors:
    tenant: UUID
    offering: UUID
    teacher: UUID
    student: UUID
    teacher_token: str
    student_token: str


class _PgEnv:
    def __init__(self, tmp_path: Path) -> None:
        raw_admin = os.environ.get("VIORA_TEST_DATABASE_URL") or os.environ.get("DATABASE_URL")
        if not raw_admin:
            pytest.skip("PostgreSQL URL required")
        self.schema = f"viora_bp_{secrets.token_hex(4)}"
        self.app_role = f"viora_bp_app_{secrets.token_hex(4)}"
        self.app_pass = secrets.token_urlsafe(16)
        self.admin = create_engine(raw_admin, future=True)
        with self.admin.begin() as c:
            c.execute(CreateSchema(self.schema))
            c.connection.driver_connection.execute(
                sql.SQL(
                    "CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE NOREPLICATION NOINHERIT"
                ).format(sql.Identifier(self.app_role), sql.Literal(self.app_pass))
            )
        migrate_url = make_url(raw_admin).update_query_dict({"options": f"-csearch_path={self.schema}"})
        app_url = make_url(raw_admin).update_query_dict({"options": f"-csearch_path={self.schema},public"})
        cfg = Config(str(ROOT / "alembic.ini"))
        cfg.set_main_option("script_location", str(ROOT / "backend" / "migrations"))
        os.environ["MIGRATION_DATABASE_URL"] = migrate_url.render_as_string(hide_password=False)
        try:
            command.upgrade(cfg, "head")
        finally:
            os.environ.pop("MIGRATION_DATABASE_URL", None)

        with self.admin.begin() as c:
            grant_application_privileges(c, self.app_role, self.schema)
        app_url = app_url.set(username=self.app_role, password=self.app_pass)
        self.storage_dir = tmp_path / "storage"
        self.settings = Settings(
            database_url=SecretStr(app_url.render_as_string(hide_password=False)),
            auth=AuthSettings(
                signing_key=SecretStr("phase3-blueprint-signing-key-32bytes!"),
                cookie_secure=False,
            ),
            environment="test",
            media_storage_dir=str(self.storage_dir),
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


def _seed_tenant(admin: Engine, schema: str, db: Database, settings: Settings, slug: str) -> _TenantActors:
    auth = AuthService(db.sessions, settings.auth)  # type: ignore[arg-type]
    tenant_id, offering_id = uuid4(), uuid4()
    teacher, student = uuid4(), uuid4()
    pw_hash = auth.passwords.hash(PASSWORD)
    emails = {
        teacher: f"{slug}-tch-{teacher}@uni.edu",
        student: f"{slug}-stu-{student}@uni.edu",
    }
    with admin.begin() as c:
        c.execute(text(f"SET LOCAL search_path TO {schema}, public"))
        c.execute(text("INSERT INTO institutions VALUES (:t, :n, true)"), {"t": tenant_id, "n": slug.upper()})
        for uid, role in ((teacher, "subject_teacher"), (student, "student")):
            c.execute(text("INSERT INTO users VALUES (:u, :n, 'active')"), {"u": uid, "n": role})
            c.execute(text("INSERT INTO memberships VALUES (:t, :u, 'active')"), {"t": tenant_id, "u": uid})
            c.execute(text("INSERT INTO role_grants (tenant_id, user_id, role) VALUES (:t, :u, :r)"), {"t": tenant_id, "u": uid, "r": role})
            c.execute(text("INSERT INTO verified_identity_attributes VALUES (:u, 'email', :e, now())"), {"u": uid, "e": emails[uid]})
            c.execute(text("INSERT INTO password_credentials VALUES (:u, :h)"), {"u": uid, "h": pw_hash})
        yr, sem, sec, sub = uuid4(), uuid4(), uuid4(), uuid4()
        c.execute(text("INSERT INTO academic_years (tenant_id, id, name) VALUES (:t, :id, '2026')"), {"t": tenant_id, "id": yr})
        c.execute(text("INSERT INTO semesters (tenant_id, id, name, academic_year_id) VALUES (:t, :id, 'Fall', :y)"), {"t": tenant_id, "id": sem, "y": yr})
        c.execute(text("INSERT INTO sections (tenant_id, id, name, semester_id) VALUES (:t, :id, 'Sec-A', :s)"), {"t": tenant_id, "id": sec, "s": sem})
        c.execute(text("INSERT INTO subjects (tenant_id, id, name) VALUES (:t, :id, 'DSA')"), {"t": tenant_id, "id": sub})
        c.execute(text("INSERT INTO subject_offerings (tenant_id, id, name, section_id, subject_id) VALUES (:t, :id, 'DSA 101', :sec, :sub)"), {"t": tenant_id, "id": offering_id, "sec": sec, "sub": sub})
        c.execute(text("INSERT INTO teacher_assignments VALUES (:t, :u, :o, true)"), {"t": tenant_id, "o": offering_id, "u": teacher})
        c.execute(text("INSERT INTO student_enrolments VALUES (:t, :u, :o, true)"), {"t": tenant_id, "o": offering_id, "u": student})

    return _TenantActors(
        tenant=tenant_id,
        offering=offering_id,
        teacher=teacher,
        student=student,
        teacher_token=auth.login(IdentityAttributeKind.email, emails[teacher], PASSWORD).access_token.get_secret_value(),
        student_token=auth.login(IdentityAttributeKind.email, emails[student], PASSWORD).access_token.get_secret_value(),
    )


def test_phase3_lesson_blueprint_workflow_artifact_storage_and_authorization(pg_env: _PgEnv) -> None:
    store = local_artifact_store(pg_env.storage_dir)
    embedder = DeterministicFakeEmbedder()
    gw = _ScriptedBlueprintGateway()
    app = create_app(
        settings=pg_env.settings,
        database=pg_env.app_db,
        ai_gateway=gw,
        artifact_store=store,
    )
    runner: WorkflowWorkerRunner = build_worker_runner(
        pg_env.settings,
        db=pg_env.app_db,
        artifact_store=store,
        ai_gateway=gw,
        embedder=embedder,
    )
    a = _seed_tenant(pg_env.admin, pg_env.schema, pg_env.app_db, pg_env.settings, "bp1")
    b = _seed_tenant(pg_env.admin, pg_env.schema, pg_env.app_db, pg_env.settings, "bp2")

    try:
        with TestClient(app) as client:
            src_res = client.post(
                f"/api/v1/tenants/{a.tenant}/offerings/{a.offering}/sources",
                headers=_hdr(a.teacher_token),
                json={
                    "title": "Binary Search Lesson",
                    "input_format": "typed_notes",
                    "content": "A sorted array arranges keys monotonically.\n\nBinary search halves the sorted interval.",
                    "visibility": "teacher_only",
                },
            ).json()
            sid, vid = src_res["source"]["source_id"], src_res["version"]["version_id"]

            assert client.post(
                f"/api/v1/tenants/{a.tenant}/sources/{sid}/versions/{vid}/concepts/extract",
                headers=_hdr(a.teacher_token),
            ).status_code == 202
            assert runner.drain()["succeeded"] == 1

            # 1. Precondition check: unapproved curriculum cannot generate a blueprint (409)
            unapproved = client.post(
                f"/api/v1/tenants/{a.tenant}/sources/{sid}/versions/{vid}/blueprint/generate",
                headers=_hdr(a.teacher_token),
            )
            assert unapproved.status_code == 409

            ext = client.get(
                f"/api/v1/tenants/{a.tenant}/sources/{sid}/versions/{vid}/concepts",
                headers=_hdr(a.teacher_token),
            ).json()
            ce_id = ext["concept_extraction_id"]
            approved_ids = [
                c["concept_id"]
                for c in ext["knowledge"]["concepts"]
                if c["name"] in {"Sorted Array", "Binary Search"}
            ]
            rejected_ids = [
                c["concept_id"]
                for c in ext["knowledge"]["concepts"]
                if c["name"] == "Rejected Heuristic"
            ]
            assert client.post(
                f"/api/v1/tenants/{a.tenant}/sources/{sid}/versions/{vid}/concepts/review",
                headers=_hdr(a.teacher_token),
                json={
                    "concept_extraction_id": ce_id,
                    "decision": "approved",
                    "approved_concept_ids": approved_ids,
                    "rejected_concept_ids": rejected_ids,
                },
            ).status_code == 200

            # Student cannot trigger blueprint generation or read unpublished teacher draft
            assert client.post(
                f"/api/v1/tenants/{a.tenant}/sources/{sid}/versions/{vid}/blueprint/generate",
                headers=_hdr(a.student_token),
            ).status_code == 403

            # 2. Enqueue blueprint job with idempotency replay & execute in worker
            trig1 = client.post(
                f"/api/v1/tenants/{a.tenant}/sources/{sid}/versions/{vid}/blueprint/generate",
                headers={**_hdr(a.teacher_token), "Idempotency-Key": "bp-key-1"},
            )
            assert trig1.status_code == 202
            trig_replay = client.post(
                f"/api/v1/tenants/{a.tenant}/sources/{sid}/versions/{vid}/blueprint/generate",
                headers={**_hdr(a.teacher_token), "Idempotency-Key": "bp-key-1"},
            )
            assert trig_replay.json()["idempotent_replay"] is True
            assert trig_replay.json()["job_id"] == trig1.json()["job_id"]
            assert runner.drain()["succeeded"] == 1

            # 3. Teacher reads full blueprint backed by ArtifactStore
            bp_resp = client.get(
                f"/api/v1/tenants/{a.tenant}/sources/{sid}/versions/{vid}/blueprint",
                headers=_hdr(a.teacher_token),
            )
            assert bp_resp.status_code == 200
            bp_rec = bp_resp.json()
            bp = bp_rec["blueprint"]
            assert bp["approved_review_id"] == trig1.json()["approved_review_id"]
            assert bp["concept_extraction_id"] == ce_id
            assert bp["prerequisite_order"] == approved_ids
            assert [e["concept_id"] for e in bp["explanations"]] == approved_ids
            assert len(bp["learning_objectives"]) == 2
            assert len(bp["worked_examples"]) == 1
            assert len(bp["scenes"]) == 1
            assert len(bp["narrations"]) == 1
            assert bp["assessments"][0]["correct_answer"] == "Sorted input"
            assert "Rejected Heuristic" not in str(bp)

            versions = client.get(
                f"/api/v1/tenants/{a.tenant}/sources/{sid}/versions/{vid}/blueprint/versions",
                headers=_hdr(a.teacher_token),
            )
            assert versions.status_code == 200
            assert len(versions.json()["items"]) == 1

            # 4. Orphan reconciliation MUST preserve committed lesson_blueprints artifacts
            rec_stats = runner.reconcile_orphans(min_age_seconds=0)
            assert rec_stats["blobs_removed"] == 0
            assert client.get(
                f"/api/v1/tenants/{a.tenant}/sources/{sid}/versions/{vid}/blueprint",
                headers=_hdr(a.teacher_token),
            ).status_code == 200

            # 5. Unpublished blueprint stays hidden from students (403/404)
            assert client.get(
                f"/api/v1/tenants/{a.tenant}/sources/{sid}/versions/{vid}/blueprint/published",
                headers=_hdr(a.student_token),
            ).status_code == 403

            client.patch(
                f"/api/v1/tenants/{a.tenant}/sources/{sid}",
                headers=_hdr(a.teacher_token),
                json={"visibility": "offering_published"},
            )
            assert client.get(
                f"/api/v1/tenants/{a.tenant}/sources/{sid}/versions/{vid}/blueprint/published",
                headers=_hdr(a.student_token),
            ).status_code == 404

            # Publish concepts -> student STILL gets 404 until teacher explicitly approves the generated blueprint
            assert client.post(
                f"/api/v1/tenants/{a.tenant}/sources/{sid}/versions/{vid}/concepts/publish",
                headers=_hdr(a.teacher_token),
                json={"concept_extraction_id": ce_id, "publish": True},
            ).status_code == 200
            assert client.get(
                f"/api/v1/tenants/{a.tenant}/sources/{sid}/versions/{vid}/blueprint/published",
                headers=_hdr(a.student_token),
            ).status_code == 404

            # Student cannot approve/publish lesson blueprint (403)
            bp_id = bp_rec["blueprint_id"]
            assert client.post(
                f"/api/v1/tenants/{a.tenant}/sources/{sid}/versions/{vid}/blueprint/review",
                headers=_hdr(a.student_token),
                json={"blueprint_id": bp_id, "decision": "approved", "publish": True},
            ).status_code == 403

            # Reject + publish=True fails closed (409); approve + publish=False keeps student blocked (404)
            assert client.post(
                f"/api/v1/tenants/{a.tenant}/sources/{sid}/versions/{vid}/blueprint/review",
                headers=_hdr(a.teacher_token),
                json={"blueprint_id": bp_id, "decision": "rejected", "publish": True},
            ).status_code == 409
            assert client.post(
                f"/api/v1/tenants/{a.tenant}/sources/{sid}/versions/{vid}/blueprint/review",
                headers=_hdr(a.teacher_token),
                json={"blueprint_id": bp_id, "decision": "approved", "publish": False},
            ).status_code == 200
            assert client.get(
                f"/api/v1/tenants/{a.tenant}/sources/{sid}/versions/{vid}/blueprint/published",
                headers=_hdr(a.student_token),
            ).status_code == 404

            # Teacher approves and publishes generated blueprint -> student gets student-safe blueprint WITHOUT answer keys
            pub_bp_state = client.post(
                f"/api/v1/tenants/{a.tenant}/sources/{sid}/versions/{vid}/blueprint/review",
                headers=_hdr(a.teacher_token),
                json={"blueprint_id": bp_id, "decision": "approved", "publish": True},
            )
            assert pub_bp_state.status_code == 200
            assert pub_bp_state.json()["teacher_approved"] is True
            assert pub_bp_state.json()["published_to_students"] is True

            stu_bp = client.get(
                f"/api/v1/tenants/{a.tenant}/sources/{sid}/versions/{vid}/blueprint/published",
                headers=_hdr(a.student_token),
            )
            assert stu_bp.status_code == 200
            assert stu_bp.json()["blueprint_id"] == bp_id
            assert stu_bp.json()["teacher_approved"] is True
            assert stu_bp.json()["published_to_students"] is True
            stu_q = stu_bp.json()["assessments"][0]
            assert "correct_answer" not in stu_q
            assert "correct_option_index" not in stu_q
            assert "accepted_aliases" not in stu_q
            assert "explanation" not in stu_q

            # Regenerating a v2 blueprint NEVER leaks unapproved v2 to students; student stays pinned to approved v1
            assert client.post(
                f"/api/v1/tenants/{a.tenant}/sources/{sid}/versions/{vid}/blueprint/generate",
                headers={**_hdr(a.teacher_token), "Idempotency-Key": "bp-key-2"},
            ).status_code == 202
            assert runner.drain()["succeeded"] == 1
            stu_still_v1 = client.get(
                f"/api/v1/tenants/{a.tenant}/sources/{sid}/versions/{vid}/blueprint/published",
                headers=_hdr(a.student_token),
            )
            assert stu_still_v1.status_code == 200
            assert stu_still_v1.json()["blueprint_id"] == bp_id

            # Publication revocation immediately blocks student access (404)
            assert client.post(
                f"/api/v1/tenants/{a.tenant}/sources/{sid}/versions/{vid}/blueprint/review",
                headers=_hdr(a.teacher_token),
                json={"blueprint_id": bp_id, "decision": "changes_requested", "publish": False},
            ).status_code == 200
            assert client.get(
                f"/api/v1/tenants/{a.tenant}/sources/{sid}/versions/{vid}/blueprint/published",
                headers=_hdr(a.student_token),
            ).status_code == 404

            # Re-approve & re-publish, then unpublish concepts -> cascades revocation (404)
            assert client.post(
                f"/api/v1/tenants/{a.tenant}/sources/{sid}/versions/{vid}/blueprint/review",
                headers=_hdr(a.teacher_token),
                json={"blueprint_id": bp_id, "decision": "approved", "publish": True},
            ).status_code == 200
            assert client.post(
                f"/api/v1/tenants/{a.tenant}/sources/{sid}/versions/{vid}/concepts/publish",
                headers=_hdr(a.teacher_token),
                json={"concept_extraction_id": ce_id, "publish": False},
            ).status_code == 200
            assert client.get(
                f"/api/v1/tenants/{a.tenant}/sources/{sid}/versions/{vid}/blueprint/published",
                headers=_hdr(a.student_token),
            ).status_code == 404

            # Cross-tenant isolation & PostgreSQL RLS verification
            assert client.get(
                f"/api/v1/tenants/{a.tenant}/sources/{sid}/versions/{vid}/blueprint",
                headers=_hdr(b.teacher_token),
            ).status_code == 403
            with pg_env.app_db.sessions.begin() as s:
                bind_tenant(s, b.tenant)
                assert s.execute(text("SELECT count(*) FROM lesson_blueprints")).scalar() == 0
                assert s.execute(text("SELECT count(*) FROM lesson_blueprint_publications")).scalar() == 0
            with pg_env.app_db.sessions.begin() as s:
                bind_tenant(s, a.tenant)
                assert s.execute(text("SELECT count(*) FROM lesson_blueprints")).scalar() == 2
                assert s.execute(text("SELECT count(*) FROM lesson_blueprint_publications")).scalar() == 1
    finally:
        runner.close()


def test_phase3_blueprint_post_inference_revocation_fails_closed(pg_env: _PgEnv) -> None:
    store = local_artifact_store(pg_env.storage_dir)
    a = _seed_tenant(pg_env.admin, pg_env.schema, pg_env.app_db, pg_env.settings, "rev")

    def _revoke_teacher_mid_inference() -> None:
        with pg_env.admin.begin() as c:
            c.execute(text(f"SET LOCAL search_path TO {pg_env.schema}, public"))
            c.execute(
                text("UPDATE teacher_assignments SET active=false WHERE tenant_id=:t AND user_id=:u"),
                {"t": a.tenant, "u": a.teacher},
            )

    gw = _ScriptedBlueprintGateway(on_blueprint_inference=_revoke_teacher_mid_inference)
    app = create_app(
        settings=pg_env.settings,
        database=pg_env.app_db,
        ai_gateway=gw,
        artifact_store=store,
    )
    runner = build_worker_runner(
        pg_env.settings,
        db=pg_env.app_db,
        artifact_store=store,
        ai_gateway=gw,
    )
    try:
        with TestClient(app) as client:
            src_res = client.post(
                f"/api/v1/tenants/{a.tenant}/offerings/{a.offering}/sources",
                headers=_hdr(a.teacher_token),
                json={"title": "Revocation Check", "input_format": "typed_notes", "content": "A sorted array.\n\nBinary search halves."},
            ).json()
            sid, vid = src_res["source"]["source_id"], src_res["version"]["version_id"]
            client.post(f"/api/v1/tenants/{a.tenant}/sources/{sid}/versions/{vid}/concepts/extract", headers=_hdr(a.teacher_token))
            assert runner.drain()["succeeded"] == 1
            ce_id = client.get(f"/api/v1/tenants/{a.tenant}/sources/{sid}/versions/{vid}/concepts", headers=_hdr(a.teacher_token)).json()["concept_extraction_id"]
            client.post(
                f"/api/v1/tenants/{a.tenant}/sources/{sid}/versions/{vid}/concepts/review",
                headers=_hdr(a.teacher_token),
                json={"concept_extraction_id": ce_id, "decision": "approved"},
            )
            trig = client.post(
                f"/api/v1/tenants/{a.tenant}/sources/{sid}/versions/{vid}/blueprint/generate",
                headers=_hdr(a.teacher_token),
            ).json()

            res = runner.drain()
            assert res["failed"] == 1
            assert res["succeeded"] == 0
            with pg_env.app_db.sessions.begin() as s:
                bind_tenant(s, a.tenant)
                assert s.execute(text("SELECT count(*) FROM lesson_blueprints")).scalar() == 0
                job_err = s.execute(
                    text("SELECT last_error_code FROM workflow_jobs WHERE job_id=:j"),
                    {"j": trig["job_id"]},
                ).scalar_one()
                assert job_err == "authorization_revoked"
    finally:
        runner.close()


def test_phase3_blueprint_stale_worker_fencing_prevents_overwrite(pg_env: _PgEnv) -> None:
    store = local_artifact_store(pg_env.storage_dir)
    a = _seed_tenant(pg_env.admin, pg_env.schema, pg_env.app_db, pg_env.settings, "fence")
    stale_takeover_done = False

    def _steal_lease_mid_inference() -> None:
        nonlocal stale_takeover_done
        if stale_takeover_done:
            return
        # Re-open lease as expired in DB and let another worker advance fencing_token and complete the job
        with pg_env.admin.begin() as c:
            c.execute(text(f"SET LOCAL search_path TO {pg_env.schema}, public"))
            c.execute(
                text(
                    "UPDATE workflow_jobs SET state='running', lease_expires_at = now() - interval '5 minutes' "
                    "WHERE operation_kind='lesson_blueprint' AND state='running'"
                )
            )
        worker2 = build_worker_runner(
            pg_env.settings,
            db=pg_env.app_db,
            artifact_store=store,
            ai_gateway=_ScriptedBlueprintGateway(),
            worker_id="worker-takeover-2",
        )
        try:
            assert worker2.drain()["succeeded"] == 1
        finally:
            worker2.close()
        stale_takeover_done = True

    gw1 = _ScriptedBlueprintGateway(on_blueprint_inference=_steal_lease_mid_inference)
    app = create_app(
        settings=pg_env.settings,
        database=pg_env.app_db,
        ai_gateway=gw1,
        artifact_store=store,
    )
    runner1 = build_worker_runner(
        pg_env.settings,
        db=pg_env.app_db,
        artifact_store=store,
        ai_gateway=gw1,
        worker_id="worker-stale-1",
    )
    try:
        with TestClient(app) as client:
            src_res = client.post(
                f"/api/v1/tenants/{a.tenant}/offerings/{a.offering}/sources",
                headers=_hdr(a.teacher_token),
                json={
                    "title": "Fencing Check",
                    "input_format": "typed_notes",
                    "content": "A sorted array arranges keys.\n\nBinary search halves.",
                },
            ).json()
            sid, vid = src_res["source"]["source_id"], src_res["version"]["version_id"]
            client.post(f"/api/v1/tenants/{a.tenant}/sources/{sid}/versions/{vid}/concepts/extract", headers=_hdr(a.teacher_token))
            assert runner1.drain()["succeeded"] == 1
            ce_id = client.get(f"/api/v1/tenants/{a.tenant}/sources/{sid}/versions/{vid}/concepts", headers=_hdr(a.teacher_token)).json()["concept_extraction_id"]
            client.post(
                f"/api/v1/tenants/{a.tenant}/sources/{sid}/versions/{vid}/concepts/review",
                headers=_hdr(a.teacher_token),
                json={"concept_extraction_id": ce_id, "decision": "approved"},
            )
            trig = client.post(
                f"/api/v1/tenants/{a.tenant}/sources/{sid}/versions/{vid}/blueprint/generate",
                headers=_hdr(a.teacher_token),
            ).json()

            res1 = runner1.drain()
            assert res1["fenced"] == 1
            assert res1["succeeded"] == 0
            assert runner1.reconcile_orphans(min_age_seconds=0)["blobs_removed"] == 0

            with pg_env.app_db.sessions.begin() as s:
                bind_tenant(s, a.tenant)
                assert s.execute(text("SELECT count(*) FROM lesson_blueprints")).scalar() == 1
                state, fence = s.execute(
                    text("SELECT state, fencing_token FROM workflow_jobs WHERE job_id=:j"),
                    {"j": trig["job_id"]},
                ).one()
                assert state == "succeeded"
                assert fence == 2
    finally:
        runner1.close()


def test_migration_0013_upgrade_downgrade_isolated_postgres() -> None:
    raw_admin = os.environ.get("VIORA_TEST_DATABASE_URL") or os.environ.get("DATABASE_URL")
    if not raw_admin:
        pytest.skip("PostgreSQL URL required")
    schema = f"viora_m0013_{secrets.token_hex(4)}"
    admin = create_engine(raw_admin, future=True)
    migrate_url = make_url(raw_admin).update_query_dict({"options": f"-csearch_path={schema}"})
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "backend" / "migrations"))
    prev_mig = os.environ.get("MIGRATION_DATABASE_URL")
    os.environ["MIGRATION_DATABASE_URL"] = migrate_url.render_as_string(hide_password=False)
    try:
        with admin.begin() as c:
            c.execute(CreateSchema(schema))
        command.upgrade(cfg, "0012_knowledge_retrieval_index")
        command.upgrade(cfg, "0013_lesson_blueprints")
        with admin.connect() as c:
            c.execute(text(f"SET LOCAL search_path TO {schema}, public"))
            tables = set(
                c.execute(
                    text(
                        "SELECT tablename FROM pg_tables WHERE schemaname=:s"
                    ),
                    {"s": schema},
                ).scalars()
            )
            assert {"lesson_blueprints", "lesson_blueprint_publications"} <= tables
            rls_rows = c.execute(
                text(
                    "SELECT relname, relrowsecurity, relforcerowsecurity FROM pg_class c "
                    "JOIN pg_namespace n ON n.oid = c.relnamespace "
                    "WHERE n.nspname=:s AND c.relname IN ('lesson_blueprints', 'lesson_blueprint_publications')"
                ),
                {"s": schema},
            ).all()
            assert len(rls_rows) == 2
            assert all(row[1] and row[2] for row in rls_rows)

        command.upgrade(cfg, "head")
        command.check(cfg)
        command.downgrade(cfg, "0012_knowledge_retrieval_index")
        with admin.connect() as c:
            tables_after_down = set(
                c.execute(
                    text("SELECT tablename FROM pg_tables WHERE schemaname=:s"),
                    {"s": schema},
                ).scalars()
            )
            assert "lesson_blueprints" not in tables_after_down
            assert "lesson_blueprint_publications" not in tables_after_down

        command.upgrade(cfg, "head")
        command.check(cfg)
    finally:
        if prev_mig is None:
            os.environ.pop("MIGRATION_DATABASE_URL", None)
        else:
            os.environ["MIGRATION_DATABASE_URL"] = prev_mig
        with admin.begin() as c:
            c.execute(DropSchema(schema, cascade=True, if_exists=True))
        admin.dispose()
