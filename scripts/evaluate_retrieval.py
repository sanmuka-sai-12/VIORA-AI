"""Evaluate lexical, semantic, hybrid, and graph-assisted retrieval quality and PostgreSQL plans.

Run with fake embeddings (zero downloads):
  python scripts/evaluate_retrieval.py

Run with local BAAI/bge-small-en-v1.5 INT8 ONNX model (explicit --download-model opt-in):
  python scripts/evaluate_retrieval.py --onnx-dir storage/models/bge-small-en-v1.5-int8 --download-model
"""

from __future__ import annotations

import argparse
import json
import math
import os
import secrets
import statistics
import sys
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

from alembic import command
from alembic.config import Config
from psycopg import sql
from pydantic import SecretStr
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.schema import CreateSchema, DropSchema

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from viora.authorization.public import bind_tenant
from viora.config import Settings
from viora.contracts import ArtifactVersionRef, TenantContext
from viora.database_roles import grant_application_privileges
from viora.db import Database
from viora.knowledge.chunking import chunk_extracted_document
from viora.knowledge.embeddings import (
    DeterministicFakeEmbedder,
    LocalOnnxEmbedder,
    TextEmbedder,
)
from viora.knowledge.indexing import (
    SqlKnowledgeIndexRepository,
    compute_chunk_content_sha256,
    compute_embedding_fingerprint,
)
from viora.knowledge.retrieval import HybridKnowledgeRetriever
from viora.knowledge.schemas import (
    ConceptCandidate,
    ConceptDefinition,
    ConceptEvidenceBinding,
    ConceptRelationshipCandidate,
    ExtractedKnowledgeGraph,
    KnowledgeRetrieveRequest,
    KnowledgeValidationReport,
    PrerequisiteCandidate,
    RetrievalMode,
)
from viora.sources.public import AdaptiveDocumentExtractor, clamp_source_limits

CORPUS_DIR = ROOT / "benchmarks" / "educational_corpus"
BGE_FILES = {
    "model_quantized.onnx": "https://huggingface.co/Xenova/bge-small-en-v1.5/resolve/main/onnx/model_quantized.onnx",
    "vocab.txt": "https://huggingface.co/Xenova/bge-small-en-v1.5/resolve/main/vocab.txt",
}

