"""AI-assisted educational concept extractor with strict evidence & grounding verification."""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime

from ..ai.public import AIGateway, AIRequestConfig, AIResourceLimitError
from ..contracts import TenantContext
from ..sources.public import DerivedExtractionRecord, EvidenceReference
from .chunking import chunk_extracted_document
from .prompts import build_concept_extraction_messages
from .schemas import (
    CONCEPT_SCHEMA_VERSION,
    PROMPT_VERSION,
    AdaptiveChunkPlan,
    ChunkingConfig,
    ChunkingMode,
    ConceptCandidate,
    ConceptDefinition,
    ConceptEvidenceBinding,
    ConceptExample,
    ConceptRelationshipCandidate,
    ExtractedKnowledgeGraph,
    GroundingClassification,
    KnowledgeValidationReport,
    LearningObjectiveCandidate,
    PrerequisiteCandidate,
    RawConceptCandidate,
    RawConceptExtractionBatch,
    RawGroundingLabel,
)

_WHITESPACE_RE = re.compile(r"\s+")
_PUNCT_RE = re.compile(r"[^a-z0-9\s]")
_STOPWORDS = frozenset(
    {
        "the", "and", "for", "with", "that", "this", "from", "into", "when", "where",
        "are", "was", "were", "has", "have", "had", "not", "but", "can", "will",
        "its", "their", "about", "such", "than", "then", "also", "over", "under",
        "between", "equals", "equal", "rate", "change", "object", "system",
        "concept", "concepts", "method", "methods", "function", "functions",
        "property", "properties", "example", "examples", "step", "steps",
        "value", "values", "element", "elements", "process", "using", "used",
        "called", "defined", "represents", "describes",
    }
)
_NEGATION_WORDS = frozenset({"not", "never", "no", "without", "cannot", "cant", "neither", "nor"})
_ANTONYM_PAIRS: tuple[tuple[str, str], ...] = (
    ("sorted", "unsorted"),
    ("fifo", "lifo"),
    ("constant", "linear"),
    ("recursive", "iterative"),
    ("singly", "doubly"),
    ("contiguous", "linked"),
    ("static", "dynamic"),
    ("directed", "undirected"),
)


def _norm_words(text: str) -> set[str]:
    return set(_PUNCT_RE.sub(" ", text.lower()).split())


def _domain_tokens(text: str) -> set[str]:
    return {
        tok
        for tok in _norm_words(text)
        if (len(tok) >= 3 and tok not in _STOPWORDS) or (len(tok) == 2 and tok.isdigit())
    }


def _has_polarity_conflict(claim_words: set[str], support_words: set[str]) -> bool:
    """Detect explicit negation or mutually exclusive qualifier flips against evidence."""
    claim_neg = bool(claim_words & _NEGATION_WORDS)
    sup_neg = bool(support_words & _NEGATION_WORDS)
    if claim_neg != sup_neg:
        return True
    for a, b in _ANTONYM_PAIRS:
        if (a in claim_words and b in support_words and a not in support_words) or (
            b in claim_words and a in support_words and b not in support_words
        ):
            return True
    return False


def _claim_semantically_aligned(
    claim_texts: list[str],
    bindings: list[ConceptEvidenceBinding],
    chunk_tokens: set[str] | None,
    chunk_text: str | None = None,
) -> bool:
    """Deterministic lexical screening heuristic; flags low overlap or polarity conflicts, never proves entailment."""
    if not bindings:
        return False
    claim_joined = " ".join(t for t in claim_texts if t)
    claim_toks = _domain_tokens(claim_joined)
    if not claim_toks:
        return True
    claim_words = _norm_words(claim_joined)

    ev_joined = " ".join(b.excerpt for b in bindings if b.excerpt)
    ev_toks = _domain_tokens(ev_joined)
    min_req = 2 if len(claim_toks) >= 4 else 1

    ev_overlap = claim_toks & ev_toks
    if len(ev_overlap) >= min_req:
        return not _has_polarity_conflict(claim_words, _norm_words(ev_joined))
    if chunk_tokens and len(claim_toks & chunk_tokens) >= min_req:
        sup_words = _norm_words(chunk_text) if chunk_text else chunk_tokens
        return not _has_polarity_conflict(claim_words, sup_words)
    return False


def _compact_schema_size_chars() -> int:
    def _strip(node: object, in_props: bool = False) -> object:
        if isinstance(node, dict):
            return {k: _strip(v, k == "properties") for k, v in node.items() if in_props or k != "title"}
        if isinstance(node, list):
            return [_strip(x) for x in node]
        return node

    return len(
        json.dumps(
            _strip(RawConceptExtractionBatch.model_json_schema()),
            separators=(",", ":"),
        )
    )


