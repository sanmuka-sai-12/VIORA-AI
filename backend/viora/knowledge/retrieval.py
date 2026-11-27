"""Authorized PostgreSQL hybrid (FTS + pgvector) and bounded graph-assisted retrieval."""

from __future__ import annotations

import re
import time
from collections import deque
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from ..contracts import TenantContext
from ..security import AccessError
from .chunking import estimate_tokens
from .embeddings import TextEmbedder
from .indexing import _format_vector_literal, _parse_vector_value
from .models import ConceptExtraction, KnowledgeReview
from .publication import publication_complete, reviewed_graph
from .schemas import (
    ExtractedKnowledgeGraph,
    GraphEnrichmentConcept,
    KnowledgeRetrievalResult,
    KnowledgeRetrieveRequest,
    RetrievedEvidenceChunk,
)

_QUERY_TOKEN_RE = re.compile(r"[a-z0-9]+")

_APPROVED_LESSON_PREDICATE = """
  s.visibility = 'offering_published'
  AND kp.published_to_students IS TRUE
  AND kp.published_at IS NOT NULL
  AND kp.review_status = 'approved'
  AND kp.approved_review_id IS NOT NULL
  AND kp.source_id = kic.source_id
  AND kp.version_id = kic.version_id
  AND kp.concept_extraction_id = kic.concept_extraction_id
  AND ce.concept_extraction_id IS NOT NULL
  AND ce.source_id = kic.source_id
  AND ce.version_id = kic.version_id
  AND ce.derived_extraction_id = kic.derived_extraction_id
  AND ce.concept_version = kp.concept_version
  AND ce.source_sha256 = kp.source_sha256
  AND sv.content_sha256 = kp.source_sha256
  AND kr.review_id IS NOT NULL
  AND kr.source_id = kic.source_id
  AND kr.version_id = kic.version_id
  AND kr.concept_extraction_id = kp.concept_extraction_id
  AND kr.decision = 'approved'
""".strip()

_AUTH_FROM_AND_WHERE = f"""
FROM knowledge_index_chunks kic
JOIN sources s
  ON s.tenant_id = kic.tenant_id AND s.source_id = kic.source_id
JOIN source_versions sv
  ON sv.tenant_id = kic.tenant_id AND sv.source_id = kic.source_id AND sv.version_id = kic.version_id
JOIN institutions inst
  ON inst.tenant_id = kic.tenant_id
JOIN subject_offerings o
  ON o.tenant_id = s.tenant_id AND o.id = s.offering_id
JOIN sections sec
  ON sec.tenant_id = o.tenant_id AND sec.id = o.section_id
JOIN subjects sub
  ON sub.tenant_id = o.tenant_id AND sub.id = o.subject_id
JOIN semesters sem
  ON sem.tenant_id = sec.tenant_id AND sem.id = sec.semester_id
JOIN academic_years ay
  ON ay.tenant_id = sem.tenant_id AND ay.id = sem.academic_year_id
LEFT JOIN programmes prog
  ON prog.tenant_id = sec.tenant_id AND prog.id = sec.programme_id
LEFT JOIN knowledge_publications kp
  ON kp.tenant_id = kic.tenant_id AND kp.version_id = kic.version_id
LEFT JOIN concept_extractions ce
  ON ce.tenant_id = kic.tenant_id AND ce.concept_extraction_id = kic.concept_extraction_id
LEFT JOIN knowledge_reviews kr
  ON kr.tenant_id = kp.tenant_id AND kr.review_id = kp.approved_review_id
LEFT JOIN teacher_assignments ta
  ON ta.tenant_id = s.tenant_id AND ta.offering_id = s.offering_id
  AND ta.user_id = :actor_id AND ta.active IS TRUE
LEFT JOIN student_enrolments se
  ON se.tenant_id = s.tenant_id AND se.offering_id = s.offering_id
  AND se.user_id = :actor_id AND se.active IS TRUE
LEFT JOIN offering_source_policies osp
  ON osp.tenant_id = s.tenant_id AND osp.offering_id = s.offering_id
WHERE kic.tenant_id = :tenant_id
  AND kic.is_active IS TRUE
  AND inst.active IS TRUE
  AND s.lifecycle_state = 'active'
  AND s.latest_version_id = kic.version_id
  AND o.active IS TRUE
  AND sec.active IS TRUE
  AND sub.active IS TRUE
  AND sem.active IS TRUE
  AND ay.active IS TRUE
  AND (sec.programme_id IS NULL OR prog.active IS TRUE)
  AND (CAST(:offering_id AS uuid) IS NULL OR s.offering_id = CAST(:offering_id AS uuid))
  AND (
    (
      s.origin_kind = 'teacher_resource'
      AND (
        (
          ta.user_id IS NOT NULL
          AND :is_teacher_eligible IS TRUE
          AND (
            NOT :published_only
            OR (
              {_APPROVED_LESSON_PREDICATE}
            )
          )
        )
        OR (
          se.user_id IS NOT NULL
          AND :is_student_eligible IS TRUE
          AND (
            {_APPROVED_LESSON_PREDICATE}
          )
        )
      )
    )
    OR (
      :include_student_submissions IS TRUE
      AND NOT :published_only
      AND s.origin_kind = 'student_submission'
      AND (
        (s.owner_user_id = :actor_id AND se.user_id IS NOT NULL AND :is_student_eligible IS TRUE)
        OR (
          ta.user_id IS NOT NULL
          AND :is_teacher_eligible IS TRUE
          AND s.visibility = 'teacher_review'
          AND COALESCE(osp.teachers_can_view_student_submissions, true) IS TRUE
        )
      )
    )
  )
"""