SYNTHETIC_DOCS = [
    {
        "key": "kinematics",
        "title": "Classical Kinematics and Motion",
        "text": (CORPUS_DIR / "samples" / "01_markdown_notes.md").read_text(encoding="utf-8"),
        "published": True,
        "concepts": [
            ("c-disp", "Displacement", "Signed change in position vector.", "Delta x = x_f - x_0", ()),
            ("c-vel", "Velocity", "Vector rate of change of displacement over time.", "v = dx/dt; SI unit m/s.", ("Displacement",)),
            ("c-acc", "Acceleration", "Rate of change of velocity under constant or varying force.", "a = dv/dt", ("Velocity",)),
        ],
        "rels": [("c-disp", "prerequisite_of", "c-vel"), ("c-vel", "prerequisite_of", "c-acc")],
    },
    {
        "key": "binary_search",
        "title": "Binary Search and Divide-and-Conquer",
        "text": (
            "# Binary Search Algorithm\n\n"
            "Binary search locates a target element in a sorted array by repeatedly halving the search interval.\n\n"
            "## Complexity and Loop Invariants\n\n"
            "Each comparison eliminates half of the remaining candidates, giving O(log n) worst-case time complexity and O(1) auxiliary space."
        ),
        "published": True,
        "concepts": [
            ("c-sorted", "Sorted Array Precondition", "Input sequence must be monotonically ordered before halving.", None, ()),
            ("c-bs", "Binary Search", "Interval-halving lookup achieving logarithmic runtime.", "Checks midpoint and narrows [lo, hi].", ("Sorted Array Precondition",)),
        ],
        "rels": [("c-sorted", "prerequisite_of", "c-bs")],
    },
    {
        "key": "thermodynamics",
        "title": "Ideal Gas Heat Capacity and Adiabatic Ratio",
        "text": (
            "# Ideal Gas Specific Heat\n\n"
            "Monatomic gases such as Helium have molar heat capacity at constant volume Cv = 12.47 J/(mol*K) and adiabatic index gamma = 1.67.\n\n"
            "## Diatomic Gases\n\n"
            "Diatomic Nitrogen and Oxygen exhibit higher heat capacity near 20.8 to 21.05 J/(mol*K) with gamma = 1.40 due to rotational degrees of freedom."
        ),
        "published": True,
        "concepts": [
            ("c-cv", "Molar Heat Capacity", "Energy required to raise one mole of gas by one kelvin at constant volume.", "Cv = dU/dT", ()),
            ("c-gamma", "Adiabatic Index", "Ratio Cp/Cv reflecting molecular degrees of freedom.", "gamma = 1.67 monatomic, 1.40 diatomic.", ("Molar Heat Capacity",)),
        ],
        "rels": [("c-cv", "prerequisite_of", "c-gamma")],
    },
    {
        "key": "wave_optics",
        "title": "Wave Optics Interference and Diffraction",
        "text": (
            "# Wave Optics\n\n"
            "Huygens wavefront principle models every point on a wavefront as a source of secondary wavelets.\n\n"
            "## Double-Slit Interference\n\n"
            "Coherent light passing through two slits forms constructive and destructive interference fringes determined by path difference."
        ),
        "published": True,
        "concepts": [
            ("c-huy", "Huygens Principle", "Secondary wavelet model explaining wavefront propagation.", None, ()),
            ("c-int", "Double-Slit Interference", "Superposition of coherent waves yielding bright and dark fringes.", None, ("Huygens Principle",)),
        ],
        "rels": [("c-huy", "prerequisite_of", "c-int")],
    },
    {
        "key": "energy",
        "title": "Conservation of Mechanical Energy",
        "text": (
            "# Mechanical Energy\n\n"
            "In a closed system without friction, total mechanical energy E = K + U remains conserved.\n\n"
            "## Kinetic and Gravitational Potential\n\n"
            "Kinetic energy is K = 0.5*m*v^2 and gravitational potential energy near Earth is U = m*g*h."
        ),
        "published": True,
        "concepts": [
            ("c-ke", "Kinetic Energy", "Translational energy proportional to mass and squared speed.", "K = 0.5*m*v^2", ()),
            ("c-cons", "Conservation of Mechanical Energy", "Total K + U stays constant when non-conservative work is zero.", None, ("Kinetic Energy",)),
        ],
        "rels": [("c-ke", "prerequisite_of", "c-cons")],
    },
    {
        "key": "unpublished_secret",
        "title": "Teacher Draft Exam Key",
        "text": "# Secret Answer Key\n\nUnpublished grading rubric for kinematics and binary search exam.",
        "published": False,
        "concepts": [],
        "rels": [],
    },
]

EVAL_QUERIES = [
    ("What is the formula for position under constant acceleration?", "kinematics"),
    ("Which quantity measures how fast displacement changes over time?", "kinematics"),
    ("How many steps does interval halving lookup take on an ordered list?", "binary_search"),
    ("Why must an array be ordered before halving the search range?", "binary_search"),
    ("Why do monatomic helium and diatomic nitrogen have different adiabatic gamma?", "thermodynamics"),
    ("What is the molar heat capacity of oxygen and nitrogen?", "thermodynamics"),
    ("How do secondary wavelets create bright and dark optical fringes?", "wave_optics"),
    ("Explain superposition of coherent light through two slits.", "wave_optics"),
    ("How does kinetic energy trade off with gravitational height without friction?", "energy"),
    ("What is the expression for translational motion energy and potential energy?", "energy"),
    # Paraphrased / semantic queries where exact keywords are sparse
    ("speeding up lookup by discarding half the remaining sorted items", "binary_search"),
    ("degrees of freedom affecting gas constant volume thermal energy storage", "thermodynamics"),
    ("secret grading rubric answer key for the exam", None),  # Must NEVER return unpublished doc!
]