_SCHEMA_OVERHEAD_CHARS = _compact_schema_size_chars()


def normalize_concept_name(name: str) -> str:
    lowered = _PUNCT_RE.sub(" ", name.lower())
    return _WHITESPACE_RE.sub(" ", lowered).strip()


def _map_grounding(
    raw_label: RawGroundingLabel,
    *,
    is_topic_request: bool,
    verified_ev_count: int,
    had_unverified_claim: bool,
) -> GroundingClassification:
    if is_topic_request:
        return "learning_request"
    if raw_label == "source_explicit":
        return "source_supported" if verified_ev_count > 0 else "unsupported"
    if raw_label == "source_synthesized":
        return "source_synthesized" if verified_ev_count > 0 else "inferred"
    if raw_label == "inferred":
        return "inferred"
    if had_unverified_claim and verified_ev_count == 0:
        return "unsupported"
    return "uncertain"


@dataclass(frozen=True)
class ConceptExtractionExecutionResult:
    knowledge: ExtractedKnowledgeGraph
    chunk_plan: AdaptiveChunkPlan
    provider: str
    model: str
    input_tokens: int
    output_tokens: int
    total_tokens: int
    inference_requests: int
    chunking_ms: float
    inference_ms: float
    validation_ms: float


class ConceptExtractor:
    """Structure-aware educational concept extraction pipeline using AIGateway."""

    def __init__(
        self,
        gateway: AIGateway,
        *,
        chunk_config: ChunkingConfig | None = None,
        request_config: AIRequestConfig | None = None,
        max_inference_requests: int = 8,
    ) -> None:
        self.gateway = gateway
        self.chunk_config = chunk_config or ChunkingConfig()
        self.max_inference_requests = max(1, max_inference_requests)
        self.request_config = request_config or AIRequestConfig(
            max_tokens=1400,
            temperature=0.0,
            timeout_seconds=20.0,
            total_deadline_seconds=45.0,
            max_retries=1,
        )

    def extract_concepts(
        self,
        *,
        context: TenantContext,
        derived: DerivedExtractionRecord,
        force_mode: ChunkingMode | None = None,
        cancel_check: Callable[[], bool] | None = None,
    ) -> ConceptExtractionExecutionResult:
        doc = derived.document
        chunk_plan = chunk_extracted_document(
            doc, config=self.chunk_config, force_mode=force_mode
        )
        if len(chunk_plan.chunks) > self.max_inference_requests:
            raise AIResourceLimitError(
                f"Chunk count {len(chunk_plan.chunks)} exceeds max_inference_requests={self.max_inference_requests}"
            )
        evidence_by_id: dict[str, EvidenceReference] = {
            ev.evidence_id: ev for ev in doc.evidence_references
        }
        is_topic = doc.input_format == "topic_description"
        schema_overhead_chars = _SCHEMA_OVERHEAD_CHARS
        max_total_chars = self.request_config.resource_limits.max_input_chars

        t_inf0 = time.monotonic_ns()
        raw_batches: list[tuple[str, RawConceptExtractionBatch]] = []
        known_names: list[str] = []
        known_names_seen: set[str] = set()
        provider_name = "unknown"
        model_name = "unknown"
        in_tok = 0
        out_tok = 0
        tot_tok = 0

        for chunk in chunk_plan.chunks:
            if cancel_check is not None and cancel_check():
                raise InterruptedError("Concept extraction cancelled cooperatively")
            messages = build_concept_extraction_messages(
                chunk,
                document_title=doc.title,
                input_format=doc.input_format,
                known_concept_names=tuple(known_names),
            )
            total_msg_chars = sum(len(m.content) for m in messages) + schema_overhead_chars
            if total_msg_chars > max_total_chars:
                raise AIResourceLimitError(
                    f"Prompt + schema size {total_msg_chars} chars exceeds limit {max_total_chars}"
                )
            res = self.gateway.complete_structured(
                context,
                messages,
                RawConceptExtractionBatch,
                config=self.request_config,
            )
            provider_name = res.metadata.provider
            model_name = res.model
            in_tok += res.usage.input_tokens
            out_tok += res.usage.output_tokens
            tot_tok += res.usage.total_tokens
            raw_batches.append((chunk.chunk_id, res.parsed))
            for c in res.parsed.concepts:
                if c.name not in known_names_seen:
                    known_names_seen.add(c.name)
                    known_names.append(c.name)

        inference_ms = round((time.monotonic_ns() - t_inf0) / 1_000_000.0, 3)

        t_val0 = time.monotonic_ns()
        knowledge = self._consolidate_and_validate(
            derived=derived,
            chunk_plan=chunk_plan,
            evidence_by_id=evidence_by_id,
            is_topic=is_topic,
            raw_batches=raw_batches,
        )
        validation_ms = round((time.monotonic_ns() - t_val0) / 1_000_000.0, 3)

        return ConceptExtractionExecutionResult(
            knowledge=knowledge,
            chunk_plan=chunk_plan,
            provider=provider_name,
            model=model_name,
            input_tokens=in_tok,
            output_tokens=out_tok,
            total_tokens=tot_tok,
            inference_requests=len(raw_batches),
            chunking_ms=chunk_plan.chunking_ms,
            inference_ms=inference_ms,
            validation_ms=validation_ms,
        )

    def _consolidate_and_validate(
        self,
        *,
        derived: DerivedExtractionRecord,
        chunk_plan: AdaptiveChunkPlan,
        evidence_by_id: dict[str, EvidenceReference],
        is_topic: bool,
        raw_batches: list[tuple[str, RawConceptExtractionBatch]],
    ) -> ExtractedKnowledgeGraph:
        doc = derived.document
        rejected_refs = 0
        semantic_mismatch_count = 0
        duplicate_merges = 0
        ambiguous_retained = 0
        notes: list[str] = []
        notes_seen: set[str] = set()
        chunk_text_by_id = {ch.chunk_id: ch.rendered_text for ch in chunk_plan.chunks}
        chunk_tokens_by_id: dict[str, set[str]] = {
            cid: _domain_tokens(txt) for cid, txt in chunk_text_by_id.items()
        }
        binding_cache: dict[str, ConceptEvidenceBinding] = {}

        # Keyed conservatively by (normalized_name, sense_key).
        # If ambiguity_note differs or raw candidate declares distinct sense, retain separate entry.
        buckets: list[dict[str, object]] = []
        id_by_name_sense: dict[tuple[str, str], dict[str, object]] = {}
        name_to_concept_ids: dict[str, list[str]] = {}
        obj_seq = 0

        def _add_note(note: str) -> None:
            clean = note[:200]
            if clean and clean not in notes_seen:
                notes_seen.add(clean)
                notes.append(clean)

        def _verify_ev(raw_ids: list[str]) -> tuple[list[ConceptEvidenceBinding], int]:
            bad = 0
            valid: list[ConceptEvidenceBinding] = []
            seen: set[str] = set()
            for eid in raw_ids:
                clean = eid.strip()
                if not clean or clean in seen:
                    continue
                ev = evidence_by_id.get(clean)
                if (
                    ev is None
                    or ev.source_id != doc.source_id
                    or ev.version_id != doc.version_id
                    or ev.source_sha256 != doc.source_sha256
                ):
                    bad += 1
                    continue
                seen.add(clean)
                cached_b = binding_cache.get(clean)
                if cached_b is None:
                    cached_b = ConceptEvidenceBinding(
                        evidence_id=ev.evidence_id,
                        source_id=doc.source_id,
                        version_id=doc.version_id,
                        source_sha256=doc.source_sha256,
                        extraction_id=derived.extraction_id,
                        page_number=ev.page_number,
                        section_id=ev.section_id,
                        block_id=ev.block_id,
                        verified=True,
                        excerpt=ev.excerpt[:1000],
                    )
                    binding_cache[clean] = cached_b
                valid.append(cached_b)
            return valid, bad

        for chunk_id, batch in raw_batches:
            ch_toks = chunk_tokens_by_id.get(chunk_id)
            ch_txt = chunk_text_by_id.get(chunk_id)
            for w in batch.warnings:
                clean_w = w.strip()
                if clean_w:
                    _add_note(clean_w)

            for raw_c in batch.concepts:
                norm = normalize_concept_name(raw_c.name)
                if not norm:
                    continue
                bindings, bad_cnt = _verify_ev(raw_c.evidence_ids)
                rejected_refs += bad_cnt
                verified_ids = tuple(b.evidence_id for b in bindings)
                grounding = _map_grounding(
                    raw_c.grounding,
                    is_topic_request=is_topic,
                    verified_ev_count=len(verified_ids),
                    had_unverified_claim=bad_cnt > 0 or raw_c.grounding == "source_explicit",
                )
                if (
                    not is_topic
                    and grounding in {"source_supported", "source_synthesized"}
                    and len(doc.normalized_text.strip()) >= 20
                    and not _claim_semantically_aligned(
                        [
                            raw_c.name,
                            raw_c.canonical_summary,
                            raw_c.definition or "",
                            *raw_c.examples,
                        ],
                        bindings,
                        ch_toks,
                        ch_txt,
                    )
                ):
                    grounding = "uncertain"
                    semantic_mismatch_count += 1

                sense_key = (
                    normalize_concept_name(raw_c.ambiguity_note)
                    if raw_c.ambiguity_note
                    else ""
                )
                existing_bucket = id_by_name_sense.get((norm, sense_key))

                if existing_bucket is None:
                    cid = f"con-{len(buckets) + 1}"
                    if norm in name_to_concept_ids:
                        ambiguous_retained += 1
                    bucket: dict[str, object] = {
                        "concept_id": cid,
                        "name": raw_c.name.strip(),
                        "norm": norm,
                        "sense_key": sense_key,
                        "sense_label": raw_c.ambiguity_note.strip()
                        if raw_c.ambiguity_note
                        else None,
                        "ambiguity_note": raw_c.ambiguity_note.strip()
                        if raw_c.ambiguity_note
                        else None,
                        "canonical_summary": raw_c.canonical_summary.strip(),
                        "definitions": [],
                        "examples": [],
                        "objectives": [],
                        "prerequisites": [],
                        "def_seen": set(),
                        "ex_seen": set(),
                        "obj_seen": set(),
                        "prereq_seen": set(),
                        "grounding": grounding,
                        "bindings": list(bindings),
                        "ev_seen": {b.evidence_id for b in bindings},
                        "chunk_ids": [chunk_id],
                        "chunk_seen": {chunk_id},
                    }
                    buckets.append(bucket)
                    id_by_name_sense[(norm, sense_key)] = bucket
                    name_to_concept_ids.setdefault(norm, []).append(cid)
                    target_bucket = bucket
                else:
                    duplicate_merges += 1
                    target_bucket = existing_bucket
                    chunk_ids = target_bucket["chunk_ids"]
                    chunk_seen = target_bucket["chunk_seen"]
                    assert isinstance(chunk_ids, list) and isinstance(chunk_seen, set)
                    if chunk_id not in chunk_seen:
                        chunk_seen.add(chunk_id)
                        chunk_ids.append(chunk_id)
                    existing_bindings = target_bucket["bindings"]
                    have_eids = target_bucket["ev_seen"]
                    assert isinstance(existing_bindings, list) and isinstance(have_eids, set)
                    for b in bindings:
                        if b.evidence_id not in have_eids:
                            existing_bindings.append(b)
                            have_eids.add(b.evidence_id)
                    if target_bucket["grounding"] != "source_supported" and grounding == "source_supported":
                        target_bucket["grounding"] = "source_supported"

                self._append_candidate_details(
                    target_bucket=target_bucket,
                    raw_c=raw_c,
                    grounding=grounding,
                    verified_ids=verified_ids,
                    obj_seq_start=obj_seq,
                )
                obj_seq += len(raw_c.learning_objectives)

        concepts = tuple(
            self._finalize_bucket(b, is_topic=is_topic) for b in buckets
        )

        # Relationships: resolve exact endpoints conservatively; flag ambiguous senses & mismatched evidence
        by_cid = {c.concept_id: c for c in concepts}
        concept_ev_ids = {
            c.concept_id: {b.evidence_id for b in c.evidence_bindings} for c in concepts
        }

        def _pick_endpoint(ids: list[str], rel_eids: set[str]) -> tuple[str, bool]:
            if len(ids) == 1:
                return ids[0], False
            if rel_eids:
                ev_matched = [cid for cid in ids if concept_ev_ids.get(cid, set()) & rel_eids]
                if len(ev_matched) == 1:
                    return ev_matched[0], False
            return ids[0], True

        relationships: list[ConceptRelationshipCandidate] = []
        rel_seen: set[tuple[str, str, str]] = set()
        for rel_chunk_id, batch in raw_batches:
            ch_toks = chunk_tokens_by_id.get(rel_chunk_id)
            ch_txt = chunk_text_by_id.get(rel_chunk_id)
            for rel in batch.relationships:
                s_norm = normalize_concept_name(rel.source_concept)
                t_norm = normalize_concept_name(rel.target_concept)
                s_ids = name_to_concept_ids.get(s_norm, [])
                t_ids = name_to_concept_ids.get(t_norm, [])
                if not s_ids or not t_ids:
                    continue
                bindings, bad_cnt = _verify_ev(rel.evidence_ids)
                rejected_refs += bad_cnt
                rel_eids = {b.evidence_id for b in bindings}
                s_id, s_ambig = _pick_endpoint(s_ids, rel_eids)
                t_id, t_ambig = _pick_endpoint(t_ids, rel_eids)
                if s_id == t_id:
                    continue
                rel_grounding = _map_grounding(
                    rel.grounding,
                    is_topic_request=is_topic,
                    verified_ev_count=len(bindings),
                    had_unverified_claim=bad_cnt > 0 or rel.grounding == "source_explicit",
                )
                if (
                    not is_topic
                    and rel_grounding in {"source_supported", "source_synthesized"}
                    and len(doc.normalized_text.strip()) >= 20
                    and not _claim_semantically_aligned(
                        [rel.source_concept, rel.target_concept],
                        bindings,
                        ch_toks,
                        ch_txt,
                    )
                ):
                    rel_grounding = "uncertain"
                    semantic_mismatch_count += 1
                if s_ambig or t_ambig:
                    if rel_grounding == "source_supported":
                        rel_grounding = "uncertain"
                    _add_note(
                        f"Relationship '{rel.source_concept.strip()}' -> '{rel.target_concept.strip()}' referenced an ambiguous multi-sense concept."
                    )
                dedupe_key = (s_id, rel.relation_type, t_id)
                if dedupe_key in rel_seen:
                    duplicate_merges += 1
                    continue
                rel_seen.add(dedupe_key)
                relationships.append(
                    ConceptRelationshipCandidate(
                        relationship_id=f"rel-{len(relationships) + 1}",
                        source_concept_id=s_id,
                        source_concept_name=rel.source_concept.strip(),
                        relation_type=rel.relation_type,
                        target_concept_id=t_id,
                        target_concept_name=rel.target_concept.strip(),
                        grounding=rel_grounding,
                        evidence_bindings=tuple(bindings),
                        needs_review=(
                            is_topic
                            or s_ambig
                            or t_ambig
                            or rel_grounding != "source_supported"
                            or len(bindings) == 0
                        ),
                    )
                )

        # Deterministically link concept.prerequisites strings to unambiguous extracted concepts
        for c in concepts:
            ev_map = {b.evidence_id: b for b in c.evidence_bindings}
            for prereq in c.prerequisites:
                p_ids = name_to_concept_ids.get(
                    normalize_concept_name(prereq.prerequisite_name), []
                )
                if len(p_ids) != 1 or p_ids[0] == c.concept_id:
                    continue
                dedupe_key = (p_ids[0], "prerequisite_of", c.concept_id)
                if dedupe_key in rel_seen:
                    continue
                rel_seen.add(dedupe_key)
                p_bindings = tuple(
                    ev_map[eid] for eid in prereq.evidence_ids if eid in ev_map
                )
                relationships.append(
                    ConceptRelationshipCandidate(
                        relationship_id=f"rel-{len(relationships) + 1}",
                        source_concept_id=p_ids[0],
                        source_concept_name=by_cid[p_ids[0]].name,
                        relation_type="prerequisite_of",
                        target_concept_id=c.concept_id,
                        target_concept_name=c.name,
                        grounding=prereq.grounding,
                        evidence_bindings=p_bindings,
                        needs_review=(
                            is_topic
                            or prereq.grounding != "source_supported"
                            or len(p_bindings) == 0
                        ),
                    )
                )

        if semantic_mismatch_count > 0:
            _add_note(
                f"Downgraded {semantic_mismatch_count} claim(s) whose valid evidence IDs lacked semantic term overlap; teacher verification required."
            )
        if is_topic:
            _add_note(
                "Source input_format is topic_description: concepts represent a learning request, not factual source evidence."
            )
        if rejected_refs > 0:
            _add_note(
                f"Rejected {rejected_refs} ungrounded or version-mismatched evidence reference(s)."
            )

        validation = KnowledgeValidationReport(
            schema_version=CONCEPT_SCHEMA_VERSION,
            prompt_version=PROMPT_VERSION,
            chunk_algorithm_version=chunk_plan.chunking_version,
            total_concepts=len(concepts),
            source_supported_count=sum(
                1 for c in concepts if c.grounding == "source_supported"
            ),
            source_synthesized_count=sum(
                1 for c in concepts if c.grounding == "source_synthesized"
            ),
            learning_request_count=sum(
                1 for c in concepts if c.grounding == "learning_request"
            ),
            inferred_count=sum(1 for c in concepts if c.grounding == "inferred"),
            uncertain_count=sum(1 for c in concepts if c.grounding == "uncertain"),
            unsupported_count=sum(1 for c in concepts if c.grounding == "unsupported"),
            invalid_evidence_refs_rejected=rejected_refs,
            duplicate_merges_count=duplicate_merges,
            ambiguous_senses_retained=ambiguous_retained,
            requires_teacher_review=True,
            validation_notes=tuple(notes),
        )
        return ExtractedKnowledgeGraph(
            schema_version=CONCEPT_SCHEMA_VERSION,
            source_id=doc.source_id,
            version_id=doc.version_id,
            derived_extraction_id=derived.extraction_id,
            source_sha256=doc.source_sha256,
            input_format=doc.input_format,
            title=doc.title,
            extraction_mode=chunk_plan.mode,
            published_to_students=False,
            concepts=concepts,
            relationships=tuple(relationships),
            validation=validation,
            created_at=datetime.now(UTC),
        )

    @staticmethod
    def _append_candidate_details(
        *,
        target_bucket: dict[str, object],
        raw_c: RawConceptCandidate,
        grounding: GroundingClassification,
        verified_ids: tuple[str, ...],
        obj_seq_start: int,
    ) -> None:
        def _add_unique(key: str, seen_key: str, texts: Iterable[str] | tuple[str, ...], build: Callable[[int, str], object]) -> None:
            items: list[object] = target_bucket[key]  # type: ignore[assignment]
            seen: set[str] = target_bucket[seen_key]  # type: ignore[assignment]
            for idx, raw in enumerate(texts):
                clean = raw.strip()
                if clean and clean not in seen:
                    seen.add(clean)
                    items.append(build(idx, clean))

        if raw_c.definition:
            _add_unique(
                "definitions",
                "def_seen",
                (raw_c.definition,),
                lambda _, s: ConceptDefinition(definition=s, grounding=grounding, evidence_ids=verified_ids),
            )
        _add_unique(
            "examples",
            "ex_seen",
            raw_c.examples,
            lambda _, s: ConceptExample(text=s, grounding=grounding, evidence_ids=verified_ids),
        )
        obj_grounding = "learning_request" if grounding == "learning_request" else grounding
        _add_unique(
            "objectives",
            "obj_seen",
            raw_c.learning_objectives,
            lambda i, s: LearningObjectiveCandidate(
                objective_id=f"obj-{obj_seq_start + i + 1}",
                statement=s,
                bloom_level=raw_c.bloom_level,
                grounding=obj_grounding,
                evidence_ids=verified_ids,
            ),
        )
        _add_unique(
            "prerequisites",
            "prereq_seen",
            raw_c.prerequisites,
            lambda _, s: PrerequisiteCandidate(prerequisite_name=s, grounding=grounding, evidence_ids=verified_ids),
        )

    @staticmethod
    def _finalize_bucket(
        bucket: dict[str, object], *, is_topic: bool
    ) -> ConceptCandidate:
        bindings = tuple(bucket["bindings"])  # type: ignore[arg-type]
        grounding: GroundingClassification = bucket["grounding"]  # type: ignore[assignment]
        needs_review = (
            is_topic
            or grounding in {"inferred", "uncertain", "unsupported", "learning_request"}
            or len(bindings) == 0
            or bucket["ambiguity_note"] is not None
        )
        return ConceptCandidate(
            concept_id=str(bucket["concept_id"]),
            name=str(bucket["name"]),
            normalized_name=str(bucket["norm"]),
            sense_label=bucket["sense_label"],  # type: ignore[arg-type]
            ambiguity_note=bucket["ambiguity_note"],  # type: ignore[arg-type]
            canonical_summary=str(bucket["canonical_summary"]),
            definitions=tuple(bucket["definitions"]),  # type: ignore[arg-type]
            examples=tuple(bucket["examples"]),  # type: ignore[arg-type]
            learning_objectives=tuple(bucket["objectives"]),  # type: ignore[arg-type]
            prerequisites=tuple(bucket["prerequisites"]),  # type: ignore[arg-type]
            grounding=grounding,
            evidence_bindings=bindings,
            needs_review=needs_review,
            chunk_ids=tuple(bucket["chunk_ids"]),  # type: ignore[arg-type]
        )