_SELECT_COLS = """
    kic.chunk_record_id,
    kic.source_id,
    kic.version_id,
    kic.derived_extraction_id,
    kic.concept_extraction_id,
    kic.index_version,
    kic.chunk_id,
    kic.chunk_index,
    kic.section_id,
    kic.section_path,
    kic.heading_title,
    kic.page_start,
    kic.page_end,
    kic.evidence_ids,
    kic.rendered_text,
    kic.content_sha256,
    kic.estimated_tokens,
    s.origin_kind,
    s.visibility,
    kp.approved_review_id,
    (
      s.origin_kind = 'teacher_resource'
      AND (ta.user_id IS NULL OR CAST(:published_only AS boolean))
    ) AS filter_approved_graph
"""


@dataclass
class _CandidateRow:
    chunk_record_id: UUID
    source_id: UUID
    version_id: UUID
    derived_extraction_id: UUID
    concept_extraction_id: UUID | None
    index_version: int
    chunk_id: str
    chunk_index: int
    section_id: str
    section_path: tuple[str, ...]
    heading_title: str | None
    page_start: int
    page_end: int
    evidence_ids: tuple[str, ...]
    rendered_text: str
    content_sha256: str
    estimated_tokens: int
    origin_kind: str
    visibility: str
    approved_review_id: UUID | None = None
    filter_approved_graph: bool = False
    lexical_score: float = 0.0
    semantic_score: float = 0.0
    lexical_rank: int | None = None
    semantic_rank: int | None = None
    rrf_score: float = 0.0