def _ensure_onnx_model(target_dir: Path, download: bool) -> Path:
    target_dir.mkdir(parents=True, exist_ok=True)
    for fname, url in BGE_FILES.items():
        dst = target_dir / fname
        if not dst.is_file():
            if not download:
                raise FileNotFoundError(f"Missing {dst}; pass --download-model to fetch explicitly")
            print(f"Downloading {fname} from {url} ...")
            urllib.request.urlretrieve(url, dst)
    return target_dir


def _metrics_for_mode(
    retriever: HybridKnowledgeRetriever,
    ctx: TenantContext,
    source_to_key: dict[UUID, str],
    mode: RetrievalMode,
    *,
    k: int = 5,
) -> dict[str, object]:
    recalls_3: list[float] = []
    recalls_5: list[float] = []
    mrrs: list[float] = []
    ndcgs: list[float] = []
    latencies: list[float] = []
    tok_counts: list[int] = []
    dup_total = 0
    auth_leaks = 0
    graph_hits = 0

    for query, expected_key in EVAL_QUERIES:
        res = retriever.retrieve(
            ctx,
            KnowledgeRetrieveRequest(
                query=query,
                mode=mode,
                top_k=k,
                candidate_k=15,
                max_context_tokens=1600,
                published_only=True,
            ),
        )
        latencies.append(res.retrieval_ms)
        tok_counts.append(res.context_tokens)
        dup_total += res.duplicate_chunks_removed
        ranked_keys = [source_to_key[c.source_id] for c in res.chunks]
        if "unpublished_secret" in ranked_keys:
            auth_leaks += 1
        if expected_key is None:
            continue
        if res.graph_concepts:
            graph_hits += 1
        recalls_3.append(1.0 if expected_key in ranked_keys[:3] else 0.0)
        recalls_5.append(1.0 if expected_key in ranked_keys[:5] else 0.0)
        rr = 0.0
        dcg = 0.0
        for rank, key in enumerate(ranked_keys[:k], start=1):
            if key == expected_key:
                if rr == 0.0:
                    rr = 1.0 / rank
                dcg += 1.0 / math.log2(rank + 1)
                break
        mrrs.append(rr)
        ndcgs.append(dcg)  # single relevant doc per query -> idcg = 1.0

    lat_sorted = sorted(latencies)
    p95_idx = min(len(lat_sorted) - 1, max(0, math.ceil(0.95 * len(lat_sorted)) - 1))
    return {
        "mode": mode,
        "recall@3": round(statistics.mean(recalls_3), 4),
        "recall@5": round(statistics.mean(recalls_5), 4),
        "mrr": round(statistics.mean(mrrs), 4),
        "ndcg@5": round(statistics.mean(ndcgs), 4),
        "latency_mean_ms": round(statistics.mean(latencies), 3),
        "latency_p50_ms": round(statistics.median(latencies), 3),
        "latency_p95_ms": round(lat_sorted[p95_idx], 3),
        "mean_context_tokens": round(statistics.mean(tok_counts), 1),
        "duplicates_removed": dup_total,
        "graph_enriched_queries": graph_hits,
        "auth_leaks": auth_leaks,
    }


