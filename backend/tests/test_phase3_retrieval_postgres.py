"""Live PostgreSQL + RLS + pgvector + Worker + HTTP tests for Phase 3 indexing and retrieval."""

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
    KnowledgeRetrieveRequest,
    RawConceptCandidate,
    RawConceptExtractionBatch,
    RawRelationshipCandidate,
)
from viora.main import create_app
from viora.workflow.worker import WorkflowWorkerRunner, build_worker_runner

pytestmark = pytest.mark.postgres
ROOT = Path(__file__).resolve().parents[2]
PASSWORD = SecretStr("phase 3 retrieval integration passphrase 42")


class _ScriptedKinematicsGateway(FakeAIGateway):
    def complete_structured(
        self,
        context: TenantContext,
        messages: list[ChatMessage],
        response_model,
        config: AIRequestConfig | None = None,
    ):
        user_txt = messages[-1].content
        ev_ids: list[str] = []
        pos = 0
        while True:
            idx = user_txt.find("[ev:", pos)
            if idx == -1:
                break
            end = user_txt.find("]", idx)
            if end == -1:
                break
            for eid in user_txt[idx + 4 : end].split(","):
                if eid.strip() and eid.strip() not in ev_ids:
                    ev_ids.append(eid.strip())
            pos = end + 1
        cited = ev_ids[:1] or ["ev-1"]
        batch = RawConceptExtractionBatch(
            section_summary="Kinematics and energy concepts",
            concepts=[
                RawConceptCandidate(
                    name="Displacement",
                    canonical_summary="Signed change in position vector.",
                    definition="Delta x = x_final - x_initial.",
                    grounding="source_explicit",
                    evidence_ids=cited,
                ),
                RawConceptCandidate(
                    name="Velocity",
                    canonical_summary="Vector rate of change of displacement.",
                    definition="v = dx / dt.",
                    grounding="source_explicit",
                    evidence_ids=cited,
                    prerequisites=["Displacement"],
                ),
            ],
            relationships=[
                RawRelationshipCandidate(
                    source_concept="Displacement",
                    relation_type="prerequisite_of",
                    target_concept="Velocity",
                    grounding="source_explicit",
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
    student1: UUID
    student2: UUID
    teacher_token: str
    student1_token: str
    student2_token: str


class PgEnv:
    def __init__(self, tmp_path: Path) -> None:
        raw_admin = os.environ.get("VIORA_TEST_DATABASE_URL") or os.environ.get("DATABASE_URL")
        if not raw_admin:
            pytest.skip("PostgreSQL URL required")
        self.schema = f"viora_p3_{secrets.token_hex(4)}"
        self.app_role = f"viora_p3_app_{secrets.token_hex(4)}"
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
        self.settings = Settings(
            database_url=SecretStr(app_url.render_as_string(hide_password=False)),
            auth=AuthSettings(
                signing_key=SecretStr("phase3-retrieval-signing-key-32bytes!!"),
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
def pg_env(tmp_path: Path) -> Iterator[PgEnv]:
    env = PgEnv(tmp_path)
    try:
        yield env
    finally:
        env.close()


def _hdr(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _seed_tenant(admin: Engine, schema: str, db: Database, settings: Settings, slug: str) -> TenantActors:
    auth = AuthService(db.sessions, settings.auth)  # type: ignore[arg-type]
    assert settings.auth is not None
    tenant_id = uuid4()
    owner, teacher, s1, s2 = uuid4(), uuid4(), uuid4(), uuid4()
    offering_id = uuid4()
    pw_hash = auth.passwords.hash(PASSWORD)
    emails = {
        teacher: f"{slug}-tch-{teacher}@uni.edu",
        s1: f"{slug}-s1-{s1}@uni.edu",
        s2: f"{slug}-s2-{s2}@uni.edu",
        owner: f"{slug}-own-{owner}@uni.edu",
    }
    with admin.begin() as c:
        c.execute(text(f"SET LOCAL search_path TO {schema}, public"))
        c.execute(text("INSERT INTO institutions VALUES (:t, :n, true)"), {"t": tenant_id, "n": slug.upper()})
        for uid, role in ((owner, "institution_owner"), (teacher, "subject_teacher"), (s1, "student"), (s2, "student")):
            em = emails[uid]
            c.execute(text("INSERT INTO users VALUES (:u, :n, 'active')"), {"u": uid, "n": role})
            c.execute(text("INSERT INTO memberships VALUES (:t, :u, 'active')"), {"t": tenant_id, "u": uid})
            c.execute(text("INSERT INTO role_grants (tenant_id, user_id, role) VALUES (:t, :u, :r)"), {"t": tenant_id, "u": uid, "r": role})
            c.execute(text("INSERT INTO verified_identity_attributes VALUES (:u, 'email', :e, now())"), {"u": uid, "e": em})
            c.execute(text("INSERT INTO password_credentials VALUES (:u, :h)"), {"u": uid, "h": pw_hash})
        yr, sem, sec, sub = uuid4(), uuid4(), uuid4(), uuid4()
        c.execute(text("INSERT INTO academic_years (tenant_id, id, name) VALUES (:t, :id, '2026')"), {"t": tenant_id, "id": yr})
        c.execute(text("INSERT INTO semesters (tenant_id, id, name, academic_year_id) VALUES (:t, :id, 'Fall', :y)"), {"t": tenant_id, "id": sem, "y": yr})
        c.execute(text("INSERT INTO sections (tenant_id, id, name, semester_id) VALUES (:t, :id, 'Sec-A', :s)"), {"t": tenant_id, "id": sec, "s": sem})
        c.execute(text("INSERT INTO subjects (tenant_id, id, name) VALUES (:t, :id, 'Physics')"), {"t": tenant_id, "id": sub})
        c.execute(text("INSERT INTO subject_offerings (tenant_id, id, name, section_id, subject_id) VALUES (:t, :id, 'Physics 101', :sec, :sub)"), {"t": tenant_id, "id": offering_id, "sec": sec, "sub": sub})
        c.execute(text("INSERT INTO teacher_assignments VALUES (:t, :u, :o, true)"), {"t": tenant_id, "o": offering_id, "u": teacher})
        for stu in (s1, s2):
            c.execute(text("INSERT INTO student_enrolments VALUES (:t, :u, :o, true)"), {"t": tenant_id, "o": offering_id, "u": stu})

    t_tok = auth.login(IdentityAttributeKind.email, emails[teacher], PASSWORD).access_token.get_secret_value()
    s1_tok = auth.login(IdentityAttributeKind.email, emails[s1], PASSWORD).access_token.get_secret_value()
    s2_tok = auth.login(IdentityAttributeKind.email, emails[s2], PASSWORD).access_token.get_secret_value()
    return TenantActors(tenant_id, offering_id, teacher, s1, s2, t_tok, s1_tok, s2_tok)


def test_phase3_indexing_idempotency_hybrid_graph_and_strict_authorization(pg_env: PgEnv) -> None:
    app = create_app(settings=pg_env.settings, database=pg_env.app_db)
    embedder = DeterministicFakeEmbedder()
    gw = _ScriptedKinematicsGateway()
    runner: WorkflowWorkerRunner = build_worker_runner(
        pg_env.settings, db=pg_env.app_db, ai_gateway=gw, embedder=embedder
    )
    a = _seed_tenant(pg_env.admin, pg_env.schema, pg_env.app_db, pg_env.settings, "alpha")
    b = _seed_tenant(pg_env.admin, pg_env.schema, pg_env.app_db, pg_env.settings, "beta")

    try:
        with TestClient(app) as client:
            # 1. Create teacher source (initially teacher_only -> unpublished)
            src_res = client.post(
                f"/api/v1/tenants/{a.tenant}/offerings/{a.offering}/sources",
                headers=_hdr(a.teacher_token),
                json={
                    "title": "Kinematics Notes",
                    "input_format": "typed_notes",
                    "content": "# Classical Kinematics\n\nDisplacement is the signed change in position.\n\nVelocity is the vector rate of change of displacement: v = dx/dt.",
                    "visibility": "teacher_only",
                },
            )
            assert src_res.status_code == 201
            sid = src_res.json()["source"]["source_id"]
            vid = src_res.json()["version"]["version_id"]

            # Index before concept extraction -> works incrementally
            idx1 = client.post(
                f"/api/v1/tenants/{a.tenant}/sources/{sid}/versions/{vid}/index",
                headers={**_hdr(a.teacher_token), "Idempotency-Key": "idx-k1"},
            )
            assert idx1.status_code == 202
            idx1_replay = client.post(
                f"/api/v1/tenants/{a.tenant}/sources/{sid}/versions/{vid}/index",
                headers={**_hdr(a.teacher_token), "Idempotency-Key": "idx-k1"},
            )
            assert idx1_replay.json()["idempotent_replay"] is True
            assert idx1_replay.json()["job_id"] == idx1.json()["job_id"]
            assert runner.drain()["succeeded"] == 1

            status1 = client.get(
                f"/api/v1/tenants/{a.tenant}/sources/{sid}/versions/{vid}/index",
                headers=_hdr(a.teacher_token),
            )
            assert status1.status_code == 200
            assert status1.json()["indexed"] is True
            assert status1.json()["index_version"] == 1
            assert status1.json()["chunk_count"] >= 1

            # Re-trigger with nothing changed -> idempotent no-op (index_version remains 1)
            idx_noop = client.post(
                f"/api/v1/tenants/{a.tenant}/sources/{sid}/versions/{vid}/index",
                headers=_hdr(a.teacher_token),
            )
            assert idx_noop.status_code == 202
            assert runner.drain()["succeeded"] == 1
            job_noop = client.get(
                f"/api/v1/tenants/{a.tenant}/jobs/{idx_noop.json()['job_id']}",
                headers=_hdr(a.teacher_token),
            ).json()
            assert job_noop["result_metadata"]["idempotent_noop"] is True
            assert job_noop["result_metadata"]["index_version"] == 1

            # 2. Extract concepts, re-index (scoped embedding reuse -> computed_embeddings == 0!)
            ce_trig = client.post(
                f"/api/v1/tenants/{a.tenant}/sources/{sid}/versions/{vid}/concepts/extract",
                headers=_hdr(a.teacher_token),
            )
            assert ce_trig.status_code == 202
            assert runner.drain()["succeeded"] == 1

            idx2 = client.post(
                f"/api/v1/tenants/{a.tenant}/sources/{sid}/versions/{vid}/index",
                headers=_hdr(a.teacher_token),
            )
            assert idx2.status_code == 202
            assert runner.drain()["succeeded"] == 1
            job2 = client.get(
                f"/api/v1/tenants/{a.tenant}/jobs/{idx2.json()['job_id']}",
                headers=_hdr(a.teacher_token),
            ).json()
            assert job2["result_metadata"]["index_version"] == 2
            assert job2["result_metadata"]["computed_embeddings"] == 0
            assert job2["result_metadata"]["reused_embeddings"] >= 1
            ce_id = job2["result_metadata"] and client.get(
                f"/api/v1/tenants/{a.tenant}/sources/{sid}/versions/{vid}/concepts",
                headers=_hdr(a.teacher_token),
            ).json()["concept_extraction_id"]

            # 3. Teacher retrieves unpublished notes; Student 1 gets 0 chunks!
            q_body = {"query": "velocity displacement rate", "offering_id": str(a.offering), "mode": "hybrid_graph"}
            t_ret = client.post(f"/api/v1/tenants/{a.tenant}/knowledge/retrieve", headers=_hdr(a.teacher_token), json=q_body)
            assert t_ret.status_code == 200
            assert len(t_ret.json()["chunks"]) >= 1
            assert t_ret.json()["chunks"][0]["concept_extraction_id"] == ce_id
            assert len(t_ret.json()["graph_concepts"]) >= 1

            s_unpub = client.post(f"/api/v1/tenants/{a.tenant}/knowledge/retrieve", headers=_hdr(a.student1_token), json=q_body)
            assert s_unpub.status_code == 200
            assert s_unpub.json()["chunks"] == []

            # 4. Approve and publish -> visibility still teacher_only -> student STILL gets 0
            client.post(
                f"/api/v1/tenants/{a.tenant}/sources/{sid}/versions/{vid}/concepts/review",
                headers=_hdr(a.teacher_token),
                json={"concept_extraction_id": ce_id, "decision": "approved"},
            )
            # Set source visibility to offering_published and publish concepts
            assert (
                client.patch(
                    f"/api/v1/tenants/{a.tenant}/sources/{sid}",
                    headers=_hdr(a.teacher_token),
                    json={"visibility": "offering_published"},
                ).status_code
                == 200
            )
            assert (
                client.post(
                    f"/api/v1/tenants/{a.tenant}/sources/{sid}/versions/{vid}/concepts/publish",
                    headers=_hdr(a.teacher_token),
                    json={"concept_extraction_id": ce_id, "publish": True},
                ).status_code
                == 200
            )

            # Now Student 1 retrieves exact approved version + graph enrichment
            s_pub = client.post(f"/api/v1/tenants/{a.tenant}/knowledge/retrieve", headers=_hdr(a.student1_token), json=q_body)
            assert s_pub.status_code == 200
            assert len(s_pub.json()["chunks"]) >= 1
            assert s_pub.json()["chunks"][0]["version_id"] == vid
            assert s_pub.json()["chunks"][0]["concept_extraction_id"] == ce_id
            assert any(gc["name"] == "Displacement" for gc in s_pub.json()["graph_concepts"])

            # Cross-tenant retrieval strictly denied
            assert (
                client.post(
                    f"/api/v1/tenants/{a.tenant}/knowledge/retrieve",
                    headers=_hdr(b.teacher_token),
                    json=q_body,
                ).status_code
                == 403
            )

            # 5. Student-private submission isolation & offering policy check
            sub1 = client.post(
                f"/api/v1/tenants/{a.tenant}/offerings/{a.offering}/sources",
                headers=_hdr(a.student1_token),
                json={
                    "title": "My Private Physics Notes",
                    "input_format": "plain_text",
                    "content": "Private reflection: momentum equals mass times velocity.",
                    "origin_kind": "student_submission",
                    "visibility": "owner_private",
                },
            ).json()
            client.post(
                f"/api/v1/tenants/{a.tenant}/sources/{sub1['source']['source_id']}/versions/{sub1['version']['version_id']}/index",
                headers=_hdr(a.student1_token),
            )
            assert runner.drain()["succeeded"] == 1

            priv_q = {"query": "momentum mass velocity", "offering_id": str(a.offering), "mode": "hybrid"}
            s1_priv = client.post(f"/api/v1/tenants/{a.tenant}/knowledge/retrieve", headers=_hdr(a.student1_token), json=priv_q).json()
            s2_priv = client.post(f"/api/v1/tenants/{a.tenant}/knowledge/retrieve", headers=_hdr(a.student2_token), json=priv_q).json()
            t_priv = client.post(f"/api/v1/tenants/{a.tenant}/knowledge/retrieve", headers=_hdr(a.teacher_token), json=priv_q).json()
            assert any(c["source_id"] == sub1["source"]["source_id"] for c in s1_priv["chunks"])
            assert not any(c["source_id"] == sub1["source"]["source_id"] for c in s2_priv["chunks"])
            assert not any(c["source_id"] == sub1["source"]["source_id"] for c in t_priv["chunks"])

            # Share with teacher -> teacher sees it until policy disables teachers_can_view_student_submissions
            client.patch(
                f"/api/v1/tenants/{a.tenant}/sources/{sub1['source']['source_id']}",
                headers=_hdr(a.student1_token),
                json={"visibility": "teacher_review"},
            )
            t_shared = client.post(f"/api/v1/tenants/{a.tenant}/knowledge/retrieve", headers=_hdr(a.teacher_token), json=priv_q).json()
            assert any(c["source_id"] == sub1["source"]["source_id"] for c in t_shared["chunks"])

            client.patch(
                f"/api/v1/tenants/{a.tenant}/offerings/{a.offering}/source-policy",
                headers=_hdr(a.teacher_token),
                json={"teachers_can_view_student_submissions": False},
            )
            t_policy_off = client.post(f"/api/v1/tenants/{a.tenant}/knowledge/retrieve", headers=_hdr(a.teacher_token), json=priv_q).json()
            assert not any(c["source_id"] == sub1["source"]["source_id"] for c in t_policy_off["chunks"])

            # 6. Unpublish immediately excludes stale teacher-resource embeddings for students
            client.post(
                f"/api/v1/tenants/{a.tenant}/sources/{sid}/versions/{vid}/concepts/publish",
                headers=_hdr(a.teacher_token),
                json={"concept_extraction_id": ce_id, "publish": False},
            )
            s_after_unpub = client.post(f"/api/v1/tenants/{a.tenant}/knowledge/retrieve", headers=_hdr(a.student1_token), json=q_body).json()
            assert not any(c["source_id"] == sid for c in s_after_unpub["chunks"])
            s_after_unpub_no_sub = client.post(
                f"/api/v1/tenants/{a.tenant}/knowledge/retrieve",
                headers=_hdr(a.student1_token),
                json={**q_body, "include_student_submissions": False},
            ).json()
            assert s_after_unpub_no_sub["chunks"] == []

            # Re-publish, then withdraw Student 1 enrolment -> denied; revoke teacher -> denied
            client.post(
                f"/api/v1/tenants/{a.tenant}/sources/{sid}/versions/{vid}/concepts/publish",
                headers=_hdr(a.teacher_token),
                json={"concept_extraction_id": ce_id, "publish": True},
            )
            with pg_env.admin.begin() as c:
                c.execute(text(f"SET LOCAL search_path TO {pg_env.schema}, public"))
                c.execute(
                    text("UPDATE student_enrolments SET active=false WHERE tenant_id=:t AND user_id=:u AND offering_id=:o"),
                    {"t": a.tenant, "u": a.student1, "o": a.offering},
                )
            assert (
                client.post(f"/api/v1/tenants/{a.tenant}/knowledge/retrieve", headers=_hdr(a.student1_token), json=q_body).status_code
                == 403
            )
            # Even tenant-wide retrieve without offering_id returns 0 chunks for withdrawn student
            s1_wide = client.post(
                f"/api/v1/tenants/{a.tenant}/knowledge/retrieve",
                headers=_hdr(a.student1_token),
                json={"query": "velocity displacement", "mode": "hybrid_graph"},
            )
            assert s1_wide.status_code == 200
            assert s1_wide.json()["chunks"] == []

            # Revoking teacher assignment excludes teacher immediately
            with pg_env.admin.begin() as c:
                c.execute(text(f"SET LOCAL search_path TO {pg_env.schema}, public"))
                c.execute(
                    text("UPDATE teacher_assignments SET active=false WHERE tenant_id=:t AND user_id=:u AND offering_id=:o"),
                    {"t": a.tenant, "u": a.teacher, "o": a.offering},
                )
            assert (
                client.post(f"/api/v1/tenants/{a.tenant}/knowledge/retrieve", headers=_hdr(a.teacher_token), json=q_body).status_code
                == 403
            )
            t_wide = client.post(
                f"/api/v1/tenants/{a.tenant}/knowledge/retrieve",
                headers=_hdr(a.teacher_token),
                json={"query": "velocity displacement", "mode": "hybrid_graph"},
            )
            assert t_wide.status_code == 200
            assert t_wide.json()["chunks"] == []

            # Still works for enrolled Student 2 (verifies published teacher resource is alive for active student)
            s2_ok = client.post(f"/api/v1/tenants/{a.tenant}/knowledge/retrieve", headers=_hdr(a.student2_token), json=q_body)
            assert s2_ok.status_code == 200
            assert any(c["source_id"] == sid for c in s2_ok.json()["chunks"])

            # Inspect PostgreSQL RLS on knowledge_index_chunks directly as non-owner app role
            with pg_env.app_db.sessions.begin() as s:
                bind_tenant(s, b.tenant)
                count_b = s.execute(text("SELECT count(*) FROM knowledge_index_chunks")).scalar()
                assert count_b == 0
            with pg_env.app_db.sessions.begin() as s:
                bind_tenant(s, a.tenant)
                count_a = s.execute(text("SELECT count(*) FROM knowledge_index_chunks")).scalar()
                assert count_a >= 1
                _ = KnowledgeRetrieveRequest(query="check")
                _ = UUID(sid)
    finally:
        runner.close()


def test_phase3_retrieval_filters_rejected_concepts_from_student_graph(pg_env: PgEnv) -> None:
    class _SplitKinematicsGateway(FakeAIGateway):
        def complete_structured(
            self,
            context: TenantContext,
            messages: list[ChatMessage],
            response_model,
            config: AIRequestConfig | None = None,
        ):
            user_txt = messages[-1].content
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
            concepts_out: list[RawConceptCandidate] = []
            if "Displacement" in user_txt:
                concepts_out.append(
                    RawConceptCandidate(
                        name="Displacement",
                        canonical_summary="Displacement is the signed change in position.",
                        grounding="source_explicit",
                        evidence_ids=[evs[0]] if evs else ["ev-1"],
                    )
                )
            if "Velocity" in user_txt:
                concepts_out.append(
                    RawConceptCandidate(
                        name="Velocity",
                        canonical_summary="Velocity is the rate of change of displacement.",
                        grounding="source_explicit",
                        evidence_ids=[evs[-1]] if evs else ["ev-1"],
                    )
                )
            self.structured_payloads[response_model] = RawConceptExtractionBatch(
                section_summary="Kinematics split chunks",
                concepts=concepts_out,
            )
            return super().complete_structured(context, messages, response_model, config)

    app = create_app(settings=pg_env.settings, database=pg_env.app_db)
    embedder = DeterministicFakeEmbedder()
    runner: WorkflowWorkerRunner = build_worker_runner(
        pg_env.settings, db=pg_env.app_db, ai_gateway=_SplitKinematicsGateway(), embedder=embedder
    )
    a = _seed_tenant(pg_env.admin, pg_env.schema, pg_env.app_db, pg_env.settings, "filter")

    try:
        with TestClient(app) as client:
            pad = " ".join(f"kinematics spacer term {i}" for i in range(420))
            src_res = client.post(
                f"/api/v1/tenants/{a.tenant}/offerings/{a.offering}/sources",
                headers=_hdr(a.teacher_token),
                json={
                    "title": "Filtered Graph Notes",
                    "input_format": "typed_notes",
                    "content": (
                        "# Kinematics Overview\n\n"
                        "Displacement is the signed change in position.\n\n"
                        f"{pad}\n\n"
                        "# Motion Overview\n\n"
                        "Velocity is the rate of change of displacement."
                    ),
                    "visibility": "offering_published",
                },
            )
            assert src_res.status_code == 201
            sid = src_res.json()["source"]["source_id"]
            vid = src_res.json()["version"]["version_id"]

            assert (
                client.post(
                    f"/api/v1/tenants/{a.tenant}/sources/{sid}/versions/{vid}/concepts/extract",
                    headers=_hdr(a.teacher_token),
                ).status_code
                == 202
            )
            assert runner.drain()["succeeded"] == 1
            ext = client.get(
                f"/api/v1/tenants/{a.tenant}/sources/{sid}/versions/{vid}/concepts",
                headers=_hdr(a.teacher_token),
            ).json()
            ce_id = ext["concept_extraction_id"]
            c_disp = next(c["concept_id"] for c in ext["knowledge"]["concepts"] if c["name"] == "Displacement")
            c_vel = next(c["concept_id"] for c in ext["knowledge"]["concepts"] if c["name"] == "Velocity")

            assert (
                client.post(
                    f"/api/v1/tenants/{a.tenant}/sources/{sid}/versions/{vid}/concepts/review",
                    headers=_hdr(a.teacher_token),
                    json={
                        "concept_extraction_id": ce_id,
                        "decision": "approved",
                        "approved_concept_ids": [c_disp],
                        "rejected_concept_ids": [c_vel],
                    },
                ).status_code
                == 200
            )
            assert (
                client.post(
                    f"/api/v1/tenants/{a.tenant}/sources/{sid}/versions/{vid}/concepts/publish",
                    headers=_hdr(a.teacher_token),
                    json={"concept_extraction_id": ce_id, "publish": True},
                ).status_code
                == 200
            )
            assert (
                client.post(
                    f"/api/v1/tenants/{a.tenant}/sources/{sid}/versions/{vid}/index",
                    headers=_hdr(a.teacher_token),
                ).status_code
                == 202
            )
            assert runner.drain()["succeeded"] == 1

            q_body = {
                "query": "displacement velocity rate",
                "offering_id": str(a.offering),
                "mode": "hybrid_graph",
            }
            s_ret = client.post(
                f"/api/v1/tenants/{a.tenant}/knowledge/retrieve",
                headers=_hdr(a.student1_token),
                json=q_body,
            )
            assert s_ret.status_code == 200
            student_names = {gc["name"] for gc in s_ret.json()["graph_concepts"]}
            assert "Displacement" in student_names
            assert "Velocity" not in student_names
            assert "Velocity" not in s_ret.json()["context_text"].split("[Concept Graph Context]")[-1]

            t_pub = client.post(
                f"/api/v1/tenants/{a.tenant}/knowledge/retrieve",
                headers=_hdr(a.teacher_token),
                json={**q_body, "published_only": True},
            )
            assert t_pub.status_code == 200
            assert "Velocity" not in {gc["name"] for gc in t_pub.json()["graph_concepts"]}

            t_draft = client.post(
                f"/api/v1/tenants/{a.tenant}/knowledge/retrieve",
                headers=_hdr(a.teacher_token),
                json={**q_body, "published_only": False},
            )
            assert t_draft.status_code == 200
            assert {"Displacement", "Velocity"} <= {gc["name"] for gc in t_draft.json()["graph_concepts"]}
    finally:
        runner.close()


def test_phase3_retrieval_authorization_boundaries_and_mixed_roles(pg_env: PgEnv) -> None:
    app = create_app(settings=pg_env.settings, database=pg_env.app_db)
    embedder = DeterministicFakeEmbedder()
    gw = _ScriptedKinematicsGateway()
    runner: WorkflowWorkerRunner = build_worker_runner(
        pg_env.settings, db=pg_env.app_db, ai_gateway=gw, embedder=embedder
    )
    a = _seed_tenant(pg_env.admin, pg_env.schema, pg_env.app_db, pg_env.settings, "authz")
    auth = AuthService(pg_env.app_db.sessions, pg_env.settings.auth)  # type: ignore[arg-type]

    # Seed: owner-only, admin-only, guardian-only, second offering, and mixed-role user (teacher in O1, student in O2)
    off2 = uuid4()
    adm, guard, mixed = uuid4(), uuid4(), uuid4()
    pw_hash = auth.passwords.hash(PASSWORD)
    emails = {
        adm: f"adm-{adm}@uni.edu",
        guard: f"guard-{guard}@uni.edu",
        mixed: f"mixed-{mixed}@uni.edu",
        "owner": f"own-{uuid4()}@uni.edu",
    }
    with pg_env.admin.begin() as c:
        c.execute(text(f"SET LOCAL search_path TO {pg_env.schema}, public"))
        owner_id = c.execute(
            text("SELECT user_id FROM role_grants WHERE tenant_id = :t AND role = 'institution_owner' LIMIT 1"),
            {"t": a.tenant},
        ).scalar_one()
        c.execute(
            text("UPDATE verified_identity_attributes SET normalized_value = :e WHERE user_id = :u AND kind = 'email'"),
            {"e": emails["owner"], "u": owner_id},
        )
        for uid, roles in ((adm, ("institution_admin",)), (guard, ("guardian",)), (mixed, ("institution_admin", "subject_teacher", "student"))):
            c.execute(text("INSERT INTO users VALUES (:u, :n, 'active')"), {"u": uid, "n": str(uid)})
            c.execute(text("INSERT INTO memberships VALUES (:t, :u, 'active')"), {"t": a.tenant, "u": uid})
            for r in roles:
                c.execute(text("INSERT INTO role_grants (tenant_id, user_id, role) VALUES (:t, :u, :r)"), {"t": a.tenant, "u": uid, "r": r})
            c.execute(text("INSERT INTO verified_identity_attributes VALUES (:u, 'email', :e, now())"), {"u": uid, "e": emails[uid]})
            c.execute(text("INSERT INTO password_credentials VALUES (:u, :h)"), {"u": uid, "h": pw_hash})

        sec_id = c.execute(text("SELECT section_id FROM subject_offerings WHERE tenant_id=:t AND id=:o"), {"t": a.tenant, "o": a.offering}).scalar_one()
        sub2 = uuid4()
        c.execute(text("INSERT INTO subjects (tenant_id, id, name) VALUES (:t, :id, 'Chemistry')"), {"t": a.tenant, "id": sub2})
        c.execute(text("INSERT INTO subject_offerings (tenant_id, id, name, section_id, subject_id) VALUES (:t, :id, 'Chem 101', :sec, :sub)"), {"t": a.tenant, "id": off2, "sec": sec_id, "sub": sub2})
        # Mixed user teaches Offering 1 (a.offering) and is ONLY a student in Offering 2 (off2); a.teacher teaches off2
        c.execute(text("INSERT INTO teacher_assignments VALUES (:t, :u, :o, true)"), {"t": a.tenant, "o": a.offering, "u": mixed})
        c.execute(text("INSERT INTO teacher_assignments VALUES (:t, :u, :o, true)"), {"t": a.tenant, "o": off2, "u": a.teacher})
        c.execute(text("INSERT INTO student_enrolments VALUES (:t, :u, :o, true)"), {"t": a.tenant, "o": off2, "u": mixed})

    own_tok = auth.login(IdentityAttributeKind.email, emails["owner"], PASSWORD).access_token.get_secret_value()
    adm_tok = auth.login(IdentityAttributeKind.email, emails[adm], PASSWORD).access_token.get_secret_value()
    guard_tok = auth.login(IdentityAttributeKind.email, emails[guard], PASSWORD).access_token.get_secret_value()
    mixed_tok = auth.login(IdentityAttributeKind.email, emails[mixed], PASSWORD).access_token.get_secret_value()

    try:
        with TestClient(app) as client:
            # 1. Institution-wide & offering-scoped gates both deny owner/admin/guardian with 403
            for tok in (own_tok, adm_tok, guard_tok):
                assert client.post(f"/api/v1/tenants/{a.tenant}/knowledge/retrieve", headers=_hdr(tok), json={"query": "displacement"}).status_code == 403
                assert client.post(f"/api/v1/tenants/{a.tenant}/knowledge/retrieve", headers=_hdr(tok), json={"query": "displacement", "offering_id": str(a.offering)}).status_code == 403

            # 2. Seed unpublished draft in Offering 1 (for mixed teacher) & Offering 2 (unpublished vs published)
            s1 = client.post(
                f"/api/v1/tenants/{a.tenant}/offerings/{a.offering}/sources",
                headers=_hdr(a.teacher_token),
                json={"title": "O1 Draft", "input_format": "typed_notes", "content": "OfferingOneDraftToken alpha kinetics.", "visibility": "teacher_only"},
            ).json()
            s2 = client.post(
                f"/api/v1/tenants/{a.tenant}/offerings/{off2}/sources",
                headers=_hdr(a.teacher_token),
                json={"title": "O2 Secret Draft", "input_format": "typed_notes", "content": "OfferingTwoSecretToken stoichiometry.", "visibility": "teacher_only"},
            ).json()
            for src_obj in (s1, s2):
                client.post(f"/api/v1/tenants/{a.tenant}/sources/{src_obj['source']['source_id']}/versions/{src_obj['version']['version_id']}/index", headers=_hdr(a.teacher_token))
            assert runner.drain()["succeeded"] == 2

            # Mixed user sees O1 draft, does NOT see O2 unpublished draft (neither tenant-wide nor offering-scoped)
            m_o1 = client.post(f"/api/v1/tenants/{a.tenant}/knowledge/retrieve", headers=_hdr(mixed_tok), json={"query": "OfferingOneDraftToken", "offering_id": str(a.offering), "mode": "hybrid"}).json()
            assert len(m_o1["chunks"]) == 1
            m_o2 = client.post(f"/api/v1/tenants/{a.tenant}/knowledge/retrieve", headers=_hdr(mixed_tok), json={"query": "OfferingTwoSecretToken", "offering_id": str(off2), "mode": "hybrid"}).json()
            assert m_o2["chunks"] == []
            m_wide = client.post(f"/api/v1/tenants/{a.tenant}/knowledge/retrieve", headers=_hdr(mixed_tok), json={"query": "OfferingTwoSecretToken", "mode": "hybrid"}).json()
            assert not any(c["source_id"] == s2["source"]["source_id"] for c in m_wide["chunks"])

            # 3. Version advancement excludes old V1 chunks once V2 becomes latest_version_id
            v2 = client.post(
                f"/api/v1/tenants/{a.tenant}/sources/{s1['source']['source_id']}/versions",
                headers=_hdr(a.teacher_token),
                json={"input_format": "typed_notes", "content": "VersionTwoReplacementToken updated kinetics."},
            ).json()
            client.post(f"/api/v1/tenants/{a.tenant}/sources/{s1['source']['source_id']}/versions/{v2['version']['version_id']}/index", headers=_hdr(a.teacher_token))
            assert runner.drain()["succeeded"] == 1
            m_v1 = client.post(f"/api/v1/tenants/{a.tenant}/knowledge/retrieve", headers=_hdr(mixed_tok), json={"query": "OfferingOneDraftToken", "offering_id": str(a.offering), "mode": "hybrid"}).json()
            assert all(c["version_id"] != s1["version"]["version_id"] for c in m_v1["chunks"])
            assert client.post(f"/api/v1/tenants/{a.tenant}/knowledge/retrieve", headers=_hdr(mixed_tok), json={"query": "OfferingOneDraftToken", "offering_id": str(a.offering), "mode": "lexical"}).json()["chunks"] == []
            m_v2 = client.post(f"/api/v1/tenants/{a.tenant}/knowledge/retrieve", headers=_hdr(mixed_tok), json={"query": "VersionTwoReplacementToken", "offering_id": str(a.offering), "mode": "hybrid"}).json()
            assert any(c["version_id"] == v2["version"]["version_id"] for c in m_v2["chunks"])

            # 4. Revoking subject_teacher role blocks mixed user from O1 (403) while retaining student role on O2
            with pg_env.admin.begin() as c:
                c.execute(text(f"SET LOCAL search_path TO {pg_env.schema}, public"))
                c.execute(text("UPDATE role_grants SET revoked_at = now() WHERE tenant_id=:t AND user_id=:u AND role IN ('subject_teacher', 'institution_admin')"), {"t": a.tenant, "u": mixed})
            assert client.post(f"/api/v1/tenants/{a.tenant}/knowledge/retrieve", headers=_hdr(mixed_tok), json={"query": "VersionTwoReplacementToken", "offering_id": str(a.offering)}).status_code == 403

            # Revoking all remaining roles denies institution-wide retrieve with 403
            with pg_env.admin.begin() as c:
                c.execute(text(f"SET LOCAL search_path TO {pg_env.schema}, public"))
                c.execute(text("UPDATE role_grants SET revoked_at = now() WHERE tenant_id=:t AND user_id=:u AND role = 'student'"), {"t": a.tenant, "u": mixed})
            assert client.post(f"/api/v1/tenants/{a.tenant}/knowledge/retrieve", headers=_hdr(mixed_tok), json={"query": "VersionTwoReplacementToken"}).status_code == 403
    finally:
        runner.close()


def test_phase3_rejected_raw_chunks_and_mismatched_reviews_fail_closed(pg_env: PgEnv) -> None:
    class _TwoChunkGateway(FakeAIGateway):
        def complete_structured(
            self,
            context: TenantContext,
            messages: list[ChatMessage],
            response_model,
            config: AIRequestConfig | None = None,
        ):
            user_txt = messages[-1].content
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
            concepts_out: list[RawConceptCandidate] = []
            if "ApprovedPartitionToken" in user_txt:
                concepts_out.append(
                    RawConceptCandidate(
                        name="ApprovedPartition",
                        canonical_summary="ApprovedPartitionToken divides keys around a pivot cleanly.",
                        grounding="source_explicit",
                        evidence_ids=[evs[0]] if evs else ["ev-1"],
                    )
                )
            if "ForbiddenRejectedToken" in user_txt:
                concepts_out.append(
                    RawConceptCandidate(
                        name="RejectedSpeculation",
                        canonical_summary="ForbiddenRejectedToken inaccurate draft heuristic must stay hidden.",
                        grounding="source_explicit",
                        evidence_ids=[evs[-1]] if evs else ["ev-1"],
                    )
                )
            batch = RawConceptExtractionBatch(
                section_summary="Partition and rejected draft",
                concepts=concepts_out,
            )
            self.structured_payloads[response_model] = batch
            return super().complete_structured(context, messages, response_model, config)

    app = create_app(settings=pg_env.settings, database=pg_env.app_db)
    embedder = DeterministicFakeEmbedder()
    runner: WorkflowWorkerRunner = build_worker_runner(
        pg_env.settings, db=pg_env.app_db, ai_gateway=_TwoChunkGateway(), embedder=embedder
    )
    a = _seed_tenant(pg_env.admin, pg_env.schema, pg_env.app_db, pg_env.settings, "leak")

    try:
        with TestClient(app) as client:
            # Build one single-section document with distinct paragraphs that splits across >=2 sequential chunks
            mid_para_1 = " ".join(f"partitioning middle alpha {i}" for i in range(180))
            mid_para_2 = " ".join(f"partitioning middle beta {i}" for i in range(180))
            content = (
                "# Sorting and Leaks\n\n"
                "ApprovedPartitionToken divides keys around a pivot cleanly.\n\n"
                f"{mid_para_1}\n\n"
                f"{mid_para_2}\n\n"
                "ForbiddenRejectedToken inaccurate draft heuristic must stay hidden.\n"
            )
            src = client.post(
                f"/api/v1/tenants/{a.tenant}/offerings/{a.offering}/sources",
                headers=_hdr(a.teacher_token),
                json={"title": "Chunk Leak Check", "input_format": "typed_notes", "content": content, "visibility": "offering_published"},
            ).json()
            sid, vid = src["source"]["source_id"], src["version"]["version_id"]

            # Also register an unreviewed offering_published source: readable as source material, NOT as approved lesson!
            raw_src = client.post(
                f"/api/v1/tenants/{a.tenant}/offerings/{a.offering}/sources",
                headers=_hdr(a.teacher_token),
                json={"title": "Raw Unreviewed Source", "input_format": "typed_notes", "content": "UnreviewedSourceMaterialToken public notes.", "visibility": "offering_published"},
            ).json()
            client.post(f"/api/v1/tenants/{a.tenant}/sources/{raw_src['source']['source_id']}/versions/{raw_src['version']['version_id']}/index", headers=_hdr(a.teacher_token))

            client.post(f"/api/v1/tenants/{a.tenant}/sources/{sid}/versions/{vid}/concepts/extract", headers=_hdr(a.teacher_token))
            assert runner.drain()["succeeded"] == 2  # 1 index job + 1 concept extract job

            client.post(f"/api/v1/tenants/{a.tenant}/sources/{sid}/versions/{vid}/index", headers=_hdr(a.teacher_token))
            assert runner.drain()["succeeded"] == 1
            assert client.get(f"/api/v1/tenants/{a.tenant}/sources/{sid}/versions/{vid}/index", headers=_hdr(a.teacher_token)).json()["chunk_count"] >= 2

            ext = client.get(f"/api/v1/tenants/{a.tenant}/sources/{sid}/versions/{vid}/concepts", headers=_hdr(a.teacher_token)).json()
            ce_id = ext["concept_extraction_id"]
            c_ok = next(c["concept_id"] for c in ext["knowledge"]["concepts"] if c["name"] == "ApprovedPartition")
            c_bad = next(c["concept_id"] for c in ext["knowledge"]["concepts"] if c["name"] == "RejectedSpeculation")

            assert client.post(
                f"/api/v1/tenants/{a.tenant}/sources/{sid}/versions/{vid}/concepts/review",
                headers=_hdr(a.teacher_token),
                json={"concept_extraction_id": ce_id, "decision": "approved", "approved_concept_ids": [c_ok], "rejected_concept_ids": [c_bad]},
            ).status_code == 200
            assert client.post(
                f"/api/v1/tenants/{a.tenant}/sources/{sid}/versions/{vid}/concepts/publish",
                headers=_hdr(a.teacher_token),
                json={"concept_extraction_id": ce_id, "publish": True},
            ).status_code == 200

            # 1. Student CANNOT see rejected chunk directly or via sibling parent_context across any retrieval mode
            for mode in ("lexical", "semantic", "hybrid", "hybrid_graph"):
                s_bad = client.post(
                    f"/api/v1/tenants/{a.tenant}/knowledge/retrieve",
                    headers=_hdr(a.student1_token),
                    json={"query": "ForbiddenRejectedToken inaccurate draft", "offering_id": str(a.offering), "mode": mode},
                ).json()
                assert not any("ForbiddenRejectedToken" in ch["rendered_text"] for ch in s_bad["chunks"])
                assert "ForbiddenRejectedToken" not in s_bad["context_text"]
                assert "RejectedSpeculation" not in {g["name"] for g in s_bad["graph_concepts"]}

            s_ok = client.post(
                f"/api/v1/tenants/{a.tenant}/knowledge/retrieve",
                headers=_hdr(a.student1_token),
                json={"query": "ApprovedPartitionToken pivot", "offering_id": str(a.offering), "mode": "hybrid_graph"},
            ).json()
            assert len(s_ok["chunks"]) >= 1
            assert all("ForbiddenRejectedToken" not in (ch["parent_context"] or "") for ch in s_ok["chunks"])
            assert "ForbiddenRejectedToken" not in s_ok["context_text"]
            assert "ApprovedPartition" in {g["name"] for g in s_ok["graph_concepts"]}

            # 2. Independently authorized raw source is readable via Sources API, NOT via student lesson retrieval
            assert client.get(f"/api/v1/tenants/{a.tenant}/sources/{raw_src['source']['source_id']}", headers=_hdr(a.student1_token)).status_code == 200
            s_raw = client.post(
                f"/api/v1/tenants/{a.tenant}/knowledge/retrieve",
                headers=_hdr(a.student1_token),
                json={"query": "UnreviewedSourceMaterialToken", "offering_id": str(a.offering), "mode": "hybrid"},
            ).json()
            assert not any(c["source_id"] == raw_src["source"]["source_id"] for c in s_raw["chunks"])
            assert client.post(
                f"/api/v1/tenants/{a.tenant}/knowledge/retrieve",
                headers=_hdr(a.student1_token),
                json={"query": "UnreviewedSourceMaterialToken", "offering_id": str(a.offering), "mode": "lexical"},
            ).json()["chunks"] == []

            # 3. Corrupted / mismatched review fails closed for students
            with pg_env.admin.begin() as c:
                c.execute(text(f"SET LOCAL search_path TO {pg_env.schema}, public"))
                c.execute(text("UPDATE knowledge_reviews SET decision = 'rejected' WHERE tenant_id=:t AND version_id=:v"), {"t": a.tenant, "v": vid})
            s_mismatched_rev = client.post(
                f"/api/v1/tenants/{a.tenant}/knowledge/retrieve",
                headers=_hdr(a.student1_token),
                json={"query": "ApprovedPartitionToken", "offering_id": str(a.offering), "mode": "hybrid_graph"},
            ).json()
            assert s_mismatched_rev["chunks"] == []
            assert s_mismatched_rev["graph_concepts"] == []
    finally:
        runner.close()


def test_phase3_shared_chunk_conservative_exclusion_and_lexical_fallback(pg_env: PgEnv) -> None:
    class _SharedChunkGateway(FakeAIGateway):
        def complete_structured(
            self,
            context: TenantContext,
            messages: list[ChatMessage],
            response_model,
            config: AIRequestConfig | None = None,
        ):
            user_txt = messages[-1].content
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
            concepts_out: list[RawConceptCandidate] = []
            if "CleanAlphaToken" in user_txt:
                concepts_out.append(
                    RawConceptCandidate(
                        name="CleanApproved",
                        canonical_summary="CleanAlphaToken verified physics core.",
                        grounding="source_explicit",
                        evidence_ids=[evs[0]] if evs else ["ev-1"],
                    )
                )
            if "SharedOverlapBetaToken" in user_txt:
                shared_ev = [evs[-1]] if evs else ["ev-1"]
                concepts_out.extend([
                    RawConceptCandidate(
                        name="ApprovedOnShared",
                        canonical_summary="SharedOverlapBetaToken accepted concept on shared chunk.",
                        grounding="source_explicit",
                        evidence_ids=shared_ev,
                    ),
                    RawConceptCandidate(
                        name="RejectedOnShared",
                        canonical_summary="SharedOverlapBetaToken rejected concept on shared chunk.",
                        grounding="source_explicit",
                        evidence_ids=shared_ev,
                    ),
                ])
            self.structured_payloads[response_model] = RawConceptExtractionBatch(
                section_summary="Shared chunk conservative exclusion",
                concepts=concepts_out,
            )
            return super().complete_structured(context, messages, response_model, config)

    # Verify default hybrid -> lexical fallback when local ONNX model artifacts are missing (no fake fallback!)
    no_emb_settings = pg_env.settings.model_copy(
        update={
            "environment": "development",
            "embedding_provider": "local_onnx",
            "local_embedding_model_dir": str(ROOT / "nonexistent-onnx-model-dir"),
        }
    )
    app = create_app(settings=no_emb_settings, database=pg_env.app_db)
    runner = build_worker_runner(no_emb_settings, db=pg_env.app_db, ai_gateway=_SharedChunkGateway())
    a = _seed_tenant(pg_env.admin, pg_env.schema, pg_env.app_db, pg_env.settings, "shared")

    try:
        with TestClient(app) as client:
            pad_1 = " ".join(f"shared section spacer alpha {i}" for i in range(210))
            pad_2 = " ".join(f"shared section spacer beta {i}" for i in range(210))
            content = (
                "# Shared Kinematics Section\n\n"
                "CleanAlphaToken verified physics core.\n\n"
                f"{pad_1}\n\n"
                f"{pad_2}\n\n"
                "SharedOverlapBetaToken accepted concept on shared chunk and rejected concept on shared chunk."
            )
            src = client.post(
                f"/api/v1/tenants/{a.tenant}/offerings/{a.offering}/sources",
                headers=_hdr(a.teacher_token),
                json={"title": "Shared Evidence Source", "input_format": "typed_notes", "content": content, "visibility": "offering_published"},
            ).json()
            sid, vid = src["source"]["source_id"], src["version"]["version_id"]

            client.post(f"/api/v1/tenants/{a.tenant}/sources/{sid}/versions/{vid}/concepts/extract", headers=_hdr(a.teacher_token))
            assert runner.drain()["succeeded"] == 1
            client.post(f"/api/v1/tenants/{a.tenant}/sources/{sid}/versions/{vid}/index", headers=_hdr(a.teacher_token))
            assert runner.drain()["succeeded"] == 1

            ext = client.get(f"/api/v1/tenants/{a.tenant}/sources/{sid}/versions/{vid}/concepts", headers=_hdr(a.teacher_token)).json()
            ce_id = ext["concept_extraction_id"]
            approved_ids = [c["concept_id"] for c in ext["knowledge"]["concepts"] if c["name"] in {"CleanApproved", "ApprovedOnShared"}]
            rejected_ids = [c["concept_id"] for c in ext["knowledge"]["concepts"] if c["name"] == "RejectedOnShared"]

            assert client.post(
                f"/api/v1/tenants/{a.tenant}/sources/{sid}/versions/{vid}/concepts/review",
                headers=_hdr(a.teacher_token),
                json={"concept_extraction_id": ce_id, "decision": "approved", "approved_concept_ids": approved_ids, "rejected_concept_ids": rejected_ids},
            ).status_code == 200
            assert client.post(
                f"/api/v1/tenants/{a.tenant}/sources/{sid}/versions/{vid}/concepts/publish",
                headers=_hdr(a.teacher_token),
                json={"concept_extraction_id": ce_id, "publish": True},
            ).status_code == 200

            # 1. Embeddings unavailable -> default hybrid request falls back to lexical cleanly; explicit semantic returns 503
            sem_503 = client.post(
                f"/api/v1/tenants/{a.tenant}/knowledge/retrieve",
                headers=_hdr(a.student1_token),
                json={"query": "CleanAlphaToken", "offering_id": str(a.offering), "mode": "semantic"},
            )
            assert sem_503.status_code == 503

            s_clean = client.post(
                f"/api/v1/tenants/{a.tenant}/knowledge/retrieve",
                headers=_hdr(a.student1_token),
                json={"query": "CleanAlphaToken", "offering_id": str(a.offering)},
            )
            assert s_clean.status_code == 200
            clean_body = s_clean.json()
            assert clean_body["mode"] == "lexical"
            assert clean_body["embedding_model"] == "none"
            assert len(clean_body["chunks"]) >= 1
            assert all("SharedOverlapBetaToken" not in (c["parent_context"] or "") for c in clean_body["chunks"])
            assert "SharedOverlapBetaToken" not in clean_body["context_text"]

            # 2. Shared chunk overlapping teacher-rejected concept is conservatively excluded even though ApprovedOnShared shares its evidence
            s_shared = client.post(
                f"/api/v1/tenants/{a.tenant}/knowledge/retrieve",
                headers=_hdr(a.student1_token),
                json={"query": "SharedOverlapBetaToken", "offering_id": str(a.offering), "mode": "hybrid_graph"},
            ).json()
            assert not any("SharedOverlapBetaToken" in c["rendered_text"] for c in s_shared["chunks"])
            assert "SharedOverlapBetaToken" not in s_shared["context_text"]
            assert "RejectedOnShared" not in {g["name"] for g in s_shared["graph_concepts"]}

            # 3. Independently authorized source access remains intact; teacher unpublished draft retrieval remains intact
            assert client.get(f"/api/v1/tenants/{a.tenant}/sources/{sid}", headers=_hdr(a.student1_token)).status_code == 200
            assert client.get(f"/api/v1/tenants/{a.tenant}/sources/{sid}/versions/{vid}/extraction", headers=_hdr(a.student1_token)).status_code == 200
            t_draft = client.post(
                f"/api/v1/tenants/{a.tenant}/knowledge/retrieve",
                headers=_hdr(a.teacher_token),
                json={"query": "SharedOverlapBetaToken", "offering_id": str(a.offering), "published_only": False},
            ).json()
            assert any("SharedOverlapBetaToken" in c["rendered_text"] for c in t_draft["chunks"])
    finally:
        runner.close()