def _cosine_sim(a: Sequence[float], b: Sequence[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    if na <= 1e-12 or nb <= 1e-12:
        return 0.0
    return float(dot / (na * nb))


def _row_to_candidate(row: Any) -> _CandidateRow:
    return _CandidateRow(
        chunk_record_id=row.chunk_record_id,
        source_id=row.source_id,
        version_id=row.version_id,
        derived_extraction_id=row.derived_extraction_id,
        concept_extraction_id=row.concept_extraction_id,
        index_version=int(row.index_version),
        chunk_id=str(row.chunk_id),
        chunk_index=int(row.chunk_index),
        section_id=str(row.section_id),
        section_path=tuple(row.section_path or ()),
        heading_title=row.heading_title,
        page_start=int(row.page_start),
        page_end=int(row.page_end),
        evidence_ids=tuple(row.evidence_ids or ()),
        rendered_text=str(row.rendered_text),
        content_sha256=str(row.content_sha256),
        estimated_tokens=int(row.estimated_tokens),
        origin_kind=str(row.origin_kind),
        visibility=str(row.visibility),
        approved_review_id=getattr(row, "approved_review_id", None),
        filter_approved_graph=bool(getattr(row, "filter_approved_graph", False)),
    )


@dataclass(frozen=True)
class _ApprovedFilterState:
    graphs: dict[UUID, ExtractedKnowledgeGraph]
    blocked_ce_ids: frozenset[UUID]
    rejected_chunks: dict[UUID, frozenset[str]]
    rejected_evs: dict[UUID, frozenset[str]]


_EMPTY_FILTER_STATE = _ApprovedFilterState(
    graphs={},
    blocked_ce_ids=frozenset(),
    rejected_chunks={},
    rejected_evs={},
)


def _is_chunk_rejected(
    state: _ApprovedFilterState,
    *,
    ce_id: UUID | None,
    chunk_id: str,
    evidence_ids: Sequence[str],
) -> bool:
    if ce_id is None or ce_id in state.blocked_ce_ids:
        return True
    rej_chks = state.rejected_chunks.get(ce_id, frozenset())
    rej_evs = state.rejected_evs.get(ce_id, frozenset())
    return chunk_id in rej_chks or bool(set(evidence_ids) & rej_evs)


def _load_approved_filter_state(
    session: Session,
    *,
    tenant_id: UUID,
    candidates: Sequence[_CandidateRow],
) -> _ApprovedFilterState:
    meta_by_ce: dict[UUID, tuple[UUID, UUID, UUID]] = {}
    for ch in candidates:
        if not ch.filter_approved_graph or ch.concept_extraction_id is None:
            continue
        if ch.approved_review_id is None:
            meta_by_ce.setdefault(ch.concept_extraction_id, (ch.source_id, ch.version_id, UUID(int=0)))
        else:
            meta_by_ce[ch.concept_extraction_id] = (
                ch.source_id,
                ch.version_id,
                ch.approved_review_id,
            )
    if not meta_by_ce:
        return _EMPTY_FILTER_STATE

    ce_ids = list(meta_by_ce)
    review_ids = list({rid for _, _, rid in meta_by_ce.values() if rid.int != 0})
    ce_rows = {
        r.concept_extraction_id: r
        for r in session.execute(
            select(
                ConceptExtraction.concept_extraction_id,
                ConceptExtraction.source_id,
                ConceptExtraction.version_id,
                ConceptExtraction.knowledge,
            ).where(
                ConceptExtraction.tenant_id == tenant_id,
                ConceptExtraction.concept_extraction_id.in_(ce_ids),
            )
        ).all()
    }
    rev_rows = (
        {
            r.review_id: r
            for r in session.execute(
                select(
                    KnowledgeReview.review_id,
                    KnowledgeReview.source_id,
                    KnowledgeReview.version_id,
                    KnowledgeReview.concept_extraction_id,
                    KnowledgeReview.rejected_concept_ids,
                ).where(
                    KnowledgeReview.tenant_id == tenant_id,
                    KnowledgeReview.review_id.in_(review_ids),
                    KnowledgeReview.decision == "approved",
                )
            ).all()
        }
        if review_ids
        else {}
    )

    graphs: dict[UUID, ExtractedKnowledgeGraph] = {}
    blocked: set[UUID] = set()
    rej_chunks_map: dict[UUID, frozenset[str]] = {}
    rej_evs_map: dict[UUID, frozenset[str]] = {}

    for ce_id, (sid, vid, rid) in meta_by_ce.items():
        ce_row = ce_rows.get(ce_id)
        rev_row = rev_rows.get(rid)
        if (
            ce_row is None
            or rev_row is None
            or ce_row.source_id != sid
            or ce_row.version_id != vid
            or rev_row.source_id != sid
            or rev_row.version_id != vid
            or rev_row.concept_extraction_id != ce_id
        ):
            blocked.add(ce_id)
            continue
        kg = ExtractedKnowledgeGraph.model_validate(ce_row.knowledge)
        rej_ids = tuple(str(cid) for cid in (rev_row.rejected_concept_ids or ()))
        reviewed = reviewed_graph(kg, rej_ids)
        if not publication_complete(reviewed):
            blocked.add(ce_id)
            continue
        graphs[ce_id] = reviewed
        rej_set = set(rej_ids)
        if rej_set:
            rej_evs = {
                b.evidence_id
                for c in kg.concepts
                if c.concept_id in rej_set
                for b in c.evidence_bindings
            }
            rej_chks = {
                cid
                for c in kg.concepts
                if c.concept_id in rej_set
                for cid in c.chunk_ids
            }
            rej_evs_map[ce_id] = frozenset(rej_evs)
            rej_chunks_map[ce_id] = frozenset(rej_chks)

    return _ApprovedFilterState(
        graphs=graphs,
        blocked_ce_ids=frozenset(blocked),
        rejected_chunks=rej_chunks_map,
        rejected_evs=rej_evs_map,
    )


def _enrich_with_concept_graph(
    session: Session,
    *,
    tenant_id: UUID,
    query: str,
    selected: Sequence[_CandidateRow],
    max_hops: int,
    max_seeds: int = 4,
    max_expanded: int = 8,
    preloaded_graphs: dict[UUID, ExtractedKnowledgeGraph] | None = None,
) -> tuple[tuple[GraphEnrichmentConcept, ...], int, int]:
    """Bounded cycle-safe BFS over authorized ConceptExtraction graphs."""
    ce_ids = list(
        dict.fromkeys(
            c.concept_extraction_id for c in selected if c.concept_extraction_id is not None
        )
    )
    if not ce_ids:
        return (), 0, 0

    preloaded = preloaded_graphs or {}
    remaining_ce_ids = [ce_id for ce_id in ce_ids if ce_id not in preloaded]
    rows = (
        session.execute(
            select(
                ConceptExtraction.concept_extraction_id,
                ConceptExtraction.knowledge,
            ).where(
                ConceptExtraction.tenant_id == tenant_id,
                ConceptExtraction.concept_extraction_id.in_(remaining_ce_ids),
            )
        ).all()
        if remaining_ce_ids
        else []
    )
    if not rows and not any(ce_id in preloaded for ce_id in ce_ids):
        return (), 0, 0

    raw_graphs: dict[UUID, ExtractedKnowledgeGraph] = {
        ce_id: preloaded[ce_id] for ce_id in ce_ids if ce_id in preloaded
    }
    for ce_id, payload in rows:
        raw_graphs[ce_id] = ExtractedKnowledgeGraph.model_validate(payload)

    query_tokens = set(_QUERY_TOKEN_RE.findall(query.lower()))
    chunk_ev_by_ce: dict[UUID, set[str]] = {}
    chunk_ids_by_ce: dict[UUID, set[str]] = {}
    for ch in selected:
        if ch.concept_extraction_id is not None:
            chunk_ev_by_ce.setdefault(ch.concept_extraction_id, set()).update(
                ch.evidence_ids
            )
            chunk_ids_by_ce.setdefault(ch.concept_extraction_id, set()).add(ch.chunk_id)

    graphs: dict[UUID, ExtractedKnowledgeGraph] = {}
    scored_seeds: list[tuple[tuple[int, int, int], UUID, str]] = []
    for ce_id, kg in raw_graphs.items():
        graphs[ce_id] = kg
        ev_set = chunk_ev_by_ce.get(ce_id, set())
        cid_set = chunk_ids_by_ce.get(ce_id, set())
        for concept in kg.concepts:
            c_evs = {b.evidence_id for b in concept.evidence_bindings}
            ev_hits = len(c_evs & ev_set)
            chunk_hits = len(set(concept.chunk_ids) & cid_set)
            name_hits = len(set(_QUERY_TOKEN_RE.findall(concept.normalized_name)) & query_tokens)
            if ev_hits > 0 or chunk_hits > 0 or name_hits > 0:
                scored_seeds.append(
                    ((ev_hits + chunk_hits * 2, name_hits, len(c_evs)), ce_id, concept.concept_id)
                )

    if not scored_seeds:
        return (), 0, 0
    scored_seeds.sort(key=lambda item: (item[0], str(item[1]), item[2]), reverse=True)
    seeds = scored_seeds[:max_seeds]

    # Build adjacency per concept_extraction_id without crossing graph boundaries
    adj_by_ce: dict[UUID, dict[str, list[tuple[str, str, str]]]] = {}
    concepts_by_ce: dict[UUID, dict[str, Any]] = {}
    for ce_id, kg in graphs.items():
        concepts_by_ce[ce_id] = {c.concept_id: c for c in kg.concepts}
        adj: dict[str, list[tuple[str, str, str]]] = {}
        for rel in kg.relationships:
            if (
                rel.source_concept_id in concepts_by_ce[ce_id]
                and rel.target_concept_id in concepts_by_ce[ce_id]
            ):
                adj.setdefault(rel.source_concept_id, []).append(
                    (
                        rel.target_concept_id,
                        rel.relation_type,
                        f"{rel.source_concept_name} --[{rel.relation_type}]--> {rel.target_concept_name}",
                    )
                )
                adj.setdefault(rel.target_concept_id, []).append(
                    (
                        rel.source_concept_id,
                        f"inverse:{rel.relation_type}",
                        f"{rel.source_concept_name} --[{rel.relation_type}]--> {rel.target_concept_name}",
                    )
                )
        adj_by_ce[ce_id] = adj

    visited: set[tuple[UUID, str]] = set()
    queue: deque[tuple[UUID, str, int, tuple[str, ...]]] = deque()
    for _, ce_id, cid in seeds:
        key = (ce_id, cid)
        if key not in visited:
            visited.add(key)
            queue.append((ce_id, cid, 0, ()))

    enriched: list[GraphEnrichmentConcept] = []
    cycles_avoided = 0
    max_hop_seen = 0

    while queue and len(enriched) < max_expanded:
        ce_id, cid, hop, paths = queue.popleft()
        max_hop_seen = max(max_hop_seen, hop)
        concept = concepts_by_ce[ce_id][cid]
        enriched.append(
            GraphEnrichmentConcept(
                concept_id=concept.concept_id,
                name=concept.name,
                sense_label=concept.sense_label,
                grounding=concept.grounding,
                canonical_summary=concept.canonical_summary,
                definitions=tuple(d.definition for d in concept.definitions[:2]),
                prerequisites=tuple(p.prerequisite_name for p in concept.prerequisites[:4]),
                relation_paths=paths[:4],
                evidence_ids=tuple(b.evidence_id for b in concept.evidence_bindings[:4]),
                hop_distance=hop,
            )
        )
        if hop >= max_hops:
            continue
        for nbr_id, _rel_type, label in adj_by_ce[ce_id].get(cid, []):
            nbr_key = (ce_id, nbr_id)
            if nbr_key in visited:
                cycles_avoided += 1
                continue
            visited.add(nbr_key)
            queue.append((ce_id, nbr_id, hop + 1, paths + (label,)))

    return tuple(enriched), max_hop_seen, cycles_avoided


class HybridKnowledgeRetriever:
    """Authorized PostgreSQL FTS + pgvector RRF + parent-child + bounded graph retrieval."""

    def __init__(self, session: Session, embedder: TextEmbedder | None = None) -> None:
        self.session = session
        self.embedder = embedder

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

    def retrieve(
        self,
        context: TenantContext,
        req: KnowledgeRetrieveRequest,
    ) -> KnowledgeRetrievalResult:
        t0 = time.monotonic_ns()
        if self.embedder is None:
            if req.mode == "semantic":
                raise AccessError(
                    503,
                    "embeddings_unavailable",
                    "Semantic retrieval requires an active local embedding model",
                )
            effective_mode = "lexical" if req.mode == "hybrid" else req.mode
        else:
            effective_mode = req.mode

        is_teacher = "subject_teacher" in context.roles
        is_student = "student" in context.roles
        auth_params: dict[str, object] = {
            "tenant_id": context.tenant_id,
            "actor_id": context.actor_id,
            "offering_id": req.offering_id,
            "is_teacher_eligible": is_teacher,
            "is_student_eligible": is_student,
            "include_student_submissions": req.include_student_submissions,
            "published_only": req.published_only,
            "candidate_k": req.candidate_k,
        }

        candidates_by_id: dict[UUID, _CandidateRow] = {}
        lexical_order: list[UUID] = []
        semantic_order: list[UUID] = []

        # 1. PostgreSQL Full-Text Search (GIN-indexed search_tsv)
        if effective_mode in {"lexical", "hybrid", "hybrid_graph"}:
            auth_with_q = _AUTH_FROM_AND_WHERE.replace(
                "FROM knowledge_index_chunks kic",
                "FROM knowledge_index_chunks kic\nCROSS JOIN q",
                1,
            )
            lex_sql = text(
                f"""
                WITH q AS (
                    SELECT COALESCE(
                        NULLIF(replace(plainto_tsquery('english', :query)::text, ' & ', ' | '), ''),
                        NULLIF(replace(plainto_tsquery('simple', :query)::text, ' & ', ' | '), '')
                    ) AS tsq_txt
                )
                SELECT {_SELECT_COLS},
                       ts_rank_cd(kic.search_tsv, to_tsquery('english', q.tsq_txt)) AS lex_score
                {auth_with_q}
                  AND q.tsq_txt IS NOT NULL
                  AND kic.search_tsv @@ to_tsquery('english', q.tsq_txt)
                ORDER BY lex_score DESC, kic.chunk_record_id ASC
                LIMIT :candidate_k
                """
            )
            lex_rows = self.session.execute(
                lex_sql, {**auth_params, "query": req.query}
            ).all()
            for rank_idx, row in enumerate(lex_rows, start=1):
                cand = candidates_by_id.get(row.chunk_record_id)
                if cand is None:
                    cand = _row_to_candidate(row)
                    candidates_by_id[row.chunk_record_id] = cand
                cand.lexical_score = float(row.lex_score or 0.0)
                cand.lexical_rank = rank_idx
                lexical_order.append(row.chunk_record_id)

        # 2. Semantic Vector Retrieval (pgvector HNSW <=> or array cosine fallback)
        if self.embedder is not None and effective_mode in {"semantic", "hybrid", "hybrid_graph"}:
            q_vec = self.embedder.embed_texts([req.query], is_query=True)[0]
            vec_schema = self.vector_schema()
            if vec_schema is not None:
                self.session.execute(
                    text("SET LOCAL hnsw.iterative_scan = 'relaxed_order'")
                )
                dist_expr = f"(kic.embedding OPERATOR({vec_schema}.<=>) CAST(:qvec AS {vec_schema}.vector))"
                sem_sql = text(
                    f"""
                    SELECT {_SELECT_COLS},
                           (1.0 - {dist_expr}) AS sem_score
                    {_AUTH_FROM_AND_WHERE}
                      AND kic.embedding IS NOT NULL
                      AND kic.embedding_model = :emb_model
                      AND kic.embedding_model_version = :emb_ver
                    ORDER BY {dist_expr} ASC, kic.chunk_record_id ASC
                    LIMIT :candidate_k
                    """
                )
                sem_rows = self.session.execute(
                    sem_sql,
                    {
                        **auth_params,
                        "qvec": _format_vector_literal(q_vec),
                        "emb_model": self.embedder.model_id,
                        "emb_ver": self.embedder.model_version,
                    },
                ).all()
                sem_scored = [(row, float(row.sem_score or 0.0)) for row in sem_rows]
            else:
                fb_sql = text(
                    f"""
                    SELECT {_SELECT_COLS}, kic.embedding
                    {_AUTH_FROM_AND_WHERE}
                      AND kic.embedding IS NOT NULL
                      AND kic.embedding_model = :emb_model
                      AND kic.embedding_model_version = :emb_ver
                    """
                )
                fb_rows = self.session.execute(
                    fb_sql,
                    {
                        **auth_params,
                        "emb_model": self.embedder.model_id,
                        "emb_ver": self.embedder.model_version,
                    },
                ).all()
                sem_scored = sorted(
                    (
                        (row, _cosine_sim(q_vec, _parse_vector_value(row.embedding)))
                        for row in fb_rows
                    ),
                    key=lambda item: (-item[1], str(item[0].chunk_record_id)),
                )[: req.candidate_k]

            for rank_idx, (row, score) in enumerate(sem_scored, start=1):
                cand = candidates_by_id.get(row.chunk_record_id)
                if cand is None:
                    cand = _row_to_candidate(row)
                    candidates_by_id[row.chunk_record_id] = cand
                cand.semantic_score = float(score)
                cand.semantic_rank = rank_idx
                semantic_order.append(row.chunk_record_id)

        # 2b. Approved-lesson fail-closed & rejected-chunk filtering
        filter_state = _load_approved_filter_state(
            self.session,
            tenant_id=context.tenant_id,
            candidates=list(candidates_by_id.values()),
        )
        if any(c.filter_approved_graph for c in candidates_by_id.values()):
            candidates_by_id = {
                cid: c
                for cid, c in candidates_by_id.items()
                if not c.filter_approved_graph
                or not _is_chunk_rejected(
                    filter_state,
                    ce_id=c.concept_extraction_id,
                    chunk_id=c.chunk_id,
                    evidence_ids=c.evidence_ids,
                )
            }
            lexical_order = [cid for cid in lexical_order if cid in candidates_by_id]
            semantic_order = [cid for cid in semantic_order if cid in candidates_by_id]

        # 3. Reciprocal Rank Fusion & Deterministic Deduplication
        rrf_k = 60.0
        for cand in candidates_by_id.values():
            score = 0.0
            if cand.lexical_rank is not None:
                score += 1.0 / (rrf_k + cand.lexical_rank)
            if cand.semantic_rank is not None:
                score += 1.0 / (rrf_k + cand.semantic_rank)
            cand.rrf_score = score

        if effective_mode == "lexical" or (
            effective_mode == "hybrid_graph" and self.embedder is None
        ):
            ranked = sorted(
                candidates_by_id.values(),
                key=lambda c: (-c.lexical_score, str(c.chunk_record_id)),
            )
        elif effective_mode == "semantic":
            ranked = sorted(
                candidates_by_id.values(),
                key=lambda c: (-c.semantic_score, str(c.chunk_record_id)),
            )
        else:
            ranked = sorted(
                candidates_by_id.values(),
                key=lambda c: (
                    -c.rrf_score,
                    -c.semantic_score,
                    -c.lexical_score,
                    str(c.chunk_record_id),
                ),
            )

        selected: list[_CandidateRow] = []
        seen_hashes: set[str] = set()
        duplicate_removed = 0
        for cand in ranked:
            if cand.content_sha256 in seen_hashes:
                duplicate_removed += 1
                continue
            seen_hashes.add(cand.content_sha256)
            selected.append(cand)
            if len(selected) >= req.top_k:
                break

        # 4. Batched Parent-Child Section Context Retrieval (zero N+1 round trips)
        parent_context_map = self._load_parent_child_context(
            context.tenant_id, selected, filter_state=filter_state
        )

        # 5. Bounded Graph Enrichment (mode == 'hybrid_graph')
        graph_concepts: tuple[GraphEnrichmentConcept, ...] = ()
        hops_traversed = 0
        cycles_avoided = 0
        if effective_mode == "hybrid_graph" and selected:
            graph_concepts, hops_traversed, cycles_avoided = _enrich_with_concept_graph(
                self.session,
                tenant_id=context.tenant_id,
                query=req.query,
                selected=selected,
                max_hops=req.max_graph_hops,
                preloaded_graphs=filter_state.graphs,
            )

        # 6. Token-Budget-Aware Context Construction
        final_chunks, context_text, context_tokens = self._build_bounded_context(
            selected=selected,
            parent_context_map=parent_context_map,
            graph_concepts=graph_concepts,
            max_context_tokens=req.max_context_tokens,
        )

        retrieval_ms = round((time.monotonic_ns() - t0) / 1_000_000.0, 3)
        return KnowledgeRetrievalResult(
            mode=effective_mode,
            query=req.query,
            embedding_model=self.embedder.model_id if self.embedder is not None else "none",
            embedding_model_version=(
                self.embedder.model_version if self.embedder is not None else "lexical_only"
            ),
            chunks=final_chunks,
            graph_concepts=graph_concepts,
            context_text=context_text,
            context_tokens=context_tokens,
            duplicate_chunks_removed=duplicate_removed,
            cycles_avoided=cycles_avoided,
            hops_traversed=hops_traversed,
            lexical_candidates=len(lexical_order),
            semantic_candidates=len(semantic_order),
            retrieval_ms=retrieval_ms,
        )

    def _load_parent_child_context(
        self,
        tenant_id: UUID,
        selected: Sequence[_CandidateRow],
        *,
        filter_state: _ApprovedFilterState = _EMPTY_FILTER_STATE,
    ) -> dict[UUID, str]:
        if not selected:
            return {}
        pairs = list(
            dict.fromkeys((c.derived_extraction_id, c.section_id) for c in selected)
        )
        derived_ids = list({p[0] for p in pairs})
        section_ids = list({p[1] for p in pairs})
        rows = self.session.execute(
            text(
                "SELECT derived_extraction_id, section_id, chunk_id, chunk_index, "
                "       heading_title, rendered_text, content_sha256, evidence_ids "
                "FROM knowledge_index_chunks "
                "WHERE tenant_id = :t AND is_active IS TRUE "
                "  AND derived_extraction_id = ANY(:dids) "
                "  AND section_id = ANY(:sids) "
                "ORDER BY derived_extraction_id, section_id, chunk_index ASC"
            ),
            {"t": tenant_id, "dids": derived_ids, "sids": section_ids},
        ).all()

        by_sec: dict[tuple[UUID, str], list[Any]] = {}
        for r in rows:
            by_sec.setdefault((r.derived_extraction_id, r.section_id), []).append(r)

        selected_hashes = {c.content_sha256 for c in selected}
        out: dict[UUID, str] = {}
        for cand in selected:
            siblings = by_sec.get((cand.derived_extraction_id, cand.section_id), [])
            breadcrumb = " > ".join(cand.section_path) if cand.section_path else (
                cand.heading_title or cand.section_id
            )
            adj_snippets: list[str] = []
            for sib in siblings:
                if (
                    cand.filter_approved_graph
                    and _is_chunk_rejected(
                        filter_state,
                        ce_id=cand.concept_extraction_id,
                        chunk_id=str(sib.chunk_id),
                        evidence_ids=tuple(sib.evidence_ids or ()),
                    )
                ):
                    continue
                if (
                    sib.chunk_id != cand.chunk_id
                    and abs(int(sib.chunk_index) - cand.chunk_index) <= 1
                    and sib.content_sha256 not in selected_hashes
                ):
                    adj_snippets.append(str(sib.rendered_text)[:280])
            if adj_snippets:
                out[cand.chunk_record_id] = (
                    f"Section [{breadcrumb}]: " + " | ".join(adj_snippets[:2])
                )
            elif breadcrumb:
                out[cand.chunk_record_id] = f"Section [{breadcrumb}]"
        return out

    @staticmethod
    def _build_bounded_context(
        *,
        selected: Sequence[_CandidateRow],
        parent_context_map: dict[UUID, str],
        graph_concepts: Sequence[GraphEnrichmentConcept],
        max_context_tokens: int,
    ) -> tuple[tuple[RetrievedEvidenceChunk, ...], str, int]:
        blocks: list[str] = []
        used_tokens = 0
        final_chunks: list[RetrievedEvidenceChunk] = []

        for cand in selected:
            parent_ctx = parent_context_map.get(cand.chunk_record_id)
            ev_tag = ",".join(cand.evidence_ids[:4])
            header = f"[Chunk {cand.chunk_id} | p.{cand.page_start}-{cand.page_end}"
            if ev_tag:
                header += f" | ev:{ev_tag}"
            header += "]"
            body = cand.rendered_text
            if parent_ctx and estimate_tokens(parent_ctx) <= 90:
                piece = f"{header}\n{parent_ctx}\n{body}"
            else:
                piece = f"{header}\n{body}"
            piece_tokens = estimate_tokens(piece)
            if final_chunks and used_tokens + piece_tokens > max_context_tokens:
                continue
            if not final_chunks and piece_tokens > max_context_tokens:
                max_chars = max(128, max_context_tokens * 4)
                piece = piece[:max_chars]
                piece_tokens = estimate_tokens(piece)
            blocks.append(piece)
            used_tokens += piece_tokens
            final_chunks.append(
                RetrievedEvidenceChunk(
                    chunk_record_id=cand.chunk_record_id,
                    source_id=cand.source_id,
                    version_id=cand.version_id,
                    derived_extraction_id=cand.derived_extraction_id,
                    concept_extraction_id=cand.concept_extraction_id,
                    index_version=cand.index_version,
                    chunk_id=cand.chunk_id,
                    chunk_index=cand.chunk_index,
                    section_id=cand.section_id,
                    section_path=cand.section_path,
                    heading_title=cand.heading_title,
                    page_start=cand.page_start,
                    page_end=cand.page_end,
                    evidence_ids=cand.evidence_ids,
                    origin_kind=cand.origin_kind,
                    visibility=cand.visibility,
                    rendered_text=cand.rendered_text,
                    parent_context=parent_ctx,
                    lexical_score=round(cand.lexical_score, 6),
                    semantic_score=round(cand.semantic_score, 6),
                    rrf_score=round(cand.rrf_score, 6),
                    estimated_tokens=cand.estimated_tokens,
                )
            )

        if graph_concepts and used_tokens < max_context_tokens:
            graph_lines: list[str] = ["[Concept Graph Context]"]
            for gc in graph_concepts:
                line = f"- {gc.name} ({gc.grounding}): {gc.canonical_summary}"
                if gc.definitions:
                    line += f" | Def: {gc.definitions[0]}"
                if gc.prerequisites:
                    line += f" | Prereqs: {', '.join(gc.prerequisites)}"
                if gc.relation_paths:
                    line += f" | Relations: {'; '.join(gc.relation_paths[:2])}"
                line_tok = estimate_tokens(line)
                if used_tokens + estimate_tokens("\n".join(graph_lines)) + line_tok > max_context_tokens:
                    break
                graph_lines.append(line)
            if len(graph_lines) > 1:
                graph_block = "\n".join(graph_lines)
                blocks.append(graph_block)
                used_tokens += estimate_tokens(graph_block)

        context_text = "\n\n".join(blocks)
        return tuple(final_chunks), context_text, estimate_tokens(context_text)
