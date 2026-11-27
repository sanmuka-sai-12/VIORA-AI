"""Incremental, scope-reusable, workflow-fenced knowledge indexing."""

from __future__ import annotations

import hashlib
import json
import threading
from collections.abc import Sequence
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import select, text, update
from sqlalchemy.orm import Session

from ..authorization.public import AuthorizationService
from ..contracts import Actor, ArtifactVersionRef, JobState, ResourceRef
from ..db import Database
from ..security import AccessError
from ..sources.public import DerivedExtractionRecord, SqlSourceRepository
from ..workflow.public import (
    ClaimedWorkflowJob,
    JobCancelledError,
    SqlWorkflowRepository,
    SqlWorkflowWorkerStore,
    WorkerLeaseController,
    WorkflowJobSubmit,
)
from .chunking import chunk_extracted_document
from .embeddings import EMBEDDING_DIM, TextEmbedder
from .models import KnowledgeIndexChunk
from .repository import SqlConceptExtractionRepository, build_knowledge_scope_key
from .schemas import (
    CHUNK_ALGORITHM_VERSION,
    ChunkingConfig,
    DocumentChunk,
    KnowledgeIndexStatus,
)


def compute_chunk_content_sha256(chunk: DocumentChunk) -> str:
    payload = json.dumps(
        {
            "section_path": list(chunk.section_path),
            "heading_title": chunk.heading_title,
            "rendered_text": chunk.rendered_text,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def compute_embedding_fingerprint(
    *,
    content_sha256: str,
    embedding_model: str,
    embedding_model_version: str,
    embedding_dim: int,
) -> str:
    payload = json.dumps(
        {
            "content_sha256": content_sha256,
            "embedding_model": embedding_model,
            "embedding_model_version": embedding_model_version,
            "embedding_dim": embedding_dim,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _format_vector_literal(vec: Sequence[float]) -> str:
    return "[" + ",".join(f"{float(v):.8f}" for v in vec) + "]"


def _parse_vector_value(raw: object) -> list[float]:
    if isinstance(raw, list):
        return [float(x) for x in raw]
    if isinstance(raw, str):
        stripped = raw.strip().lstrip("[").rstrip("]")
        if not stripped:
            return []
        return [float(x) for x in stripped.split(",")]
    return [float(x) for x in raw]  # type: ignore[union-attr]


class SqlKnowledgeIndexRepository:
    """Tenant-bound RLS-enforced repository for versioned chunk & embedding indices."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def vector_schema(self) -> str | None:
        val = self.session.scalar(
            text(
                "SELECT n.nspname FROM pg_extension e "
                "JOIN pg_namespace n ON n.oid = e.extnamespace "
                "WHERE e.extname = 'vector'"
            )
        )
        return str(val) if val else None

    def has_pgvector(self) -> bool:
        return self.vector_schema() is not None

    def enqueue_index_job(
        self,
        *,
        tenant_id: UUID,
        offering_id: UUID | None,
        source_id: UUID,
        version_id: UUID,
        submitted_by_user_id: UUID,
        input_artifact: ArtifactVersionRef,
        idempotency_key: str | None,
        request_digest: str,
        now: datetime,
    ) -> tuple[UUID, JobState, bool]:
        job, replay = SqlWorkflowRepository(self.session).enqueue(
            WorkflowJobSubmit(
                tenant_id=tenant_id,
                offering_id=offering_id,
                source_id=source_id,
                version_id=version_id,
                submitted_by_user_id=submitted_by_user_id,
                operation_kind="knowledge_index",
                idempotency_key=idempotency_key,
                request_digest=request_digest,
                input_artifact=input_artifact,
            ),
            now=now,
        )
        return job.job_id, job.state, replay

    def get_index_status(
        self, tenant_id: UUID, source_id: UUID, version_id: UUID
    ) -> KnowledgeIndexStatus:
        rows = self.session.execute(
            select(
                KnowledgeIndexChunk.index_version,
                KnowledgeIndexChunk.derived_extraction_id,
                KnowledgeIndexChunk.concept_extraction_id,
                KnowledgeIndexChunk.embedding_model,
                KnowledgeIndexChunk.embedding_model_version,
                KnowledgeIndexChunk.chunk_algorithm_version,
                KnowledgeIndexChunk.updated_at,
            )
            .where(
                KnowledgeIndexChunk.tenant_id == tenant_id,
                KnowledgeIndexChunk.source_id == source_id,
                KnowledgeIndexChunk.version_id == version_id,
                KnowledgeIndexChunk.is_active.is_(True),
            )
            .order_by(KnowledgeIndexChunk.chunk_index.asc())
        ).all()
        if not rows:
            return KnowledgeIndexStatus(
                tenant_id=tenant_id,
                source_id=source_id,
                version_id=version_id,
                indexed=False,
            )
        first = rows[0]
        return KnowledgeIndexStatus(
            tenant_id=tenant_id,
            source_id=source_id,
            version_id=version_id,
            indexed=True,
            index_version=int(first.index_version),
            derived_extraction_id=first.derived_extraction_id,
            concept_extraction_id=first.concept_extraction_id,
            chunk_count=len(rows),
            embedding_model=first.embedding_model,
            embedding_model_version=first.embedding_model_version,
            chunk_algorithm_version=first.chunk_algorithm_version,
            updated_at=max(r.updated_at for r in rows),
        )

    def get_active_chunk_fingerprints(
        self, tenant_id: UUID, version_id: UUID
    ) -> list[tuple[str, UUID, UUID | None, str, str, str, int]]:
        rows = self.session.execute(
            select(
                KnowledgeIndexChunk.chunk_id,
                KnowledgeIndexChunk.derived_extraction_id,
                KnowledgeIndexChunk.concept_extraction_id,
                KnowledgeIndexChunk.embedding_fingerprint,
                KnowledgeIndexChunk.embedding_model,
                KnowledgeIndexChunk.embedding_model_version,
                KnowledgeIndexChunk.index_version,
            )
            .where(
                KnowledgeIndexChunk.tenant_id == tenant_id,
                KnowledgeIndexChunk.version_id == version_id,
                KnowledgeIndexChunk.is_active.is_(True),
            )
            .order_by(KnowledgeIndexChunk.chunk_index.asc())
        ).all()
        return [
            (
                r.chunk_id,
                r.derived_extraction_id,
                r.concept_extraction_id,
                r.embedding_fingerprint,
                r.embedding_model,
                r.embedding_model_version,
                int(r.index_version),
            )
            for r in rows
        ]

    def load_reusable_embeddings(
        self,
        *,
        tenant_id: UUID,
        scope_key: str,
        fingerprints: Sequence[str],
    ) -> dict[str, list[float]]:
        if not fingerprints:
            return {}
        rows = self.session.execute(
            text(
                "SELECT DISTINCT ON (embedding_fingerprint) embedding_fingerprint, embedding "
                "FROM knowledge_index_chunks "
                "WHERE tenant_id = :t AND scope_key = :sk AND embedding_fingerprint = ANY(:fps) "
                "AND embedding IS NOT NULL "
                "ORDER BY embedding_fingerprint, created_at DESC"
            ),
            {"t": tenant_id, "sk": scope_key, "fps": list(fingerprints)},
        ).all()
        return {
            str(r.embedding_fingerprint): _parse_vector_value(r.embedding)
            for r in rows
        }

    def commit_index_version(
        self,
        *,
        tenant_id: UUID,
        source_id: UUID,
        version_id: UUID,
        derived_extraction_id: UUID,
        concept_extraction_id: UUID | None,
        job_id: UUID | None,
        scope_key: str,
        embedding_model: str,
        embedding_model_version: str,
        embedding_dim: int,
        chunks: Sequence[DocumentChunk],
        content_hashes: Sequence[str],
        fingerprints: Sequence[str],
        embeddings: Sequence[Sequence[float] | None],
        now: datetime,
    ) -> int:
        prev_ver = self.session.scalar(
            select(KnowledgeIndexChunk.index_version)
            .where(
                KnowledgeIndexChunk.tenant_id == tenant_id,
                KnowledgeIndexChunk.version_id == version_id,
            )
            .order_by(KnowledgeIndexChunk.index_version.desc())
            .limit(1)
        )
        next_ver = (int(prev_ver) + 1) if prev_ver is not None else 1

        self.session.execute(
            update(KnowledgeIndexChunk)
            .where(
                KnowledgeIndexChunk.tenant_id == tenant_id,
                KnowledgeIndexChunk.version_id == version_id,
                KnowledgeIndexChunk.is_active.is_(True),
            )
            .values(is_active=False, updated_at=now)
        )

        vec_schema = self.vector_schema()
        use_pgvector = vec_schema is not None
        cast_expr = (
            f"CAST(:emb AS {vec_schema}.vector)"
            if use_pgvector
            else "CAST(:emb AS double precision[])"
        )
        insert_sql = text(
            f"""
            INSERT INTO knowledge_index_chunks (
                tenant_id, chunk_record_id, source_id, version_id,
                derived_extraction_id, concept_extraction_id, job_id, index_version,
                chunk_id, chunk_index, section_id, section_path, heading_title,
                page_start, page_end, estimated_tokens, evidence_ids, block_ids,
                rendered_text, content_sha256, chunk_algorithm_version, scope_key,
                embedding_model, embedding_model_version, embedding_dim,
                embedding_fingerprint, is_active, created_at, updated_at, embedding
            ) VALUES (
                :tenant_id, :chunk_record_id, :source_id, :version_id,
                :derived_extraction_id, :concept_extraction_id, :job_id, :index_version,
                :chunk_id, :chunk_index, :section_id, CAST(:section_path AS jsonb), :heading_title,
                :page_start, :page_end, :estimated_tokens, CAST(:evidence_ids AS jsonb), CAST(:block_ids AS jsonb),
                :rendered_text, :content_sha256, :chunk_algorithm_version, :scope_key,
                :embedding_model, :embedding_model_version, :embedding_dim,
                :embedding_fingerprint, true, :now, :now, {cast_expr}
            )
            """
        )
        for chunk, c_hash, fp, vec in zip(
            chunks, content_hashes, fingerprints, embeddings, strict=True
        ):
            emb_param: object = (
                None
                if vec is None
                else (_format_vector_literal(vec) if use_pgvector else [float(x) for x in vec])
            )
            self.session.execute(
                insert_sql,
                {
                    "tenant_id": tenant_id,
                    "chunk_record_id": uuid4(),
                    "source_id": source_id,
                    "version_id": version_id,
                    "derived_extraction_id": derived_extraction_id,
                    "concept_extraction_id": concept_extraction_id,
                    "job_id": job_id,
                    "index_version": next_ver,
                    "chunk_id": chunk.chunk_id,
                    "chunk_index": chunk.chunk_index,
                    "section_id": chunk.section_id,
                    "section_path": json.dumps(list(chunk.section_path)),
                    "heading_title": chunk.heading_title,
                    "page_start": chunk.page_start,
                    "page_end": chunk.page_end,
                    "estimated_tokens": chunk.estimated_tokens,
                    "evidence_ids": json.dumps(list(chunk.evidence_ids)),
                    "block_ids": json.dumps(list(chunk.block_ids)),
                    "rendered_text": chunk.rendered_text,
                    "content_sha256": c_hash,
                    "chunk_algorithm_version": CHUNK_ALGORITHM_VERSION,
                    "scope_key": scope_key,
                    "embedding_model": embedding_model,
                    "embedding_model_version": embedding_model_version,
                    "embedding_dim": embedding_dim,
                    "embedding_fingerprint": fp,
                    "now": now,
                    "emb": emb_param,
                },
            )
        return next_ver


class KnowledgeIndexingWorkerHandler:
    """Executes durable knowledge_index jobs with reauthorization, scoped reuse, and fenced commits."""

    def __init__(
        self,
        *,
        db: Database,
        access: AuthorizationService,
        embedder: TextEmbedder | None,
        chunk_config: ChunkingConfig | None = None,
        heartbeat_seconds: int = 5,
    ) -> None:
        self.db = db
        self.access = access
        self.embedder: TextEmbedder | None = embedder
        self.chunk_config = chunk_config or ChunkingConfig()
        self.heartbeat_seconds = max(1, heartbeat_seconds)

    def execute(
        self,
        job: ClaimedWorkflowJob,
        *,
        heartbeat: WorkerLeaseController,
    ) -> tuple[UUID, dict[str, str | int | float | bool]]:
        actor = Actor(actor_id=job.submitted_by_user_id)
        source_ref = ResourceRef(
            tenant_id=job.tenant_id, kind="source", resource_id=job.source_id
        )

        heartbeat.renew(stage="reauthorizing", progress_metadata={"stage": "reauthorizing"})
        if heartbeat.check_cancelled():
            raise JobCancelledError("Cancelled before indexing reauthorization")

        with self.access.reauthorize(actor, "source.process", source_ref) as scope:
            src_repo = SqlSourceRepository(scope.session)
            src_info = src_repo.get_source_access(job.tenant_id, job.source_id)
            ver = src_repo.get_version(job.tenant_id, job.source_id, job.version_id)
            derived: DerivedExtractionRecord | None = (
                src_repo.get_latest_derived_extraction(
                    job.tenant_id, job.source_id, job.version_id
                )
            )
            if src_info is None or ver is None or derived is None:
                raise AccessError(
                    409,
                    "derived_extraction_missing",
                    "Derived document extraction must exist before indexing",
                )
            if src_info.lifecycle_state != "active":
                raise AccessError(
                    409, "source_archived", "Archived sources cannot be indexed"
                )
            latest_concept = SqlConceptExtractionRepository(
                scope.session
            ).get_latest_for_version(job.tenant_id, job.version_id)
            concept_extraction_id = (
                latest_concept.concept_extraction_id
                if latest_concept is not None
                and latest_concept.derived_extraction_id == derived.extraction_id
                else None
            )
            scope_key = build_knowledge_scope_key(
                offering_id=src_info.offering_id,
                origin_kind=src_info.origin_kind,
                owner_user_id=src_info.owner_user_id,
            )
            emb_model = self.embedder.model_id if self.embedder is not None else "none"
            emb_ver = (
                self.embedder.model_version if self.embedder is not None else "lexical_only"
            )
            emb_dim = self.embedder.dimensions if self.embedder is not None else EMBEDDING_DIM
            plan = chunk_extracted_document(derived.document, config=self.chunk_config)
            chunks = plan.chunks
            content_hashes = [compute_chunk_content_sha256(c) for c in chunks]
            fingerprints = [
                compute_embedding_fingerprint(
                    content_sha256=ch,
                    embedding_model=emb_model,
                    embedding_model_version=emb_ver,
                    embedding_dim=emb_dim,
                )
                for ch in content_hashes
            ]

            idx_repo = SqlKnowledgeIndexRepository(scope.session)
            active_rows = idx_repo.get_active_chunk_fingerprints(
                job.tenant_id, job.version_id
            )
            already_current = (
                len(active_rows) == len(chunks)
                and all(
                    row[0] == c.chunk_id
                    and row[1] == derived.extraction_id
                    and row[2] == concept_extraction_id
                    and row[3] == fp
                    and row[4] == emb_model
                    and row[5] == emb_ver
                    for row, c, fp in zip(active_rows, chunks, fingerprints, strict=True)
                )
            )
            if already_current:
                index_version = active_rows[0][6] if active_rows else 1
                meta: dict[str, str | int | float | bool] = {
                    "index_version": index_version,
                    "chunk_count": len(chunks),
                    "reused_embeddings": len(chunks) if self.embedder is not None else 0,
                    "computed_embeddings": 0,
                    "idempotent_noop": True,
                }
                SqlWorkflowWorkerStore.finalize_succeeded(
                    scope.session,
                    tenant_id=job.tenant_id,
                    job_id=job.job_id,
                    worker_id=job.lease_owner,
                    fencing_token=job.fencing_token,
                    extraction_id=derived.extraction_id,
                    result_metadata=meta,
                    progress_metadata={"stage": "completed", **meta},
                    now=datetime.now(UTC),
                )
                return derived.extraction_id, meta

            reusable = (
                idx_repo.load_reusable_embeddings(
                    tenant_id=job.tenant_id,
                    scope_key=scope_key,
                    fingerprints=list(dict.fromkeys(fingerprints)),
                )
                if self.embedder is not None
                else {}
            )

        # Stage 2: Compute missing embeddings OUTSIDE DB transaction
        missing_fps: list[str] = []
        missing_texts: list[str] = []
        seen_missing: set[str] = set()
        if self.embedder is not None:
            for chunk, fp in zip(chunks, fingerprints, strict=True):
                if fp not in reusable and fp not in seen_missing:
                    seen_missing.add(fp)
                    missing_fps.append(fp)
                    heading = f"{chunk.heading_title}\n" if chunk.heading_title else ""
                    missing_texts.append(f"{heading}{chunk.rendered_text}")

        heartbeat.renew(
            stage="embedding_chunks",
            progress_metadata={
                "stage": "embedding_chunks",
                "total_chunks": len(chunks),
                "reused": len(chunks) - len(missing_fps),
                "to_embed": len(missing_fps),
            },
        )
        if heartbeat.check_cancelled():
            raise JobCancelledError("Cancelled before embedding generation")

        computed_vectors: dict[str, list[float]] = {}
        if missing_texts:
            assert self.embedder is not None
            stop_hb = threading.Event()

            def _hb() -> None:
                while not stop_hb.wait(self.heartbeat_seconds):
                    try:
                        res = heartbeat.renew(
                            stage="embedding_chunks",
                            progress_metadata={"stage": "embedding_chunks"},
                        )
                        if not res.renewed or res.cancel_requested:
                            break
                    except Exception:  # noqa: BLE001
                        break

            t = threading.Thread(target=_hb, daemon=True)
            t.start()
            try:
                vecs = self.embedder.embed_texts(missing_texts, is_query=False)
            finally:
                stop_hb.set()
                t.join(timeout=1.0)
            for fp, vec in zip(missing_fps, vecs, strict=True):
                computed_vectors[fp] = vec

        if heartbeat.check_cancelled():
            raise JobCancelledError("Cancelled before committing knowledge index")

        ordered_vectors: list[list[float] | None] = [
            None
            if self.embedder is None
            else (reusable[fp] if fp in reusable else computed_vectors[fp])
            for fp in fingerprints
        ]
        reused_count = (
            sum(1 for fp in fingerprints if fp in reusable)
            if self.embedder is not None
            else 0
        )

        # Stage 3: Reauthorize and commit fenced index version
        with self.access.reauthorize(actor, "source.process", source_ref) as scope:
            idx_repo = SqlKnowledgeIndexRepository(scope.session)
            now = datetime.now(UTC)
            index_ver = idx_repo.commit_index_version(
                tenant_id=job.tenant_id,
                source_id=job.source_id,
                version_id=job.version_id,
                derived_extraction_id=derived.extraction_id,
                concept_extraction_id=concept_extraction_id,
                job_id=job.job_id,
                scope_key=scope_key,
                embedding_model=emb_model,
                embedding_model_version=emb_ver,
                embedding_dim=emb_dim,
                chunks=chunks,
                content_hashes=content_hashes,
                fingerprints=fingerprints,
                embeddings=ordered_vectors,
                now=now,
            )
            result_meta: dict[str, str | int | float | bool] = {
                "index_version": index_ver,
                "chunk_count": len(chunks),
                "reused_embeddings": reused_count,
                "computed_embeddings": len(missing_fps),
                "idempotent_noop": False,
            }
            SqlWorkflowWorkerStore.finalize_succeeded(
                scope.session,
                tenant_id=job.tenant_id,
                job_id=job.job_id,
                worker_id=job.lease_owner,
                fencing_token=job.fencing_token,
                extraction_id=derived.extraction_id,
                result_metadata=result_meta,
                progress_metadata={"stage": "completed", **result_meta},
                now=now,
            )
            return derived.extraction_id, result_meta