def run_evaluation(embedders: list[tuple[str, TextEmbedder]]) -> dict[str, object]:
    raw_admin = os.environ.get("VIORA_TEST_DATABASE_URL") or "postgresql+psycopg://viora:change-this-local-password@127.0.0.1:5432/viora"
    schema = f"viora_eval_{secrets.token_hex(4)}"
    app_role = f"viora_eval_app_{secrets.token_hex(4)}"
    app_pass = secrets.token_urlsafe(16)
    admin = create_engine(raw_admin, future=True)

    with admin.begin() as c:
        c.execute(CreateSchema(schema))
        c.connection.driver_connection.execute(
            sql.SQL(
                "CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE NOREPLICATION NOINHERIT"
            ).format(sql.Identifier(app_role), sql.Literal(app_pass))
        )

    migrate_url = make_url(raw_admin).update_query_dict({"options": f"-csearch_path={schema}"})
    app_url = make_url(raw_admin).update_query_dict({"options": f"-csearch_path={schema},public"})
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "backend" / "migrations"))
    os.environ["MIGRATION_DATABASE_URL"] = migrate_url.render_as_string(hide_password=False)
    try:
        command.upgrade(cfg, "head")
    finally:
        os.environ.pop("MIGRATION_DATABASE_URL", None)

    with admin.begin() as c:
        grant_application_privileges(c, app_role, schema)

    app_url = app_url.set(username=app_role, password=app_pass)
    app_db = Database(Settings(database_url=SecretStr(app_url.render_as_string(hide_password=False))))

    try:
        tenant_id, teacher_id, student_id = uuid4(), uuid4(), uuid4()
        yr, sem, sec, sub, off_id = uuid4(), uuid4(), uuid4(), uuid4(), uuid4()
        now = datetime.now(UTC)

        with admin.begin() as c:
            c.execute(text(f"SET LOCAL search_path TO {schema}, public"))
            c.execute(text("INSERT INTO institutions VALUES (:t, 'Eval', true)"), {"t": tenant_id})
            for uid, role in ((teacher_id, "subject_teacher"), (student_id, "student")):
                c.execute(text("INSERT INTO users VALUES (:u, :n, 'active')"), {"u": uid, "n": role})
                c.execute(text("INSERT INTO memberships VALUES (:t, :u, 'active')"), {"t": tenant_id, "u": uid})
                c.execute(text("INSERT INTO role_grants (tenant_id, user_id, role) VALUES (:t, :u, :r)"), {"t": tenant_id, "u": uid, "r": role})
            c.execute(text("INSERT INTO academic_years (tenant_id, id, name) VALUES (:t, :id, '2026')"), {"t": tenant_id, "id": yr})
            c.execute(text("INSERT INTO semesters (tenant_id, id, name, academic_year_id) VALUES (:t, :id, 'Fall', :y)"), {"t": tenant_id, "id": sem, "y": yr})
            c.execute(text("INSERT INTO sections (tenant_id, id, name, semester_id) VALUES (:t, :id, 'S1', :s)"), {"t": tenant_id, "id": sec, "s": sem})
            c.execute(text("INSERT INTO subjects (tenant_id, id, name) VALUES (:t, :id, 'STEM')"), {"t": tenant_id, "id": sub})
            c.execute(text("INSERT INTO subject_offerings (tenant_id, id, name, section_id, subject_id) VALUES (:t, :id, 'STEM 101', :sec, :sub)"), {"t": tenant_id, "id": off_id, "sec": sec, "sub": sub})
            c.execute(text("INSERT INTO teacher_assignments VALUES (:t, :u, :o, true)"), {"t": tenant_id, "o": off_id, "u": teacher_id})
            c.execute(text("INSERT INTO student_enrolments VALUES (:t, :u, :o, true)"), {"t": tenant_id, "o": off_id, "u": student_id})

        extractor = AdaptiveDocumentExtractor()
        limits = clamp_source_limits()
        source_to_key: dict[UUID, str] = {}
        prepared_docs = []

        for item in SYNTHETIC_DOCS:
            sid, vid, ext_id, ce_id = uuid4(), uuid4(), uuid4(), uuid4()
            source_to_key[sid] = str(item["key"])
            raw_bytes = str(item["text"]).encode("utf-8")
            sha = "a" * 64
            import hashlib
            sha = hashlib.sha256(raw_bytes).hexdigest()
            doc = extractor.extract(
                tenant_id=tenant_id,
                source_id=sid,
                version_id=vid,
                source_version=1,
                source_sha256=sha,
                input_format="typed_notes",
                title=str(item["title"]),
                raw_bytes=raw_bytes,
                limits=limits,
            )
            plan = chunk_extracted_document(doc)
            ev0 = doc.evidence_references[0]
            concepts = tuple(
                ConceptCandidate(
                    concept_id=cid,
                    name=name,
                    normalized_name=name.lower(),
                    canonical_summary=summary,
                    definitions=(ConceptDefinition(definition=defn, grounding="source_supported", evidence_ids=(ev0.evidence_id,)),) if defn else (),
                    prerequisites=tuple(PrerequisiteCandidate(prerequisite_name=p, grounding="source_supported") for p in prereqs),
                    grounding="source_supported",
                    evidence_bindings=(
                        ConceptEvidenceBinding(
                            evidence_id=ev0.evidence_id,
                            source_id=sid,
                            version_id=vid,
                            source_sha256=sha,
                            extraction_id=ext_id,
                            page_number=ev0.page_number,
                            section_id=ev0.section_id,
                            block_id=ev0.block_id,
                            verified=True,
                            excerpt=ev0.excerpt,
                        ),
                    ),
                    chunk_ids=(plan.chunks[0].chunk_id,) if plan.chunks else (),
                )
                for cid, name, summary, defn, prereqs in item["concepts"]
            )
            cname = {c.concept_id: c.name for c in concepts}
            rels = tuple(
                ConceptRelationshipCandidate(
                    relationship_id=f"rel-{i}",
                    source_concept_id=sc,
                    source_concept_name=cname[sc],
                    relation_type=rt,  # type: ignore[arg-type]
                    target_concept_id=tc,
                    target_concept_name=cname[tc],
                    grounding="source_supported",
                )
                for i, (sc, rt, tc) in enumerate(item["rels"], start=1)
            )
            kg = ExtractedKnowledgeGraph(
                source_id=sid,
                version_id=vid,
                derived_extraction_id=ext_id,
                source_sha256=sha,
                input_format="typed_notes",
                title=str(item["title"]),
                extraction_mode=plan.mode,
                published_to_students=bool(item["published"]),
                concepts=concepts,
                relationships=rels,
                validation=KnowledgeValidationReport(
                    total_concepts=len(concepts),
                    source_supported_count=len(concepts),
                    source_synthesized_count=0,
                    learning_request_count=0,
                    inferred_count=0,
                    uncertain_count=0,
                    unsupported_count=0,
                    invalid_evidence_refs_rejected=0,
                    duplicate_merges_count=0,
                    ambiguous_senses_retained=0,
                ),
                created_at=now,
            )

            with admin.begin() as c:
                c.execute(text(f"SET LOCAL search_path TO {schema}, public"))
                vis = "offering_published" if item["published"] else "teacher_only"
                c.execute(
                    text(
                        "INSERT INTO sources (tenant_id, source_id, title, offering_id, creator_user_id, owner_user_id, origin_kind, visibility, lifecycle_state, latest_version_number, latest_version_id) "
                        "VALUES (:t, :s, :ti, :o, :u, :u, 'teacher_resource', :v, 'active', 1, :vid)"
                    ),
                    {"t": tenant_id, "s": sid, "ti": item["title"], "o": off_id, "u": teacher_id, "v": vis, "vid": vid},
                )
                c.execute(
                    text(
                        "INSERT INTO source_versions (tenant_id, version_id, source_id, version_number, created_by_user_id, input_format, content_sha256, size_bytes, storage_kind, storage_resource_id, storage_version_id, storage_sha256, processing_status, extractor_id, contract_version, extraction_ms, char_count, page_count, warning_count, processing_metadata) "
                        "VALUES (:t, :v, :s, 1, :u, 'typed_notes', :sha, :sz, 'source_input', :s, :v, :sha, 'extracted', 'adaptive', '1.0', 1.0, :ch, 1, 0, '{}'::jsonb)"
                    ),
                    {"t": tenant_id, "v": vid, "s": sid, "u": teacher_id, "sha": sha, "sz": len(raw_bytes), "ch": len(doc.normalized_text)},
                )
                c.execute(
                    text(
                        "INSERT INTO derived_extractions (tenant_id, extraction_id, source_id, version_id, source_sha256, extraction_version, processing_status, extractor_id, extractor_version, contract_version, extraction_ms, char_count, page_count, warning_count, processing_metadata, document) "
                        "VALUES (:t, :e, :s, :v, :sha, 1, 'extracted', 'adaptive', '2.0', '1.0', 1.0, :ch, 1, 0, '{}'::jsonb, CAST(:doc AS jsonb))"
                    ),
                    {"t": tenant_id, "e": ext_id, "s": sid, "v": vid, "sha": sha, "ch": len(doc.normalized_text), "doc": doc.model_dump_json()},
                )
                c.execute(
                    text(
                        "INSERT INTO concept_extractions (tenant_id, concept_extraction_id, source_id, version_id, derived_extraction_id, extraction_version, concept_version, provider, model, prompt_version, schema_version, chunk_algorithm_version, input_fingerprint, input_tokens, output_tokens, total_tokens, inference_requests, chunking_ms, inference_ms, validation_ms, cache_hit, published_to_students, source_sha256, knowledge) "
                        "VALUES (:t, :ce, :s, :v, :e, 1, 1, 'eval', 'eval', 'p1', 's1', 'c1', :sha, 0, 0, 0, 0, 0, 0, 0, false, :pub, :sha, CAST(:kg AS jsonb))"
                    ),
                    {"t": tenant_id, "ce": ce_id, "s": sid, "v": vid, "e": ext_id, "sha": sha, "pub": bool(item["published"]), "kg": kg.model_dump_json()},
                )
                if item["published"]:
                    rev_id = uuid4()
                    c.execute(
                        text(
                            "INSERT INTO knowledge_reviews (tenant_id, review_id, source_id, version_id, concept_extraction_id, review_version, decision, approved_concept_ids, rejected_concept_ids, reviewed_by_user_id) "
                            "VALUES (:t, :r, :s, :v, :ce, 1, 'approved', '[]'::jsonb, '[]'::jsonb, :u)"
                        ),
                        {"t": tenant_id, "r": rev_id, "s": sid, "v": vid, "ce": ce_id, "u": teacher_id},
                    )
                    c.execute(
                        text(
                            "INSERT INTO knowledge_publications (tenant_id, version_id, source_id, concept_extraction_id, concept_version, source_sha256, review_status, latest_review_id, approved_review_id, published_to_students, published_by_user_id, published_at, outline) "
                            "VALUES (:t, :v, :s, :ce, 1, :sha, 'approved', :r, :r, true, :u, now(), '{}'::jsonb)"
                        ),
                        {"t": tenant_id, "v": vid, "s": sid, "ce": ce_id, "sha": sha, "r": rev_id, "u": teacher_id},
                    )

            prepared_docs.append((sid, vid, ext_id, ce_id, plan.chunks, ArtifactVersionRef(tenant_id=tenant_id, kind="source_input", resource_id=sid, version_id=vid, sha256=sha)))

        student_ctx = TenantContext(actor_id=student_id, tenant_id=tenant_id, roles=frozenset({"student"}))
        report: dict[str, object] = {"corpus_docs": len(SYNTHETIC_DOCS), "queries": len(EVAL_QUERIES), "embedders": {}}

        for label, embedder in embedders:
            import time as _t
            t_emb0 = _t.monotonic_ns()
            with app_db.sessions.begin() as s:
                bind_tenant(s, tenant_id)
                idx_repo = SqlKnowledgeIndexRepository(s)
                for sid, vid, ext_id, ce_id, chunks, _art in prepared_docs:
                    hashes = [compute_chunk_content_sha256(c) for c in chunks]
                    fps = [
                        compute_embedding_fingerprint(
                            content_sha256=h,
                            embedding_model=embedder.model_id,
                            embedding_model_version=embedder.model_version,
                            embedding_dim=embedder.dimensions,
                        )
                        for h in hashes
                    ]
                    vecs = embedder.embed_texts(
                        [f"{c.heading_title or ''}\n{c.rendered_text}" for c in chunks],
                        is_query=False,
                    )
                    idx_repo.commit_index_version(
                        tenant_id=tenant_id,
                        source_id=sid,
                        version_id=vid,
                        derived_extraction_id=ext_id,
                        concept_extraction_id=ce_id,
                        job_id=None,
                        scope_key="eval",
                        embedding_model=embedder.model_id,
                        embedding_model_version=embedder.model_version,
                        embedding_dim=embedder.dimensions,
                        chunks=chunks,
                        content_hashes=hashes,
                        fingerprints=fps,
                        embeddings=vecs,
                        now=now,
                    )
            index_ms = round((_t.monotonic_ns() - t_emb0) / 1_000_000.0, 2)

            mode_results = []
            with app_db.sessions.begin() as s:
                bind_tenant(s, tenant_id)
                retriever = HybridKnowledgeRetriever(s, embedder)
                for mode in ("lexical", "semantic", "hybrid", "hybrid_graph"):
                    mode_results.append(
                        _metrics_for_mode(retriever, student_ctx, source_to_key, mode)  # type: ignore[arg-type]
                    )

                # Capture representative PostgreSQL query plans
                qvec = "[" + ",".join("0.01" for _ in range(384)) + "]"
                lex_plan = [
                    r[0]
                    for r in s.execute(
                        text(
                            "EXPLAIN (ANALYZE, BUFFERS) "
                            "SELECT chunk_record_id FROM knowledge_index_chunks "
                            "WHERE tenant_id = :t AND is_active IS TRUE "
                            "AND search_tsv @@ to_tsquery('english', 'velocity | displacement') LIMIT 10"
                        ),
                        {"t": tenant_id},
                    ).all()
                ]
                vec_plan = [
                    r[0]
                    for r in s.execute(
                        text(
                            "EXPLAIN (ANALYZE, BUFFERS) "
                            "SELECT chunk_record_id FROM knowledge_index_chunks "
                            "WHERE tenant_id = :t AND is_active IS TRUE "
                            "ORDER BY embedding <=> CAST(:qv AS vector) LIMIT 10"
                        ),
                        {"t": tenant_id, "qv": qvec},
                    ).all()
                ]

            report["embedders"][label] = {  # type: ignore[index]
                "model_id": embedder.model_id,
                "model_version": embedder.model_version,
                "index_total_ms": index_ms,
                "modes": mode_results,
                "explain_lexical": lex_plan[:6],
                "explain_semantic": vec_plan[:6],
            }
        return report
    finally:
        app_db.close()
        with admin.begin() as c:
            c.execute(DropSchema(schema, cascade=True, if_exists=True))
            c.connection.driver_connection.execute(
                sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(app_role))
            )
        admin.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate Phase 3 retrieval quality & latency locally")
    parser.add_argument("--onnx-dir", type=Path, default=None)
    parser.add_argument("--download-model", action="store_true")
    args = parser.parse_args()

    embedders: list[tuple[str, TextEmbedder]] = [("fake", DeterministicFakeEmbedder())]
    if args.onnx_dir is not None:
        model_dir = _ensure_onnx_model(args.onnx_dir, args.download_model)
        embedders.append(("bge_small_int8_onnx", LocalOnnxEmbedder(model_dir)))

    report = run_evaluation(embedders)
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
