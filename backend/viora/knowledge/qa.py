"""Grounded academic Q&A over authorized retrieval with citation and injection guards."""

from __future__ import annotations

import json
import re
import time
from collections import Counter
from uuid import UUID

from ..ai.public import (
    AIGateway,
    AIRequestConfig,
    AIResourceLimitError,
    AIResourceLimits,
    clamp_resource_limits,
)
from ..contracts import TenantContext
from .prompts import build_grounded_qa_messages, sanitize_untrusted_text
from .schemas import (
    QA_PROMPT_VERSION,
    QA_SCHEMA_VERSION,
    GroundedQAClaim,
    KnowledgeQARequest,
    KnowledgeQAResult,
    KnowledgeRetrievalResult,
    QAAbstention,
    QACitation,
    QAClarification,
    QAGeneralKnowledge,
    QAStatus,
    QAValidationReport,
    RawQAAnswer,
    RetrievedEvidenceChunk,
)

_WHITESPACE_RE = re.compile(r"\s+")


def _compact_qa_schema_size_chars() -> int:
    def _strip(node: object, in_props: bool = False) -> object:
        if isinstance(node, dict):
            return {
                k: _strip(v, k == "properties")
                for k, v in node.items()
                if in_props or k != "title"
            }
        if isinstance(node, list):
            return [_strip(x) for x in node]
        return node

    return len(
        json.dumps(
            _strip(RawQAAnswer.model_json_schema()),
            separators=(",", ":"),
        )
    )


_QA_SCHEMA_OVERHEAD_CHARS = _compact_qa_schema_size_chars()


def _verbatim_quote_in_text(quote: str, text: str) -> bool:
    clean_q = _WHITESPACE_RE.sub(" ", quote).strip()
    clean_t = _WHITESPACE_RE.sub(" ", text).strip()
    if not clean_q or not clean_t:
        return False
    prefix = r"(?<![A-Za-z0-9_-])" if (clean_q[0].isalnum() or clean_q[0] in "_-") else ""
    suffix = r"(?![A-Za-z0-9_-])" if (clean_q[-1].isalnum() or clean_q[-1] in "_-") else ""
    return re.search(f"{prefix}{re.escape(clean_q)}{suffix}", clean_t) is not None


class GroundedKnowledgeQA:
    """Produces bounded, citation-verified educational Q&A from authorized retrieval."""

    def __init__(
        self,
        gateway: AIGateway,
        *,
        request_config: AIRequestConfig | None = None,
        max_inference_requests: int = 1,
    ) -> None:
        if max_inference_requests != 1:
            raise ValueError("GroundedKnowledgeQA permits at most 1 inference request")
        self.gateway = gateway
        self.max_inference_requests = 1
        self.request_config = request_config or AIRequestConfig(
            max_tokens=1200,
            temperature=0.0,
            timeout_seconds=25.0,
            total_deadline_seconds=45.0,
            max_retries=1,
        )

    def answer(
        self,
        *,
        context: TenantContext,
        request: KnowledgeQARequest,
        retrieval: KnowledgeRetrievalResult,
    ) -> KnowledgeQAResult:
        t0 = time.monotonic_ns()
        if not retrieval.chunks and not request.allow_general_knowledge:
            _, q_signals = sanitize_untrusted_text(request.question)
            total_ms = round((time.monotonic_ns() - t0) / 1_000_000.0, 3)
            reason = "No authorized source passages matched the question."
            return KnowledgeQAResult(
                schema_version=QA_SCHEMA_VERSION,
                status="insufficient_evidence",
                question=request.question,
                answer="Insufficient authorized source evidence was found to answer this question.",
                abstention=QAAbstention(
                    reason=reason,
                    missing_evidence_topics=(request.question[:160],),
                ),
                validation=QAValidationReport(
                    schema_version=QA_SCHEMA_VERSION,
                    prompt_version=QA_PROMPT_VERSION,
                    retrieval_mode=retrieval.mode,
                    authorized_chunks_count=0,
                    authorized_graph_concepts_count=len(retrieval.graph_concepts),
                    context_tokens=0,
                    valid_citations_count=0,
                    rejected_citations_count=0,
                    dropped_ungrounded_claims_count=0,
                    general_knowledge_suppressed=False,
                    prompt_injection_signals_detected=q_signals,
                    inference_requests=0,
                    retrieval_ms=retrieval.retrieval_ms,
                    inference_ms=0.0,
                    validation_ms=total_ms,
                    total_ms=round(retrieval.retrieval_ms + total_ms, 3),
                    provider="none",
                    model="none",
                    input_tokens=0,
                    output_tokens=0,
                    total_tokens=0,
                    notes=("Abstained without inference because zero authorized chunks were retrieved.",),
                ),
            )

        gw_limits = getattr(self.gateway, "hard_limits", None)
        hard_limits = gw_limits if isinstance(gw_limits, AIResourceLimits) else AIResourceLimits()
        effective_limits = clamp_resource_limits(self.request_config.resource_limits, hard_limits)
        if (
            self.request_config.max_tokens is not None
            and self.request_config.max_tokens > effective_limits.max_output_tokens
        ):
            raise AIResourceLimitError(
                f"Requested max_tokens {self.request_config.max_tokens} exceeds limit {effective_limits.max_output_tokens}"
            )

        messages, included_chunks, context_tokens, injection_signals = build_grounded_qa_messages(
            question=request.question,
            chunks=retrieval.chunks,
            graph_concepts=retrieval.graph_concepts,
            max_context_tokens=request.max_context_tokens,
            max_input_chars=effective_limits.max_input_chars,
            schema_overhead_chars=_QA_SCHEMA_OVERHEAD_CHARS,
            allow_general_knowledge=request.allow_general_knowledge,
        )

        t_inf0 = time.monotonic_ns()
        res = self.gateway.complete_structured(
            context,
            messages,
            RawQAAnswer,
            config=self.request_config,
        )
        inference_ms = round((time.monotonic_ns() - t_inf0) / 1_000_000.0, 3)

        t_val0 = time.monotonic_ns()
        validated = self._validate_output(
            request=request,
            retrieval=retrieval,
            included_chunks=included_chunks,
            context_tokens=context_tokens,
            injection_signals=injection_signals,
            raw=res.parsed,
            provider=res.metadata.provider,
            model=res.model,
            input_tokens=res.usage.input_tokens,
            output_tokens=res.usage.output_tokens,
            total_tokens=res.usage.total_tokens,
            inference_ms=inference_ms,
            t_val0=t_val0,
            t0=t0,
        )
        return validated

    @staticmethod
    def _validate_output(
        *,
        request: KnowledgeQARequest,
        retrieval: KnowledgeRetrievalResult,
        included_chunks: tuple[tuple[str, RetrievedEvidenceChunk], ...],
        context_tokens: int,
        injection_signals: int,
        raw: RawQAAnswer,
        provider: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
        total_tokens: int,
        inference_ms: float,
        t_val0: int,
        t0: int,
    ) -> KnowledgeQAResult:
        by_cite_id: dict[str, tuple[str, RetrievedEvidenceChunk]] = {
            cid: (cid, ch) for cid, ch in included_chunks
        }
        by_upper_id = {cid.upper(): val for cid, val in by_cite_id.items()}
        by_record_id = {str(ch.chunk_record_id): (cid, ch) for cid, ch in included_chunks}
        chunk_id_counts = Counter(ch.chunk_id for _, ch in included_chunks)
        by_unique_chunk_id = {
            ch.chunk_id: (cid, ch)
            for cid, ch in included_chunks
            if chunk_id_counts[ch.chunk_id] == 1
        }

        def _resolve_chunk(ref: str) -> tuple[str, RetrievedEvidenceChunk] | None:
            key = ref.strip()
            if not key:
                return None
            return (
                by_cite_id.get(key)
                or by_upper_id.get(key.upper())
                or by_record_id.get(key)
                or by_unique_chunk_id.get(key)
            )

        notes: list[str] = []
        if injection_signals > 0:
            notes.append(
                f"Detected and isolated {injection_signals} prompt-injection signal(s) inside untrusted inputs."
            )

        rejected_citations = 0
        dropped_claims = 0
        validated_claims: list[GroundedQAClaim] = []
        all_citations_map: dict[tuple[UUID, str | None], QACitation] = {}

        for raw_claim in raw.claims:
            stmt = raw_claim.statement.strip()
            if not stmt:
                continue
            claim_cits: list[QACitation] = []
            seen_claim_cits: set[tuple[UUID, str | None]] = set()
            for raw_cit in raw_claim.citations:
                resolved = _resolve_chunk(raw_cit.chunk_ref)
                if resolved is None:
                    rejected_citations += 1
                    continue
                cite_id, chunk = resolved
                ev_id = raw_cit.evidence_id.strip() if raw_cit.evidence_id and raw_cit.evidence_id.strip() else None
                if ev_id is not None and ev_id not in chunk.evidence_ids:
                    rejected_citations += 1
                    continue
                cit_key = (chunk.chunk_record_id, ev_id)
                if cit_key in seen_claim_cits:
                    continue
                quote = stmt if raw_cit.quote is None else raw_cit.quote
                excerpt = _WHITESPACE_RE.sub(" ", quote).strip()
                if (
                    not _verbatim_quote_in_text(excerpt, chunk.rendered_text)
                    or not _verbatim_quote_in_text(stmt, excerpt[:500])
                ):
                    rejected_citations += 1
                    continue
                seen_claim_cits.add(cit_key)

                cit_obj = QACitation(
                    citation_ref=cite_id,
                    chunk_record_id=chunk.chunk_record_id,
                    source_id=chunk.source_id,
                    version_id=chunk.version_id,
                    derived_extraction_id=chunk.derived_extraction_id,
                    concept_extraction_id=chunk.concept_extraction_id,
                    chunk_id=chunk.chunk_id,
                    section_id=chunk.section_id,
                    section_path=chunk.section_path,
                    heading_title=chunk.heading_title,
                    page_start=chunk.page_start,
                    page_end=chunk.page_end,
                    evidence_id=ev_id,
                    origin_kind=chunk.origin_kind,
                    excerpt=excerpt[:500],
                    quote_verified=raw_cit.quote is not None,
                )
                claim_cits.append(cit_obj)
                all_citations_map.setdefault(cit_key, cit_obj)

            if not claim_cits:
                dropped_claims += 1
                continue

            validated_claims.append(
                GroundedQAClaim(
                    claim_id=f"claim-{len(validated_claims) + 1}",
                    statement=stmt,
                    grounding=raw_claim.grounding,
                    citations=tuple(claim_cits),
                )
            )

        if rejected_citations > 0:
            notes.append(
                f"Rejected {rejected_citations} fabricated or out-of-context citation(s)."
            )
        if dropped_claims > 0:
            notes.append(
                f"Dropped {dropped_claims} claim(s) lacking verified source citations."
            )

        raw_gk = (raw.general_knowledge_answer or "").strip()
        general_knowledge: QAGeneralKnowledge | None = None
        gk_suppressed = False
        if request.allow_general_knowledge and raw_gk:
            general_knowledge = QAGeneralKnowledge(content=raw_gk[:2000])
        elif raw_gk and not request.allow_general_knowledge:
            gk_suppressed = True
            notes.append(
                "Suppressed general-knowledge output because allow_general_knowledge is false."
            )

        ambiguities = tuple(a.strip()[:240] for a in raw.ambiguities if a.strip())
        clarifying_questions = tuple(
            q.strip()[:300] for q in raw.clarifying_questions if q.strip()
        )
        missing_topics = tuple(
            m.strip()[:200] for m in raw.missing_evidence_topics if m.strip()
        )
        raw_answer = raw.answer.strip()
        had_rejections = rejected_citations > 0 or dropped_claims > 0

        clarification: QAClarification | None = None
        abstention: QAAbstention | None = None
        status: QAStatus = raw.status

        if status == "answered":
            if not validated_claims:
                status = "insufficient_evidence"
                reason = "No claims could be verified against authorized source citations."
                answer = "Insufficient verified source evidence to answer this question from the authorized course materials."
                abstention = QAAbstention(
                    reason=reason,
                    missing_evidence_topics=missing_topics or (request.question[:160],),
                )
                notes.append("Downgraded ungrounded answer to insufficient_evidence.")
            else:
                answer = " ".join(c.statement for c in validated_claims)[:2000]
        elif status == "clarification_needed":
            if not clarifying_questions:
                if ambiguities:
                    clarifying_questions = (f"Could you clarify: {ambiguities[0]}?",)
                elif raw_answer:
                    clarifying_questions = (
                        "Could you clarify the scope, constraints, or assumptions of your question?",
                    )
                else:
                    status = "insufficient_evidence"
                    reason = "Question is underspecified and no clarifying details were produced."
                    answer = reason
                    abstention = QAAbstention(
                        reason=reason,
                        missing_evidence_topics=missing_topics or (request.question[:160],),
                    )
            if status == "clarification_needed":
                reason = (
                    raw_answer[:600]
                    if (raw_answer and not had_rejections)
                    else "The question is ambiguous or missing required context."
                )
                clarification = QAClarification(
                    reason=reason,
                    ambiguities=ambiguities,
                    clarifying_questions=clarifying_questions,
                )
                answer = reason
        else:
            status = "insufficient_evidence"
            reason = (
                (raw.abstention_reason or "").strip()
                or (raw_answer if not had_rejections else "")
                or "Authorized course sources do not contain sufficient evidence to answer this question."
            )[:600]
            abstention = QAAbstention(
                reason=reason,
                missing_evidence_topics=missing_topics or (request.question[:160],),
            )
            if clarifying_questions:
                clarification = QAClarification(
                    reason=reason,
                    ambiguities=ambiguities,
                    clarifying_questions=clarifying_questions,
                )
            answer = reason

        validation_ms = round((time.monotonic_ns() - t_val0) / 1_000_000.0, 3)
        qa_ms = round((time.monotonic_ns() - t0) / 1_000_000.0, 3)
        return KnowledgeQAResult(
            schema_version=QA_SCHEMA_VERSION,
            status=status,
            question=request.question,
            answer=answer,
            source_grounded_claims=tuple(validated_claims),
            citations=tuple(all_citations_map.values()),
            clarification=clarification,
            abstention=abstention,
            general_knowledge=general_knowledge,
            validation=QAValidationReport(
                schema_version=QA_SCHEMA_VERSION,
                prompt_version=QA_PROMPT_VERSION,
                retrieval_mode=retrieval.mode,
                authorized_chunks_count=len(included_chunks),
                authorized_graph_concepts_count=len(retrieval.graph_concepts),
                context_tokens=context_tokens,
                valid_citations_count=len(all_citations_map),
                rejected_citations_count=rejected_citations,
                dropped_ungrounded_claims_count=dropped_claims,
                general_knowledge_suppressed=gk_suppressed,
                prompt_injection_signals_detected=injection_signals,
                inference_requests=1,
                retrieval_ms=retrieval.retrieval_ms,
                inference_ms=inference_ms,
                validation_ms=validation_ms,
                total_ms=round(retrieval.retrieval_ms + qa_ms, 3),
                provider=provider,
                model=model,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                total_tokens=total_tokens,
                notes=tuple(dict.fromkeys(notes)),
            ),
        )
