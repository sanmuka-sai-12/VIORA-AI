"""Versioned Pydantic contracts for structure-aware chunking and concept extraction."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from ..contracts import ArtifactVersionRef, Contract, Digest, JobState
from ..sources.public import SourceInputFormat

CHUNK_ALGORITHM_VERSION = "struct_chunk:1.0"
PROMPT_VERSION = "concept_prompt:1.2"
CONCEPT_SCHEMA_VERSION = "1.1"

ChunkingMode = Literal["single_pass", "hierarchical"]

GroundingClassification = Literal[
    "source_supported",
    "source_synthesized",
    "learning_request",
    "inferred",
    "uncertain",
    "unsupported",
]

RawGroundingLabel = Literal[
    "source_explicit",
    "source_synthesized",
    "learning_request",
    "inferred",
    "uncertain",
]

RelationshipType = Literal[
    "prerequisite_of",
    "part_of",
    "defines",
    "contrasts_with",
    "applies_to",
    "example_of",
]

BloomLevel = Literal["remember", "understand", "apply", "analyze"]


class ChunkingConfig(Contract):
    """Conservative token-budgeting settings for model-aware structure chunking."""

    single_pass_max_tokens: int = Field(default=1600, ge=256, le=32_000)
    chunk_max_tokens: int = Field(default=1000, ge=200, le=16_000)
    chunk_min_tokens: int = Field(default=120, ge=20, le=2_000)
    overlap_tokens: int = Field(default=64, ge=0, le=512)
    chars_per_token: float = Field(default=4.0, ge=2.0, le=6.0)
    max_chunks: int = Field(default=32, ge=1, le=128)


class DocumentChunk(Contract):
    """Atomic or section-aligned structural chunk retaining exact evidence traceability."""

    chunk_id: str = Field(min_length=1, max_length=64)
    chunk_index: int = Field(ge=0)
    section_id: str = Field(min_length=1, max_length=64)
    section_path: tuple[str, ...] = ()
    heading_title: str | None = None
    page_start: int = Field(ge=1)
    page_end: int = Field(ge=1)
    estimated_tokens: int = Field(ge=1)
    char_count: int = Field(ge=1)
    overlap_prefix: str = ""
    rendered_text: str = Field(min_length=1)
    block_ids: tuple[str, ...] = ()
    table_ids: tuple[str, ...] = ()
    figure_ids: tuple[str, ...] = ()
    formula_ids: tuple[str, ...] = ()
    evidence_ids: tuple[str, ...] = ()
    contains_code: bool = False
    contains_table: bool = False
    contains_formula: bool = False
    contains_figure: bool = False


class AdaptiveChunkPlan(Contract):
    """Complete chunking plan and utilization metrics for a derived document."""

    chunking_version: str = CHUNK_ALGORITHM_VERSION
    mode: ChunkingMode
    source_id: UUID
    version_id: UUID
    source_sha256: Digest
    total_estimated_tokens: int = Field(ge=0)
    budget_tokens: int = Field(ge=1)
    coverage_ratio: float = Field(ge=0.0, le=1.0)
    duplicate_ratio: float = Field(ge=0.0, le=1.0)
    chunking_ms: float = Field(ge=0.0)
    chunks: tuple[DocumentChunk, ...] = ()
    uncovered_evidence_ids: tuple[str, ...] = ()


# ---------------------------------------------------------------------------
# Compact LLM structured-output wire schema (kept flat & low-token)
# ---------------------------------------------------------------------------


class RawConceptCandidate(BaseModel):
    model_config = ConfigDict(extra="ignore", json_schema_extra={
        "required": ["name", "canonical_summary", "grounding", "evidence_ids"],
    })

    name: str = Field(min_length=1, max_length=160)
    canonical_summary: str = Field(min_length=1, max_length=600)
    definition: str | None = Field(default=None, max_length=800)
    grounding: RawGroundingLabel = "uncertain"
    bloom_level: BloomLevel = "understand"
    ambiguity_note: str | None = Field(default=None, max_length=240)
    evidence_ids: list[str] = Field(default_factory=list, max_length=16)
    examples: list[str] = Field(default_factory=list, max_length=6)
    learning_objectives: list[str] = Field(default_factory=list, max_length=6)
    prerequisites: list[str] = Field(default_factory=list, max_length=6)


class RawRelationshipCandidate(BaseModel):
    model_config = ConfigDict(extra="ignore", json_schema_extra={
        "required": ["source_concept", "relation_type", "target_concept", "grounding", "evidence_ids"],
    })

    source_concept: str = Field(min_length=1, max_length=160)
    relation_type: RelationshipType | None = None
    target_concept: str = Field(min_length=1, max_length=160)
    grounding: RawGroundingLabel = "uncertain"
    evidence_ids: list[str] = Field(default_factory=list, max_length=16)


class RawConceptExtractionBatch(BaseModel):
    model_config = ConfigDict(extra="ignore")

    section_summary: str = Field(default="", max_length=600)
    concepts: list[RawConceptCandidate] = Field(default_factory=list, max_length=24)
    relationships: list[RawRelationshipCandidate] = Field(
        default_factory=list, max_length=32
    )
    warnings: list[str] = Field(default_factory=list, max_length=12)


# ---------------------------------------------------------------------------
# Canonical validated & persisted knowledge contracts
# ---------------------------------------------------------------------------


class ConceptEvidenceBinding(Contract):
    """Verified link tying an extracted claim back to exact document evidence."""

    evidence_id: str = Field(min_length=1, max_length=64)
    source_id: UUID
    version_id: UUID
    source_sha256: Digest
    extraction_id: UUID
    page_number: int = Field(ge=1)
    section_id: str | None = None
    block_id: str | None = None
    verified: bool = True
    excerpt: str = Field(max_length=1000)


class ConceptDefinition(Contract):
    definition: str = Field(min_length=1, max_length=1000)
    grounding: GroundingClassification
    evidence_ids: tuple[str, ...] = ()


class ConceptExample(Contract):
    text: str = Field(min_length=1, max_length=1000)
    grounding: GroundingClassification
    evidence_ids: tuple[str, ...] = ()


class LearningObjectiveCandidate(Contract):
    objective_id: str = Field(min_length=1, max_length=64)
    statement: str = Field(min_length=1, max_length=600)
    bloom_level: BloomLevel
    grounding: GroundingClassification
    evidence_ids: tuple[str, ...] = ()


class PrerequisiteCandidate(Contract):
    prerequisite_name: str = Field(min_length=1, max_length=160)
    grounding: GroundingClassification
    evidence_ids: tuple[str, ...] = ()


class ConceptCandidate(Contract):
    concept_id: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=160)
    normalized_name: str = Field(min_length=1, max_length=160)
    sense_label: str | None = None
    ambiguity_note: str | None = None
    canonical_summary: str = Field(min_length=1, max_length=800)
    definitions: tuple[ConceptDefinition, ...] = ()
    examples: tuple[ConceptExample, ...] = ()
    learning_objectives: tuple[LearningObjectiveCandidate, ...] = ()
    prerequisites: tuple[PrerequisiteCandidate, ...] = ()
    grounding: GroundingClassification
    evidence_bindings: tuple[ConceptEvidenceBinding, ...] = ()
    needs_review: bool = True
    chunk_ids: tuple[str, ...] = ()


class ConceptRelationshipCandidate(Contract):
    relationship_id: str = Field(min_length=1, max_length=64)
    source_concept_id: str = Field(min_length=1, max_length=64)
    source_concept_name: str = Field(min_length=1, max_length=160)
    relation_type: RelationshipType
    target_concept_id: str = Field(min_length=1, max_length=64)
    target_concept_name: str = Field(min_length=1, max_length=160)
    grounding: GroundingClassification
    evidence_bindings: tuple[ConceptEvidenceBinding, ...] = ()
    needs_review: bool = True


class KnowledgeValidationReport(Contract):
    """Explicit grounding and validation telemetry; schema match does not prove factual truth."""

    schema_version: str = CONCEPT_SCHEMA_VERSION
    prompt_version: str = PROMPT_VERSION
    chunk_algorithm_version: str = CHUNK_ALGORITHM_VERSION
    total_concepts: int = Field(ge=0)
    source_supported_count: int = Field(ge=0)
    source_synthesized_count: int = Field(ge=0)
    learning_request_count: int = Field(ge=0)
    inferred_count: int = Field(ge=0)
    uncertain_count: int = Field(ge=0)
    unsupported_count: int = Field(ge=0)
    invalid_evidence_refs_rejected: int = Field(ge=0)
    duplicate_merges_count: int = Field(ge=0)
    ambiguous_senses_retained: int = Field(ge=0)
    structural_validity: Literal["valid", "unknown"] = "unknown"
    required_fields_complete: bool | None = None
    semantic_support: Literal["not_verified"] = "not_verified"
    requires_teacher_review: bool = True
    validation_notes: tuple[str, ...] = ()


class ExtractedKnowledgeGraph(Contract):
    schema_version: str = CONCEPT_SCHEMA_VERSION
    source_id: UUID
    version_id: UUID
    derived_extraction_id: UUID
    source_sha256: Digest
    input_format: SourceInputFormat
    title: str
    extraction_mode: ChunkingMode
    published_to_students: bool = False
    concepts: tuple[ConceptCandidate, ...] = ()
    relationships: tuple[ConceptRelationshipCandidate, ...] = ()
    validation: KnowledgeValidationReport
    created_at: datetime


class ConceptExtractionRecord(Contract):
    """Persisted educational concept extraction artifact with provenance and cost telemetry."""

    concept_extraction_id: UUID
    tenant_id: UUID
    source_id: UUID
    version_id: UUID
    derived_extraction_id: UUID
    extraction_version: int = Field(ge=1)
    concept_version: int = Field(ge=1)
    job_id: UUID | None = None
    provider: str
    model: str
    prompt_version: str
    schema_version: str
    chunk_algorithm_version: str
    input_fingerprint: Digest
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    total_tokens: int = Field(ge=0)
    inference_requests: int = Field(ge=0)
    chunking_ms: float = Field(ge=0.0)
    inference_ms: float = Field(ge=0.0)
    validation_ms: float = Field(ge=0.0)
    cache_hit: bool = False
    published_to_students: bool = False
    knowledge: ExtractedKnowledgeGraph
    created_at: datetime


class ConceptExtractionTriggerRequest(Contract):
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=128)


class ConceptExtractionTriggerResult(Contract):
    job_id: UUID
    job_state: JobState
    derived_extraction_id: UUID
    concept_extraction_id: UUID | None = None
    idempotent_replay: bool = False


CURRICULUM_ALGORITHM_VERSION = "curriculum_dag:1.0"

ReviewDecision = Literal["approved", "changes_requested", "rejected"]
PublicationReviewStatus = Literal[
    "pending_review", "approved", "changes_requested", "rejected"
]


class PrerequisiteCycle(Contract):
    """Detected cyclic prerequisite dependency and deterministic resolution edge."""

    cycle_id: str = Field(min_length=1, max_length=64)
    concept_ids: tuple[str, ...] = ()
    concept_names: tuple[str, ...] = ()
    broken_edge_id: str | None = None
    resolution_note: str = Field(min_length=1, max_length=400)


class TeachingOrderStep(Contract):
    """One deterministically sequenced concept in prerequisite teaching order."""

    order_index: int = Field(ge=1)
    depth_level: int = Field(ge=0)
    concept_id: str = Field(min_length=1, max_length=64)
    concept_name: str = Field(min_length=1, max_length=160)
    prerequisite_concept_ids: tuple[str, ...] = ()
    blocked_by_cycle: bool = False
    grounding: GroundingClassification
    evidence_ids: tuple[str, ...] = ()
    needs_review: bool = True


class PrerequisiteAnalysis(Contract):
    """Deterministic prerequisite DAG analysis, cycle report, and teaching order."""

    algorithm_version: str = CURRICULUM_ALGORITHM_VERSION
    total_concepts: int = Field(ge=0)
    total_prerequisite_edges: int = Field(ge=0)
    has_cycles: bool = False
    cycles: tuple[PrerequisiteCycle, ...] = ()
    root_concept_ids: tuple[str, ...] = ()
    teaching_order: tuple[TeachingOrderStep, ...] = ()


class LessonOutlineStep(Contract):
    """Evidence-linked lesson step aligned to prerequisite teaching order."""

    step_index: int = Field(ge=1)
    depth_level: int = Field(ge=0)
    concept_id: str = Field(min_length=1, max_length=64)
    concept_name: str = Field(min_length=1, max_length=160)
    sense_label: str | None = None
    canonical_summary: str
    definitions: tuple[ConceptDefinition, ...] = ()
    examples: tuple[ConceptExample, ...] = ()
    learning_objectives: tuple[LearningObjectiveCandidate, ...] = ()
    prerequisite_concept_ids: tuple[str, ...] = ()
    grounding: GroundingClassification
    evidence_bindings: tuple[ConceptEvidenceBinding, ...] = ()
    needs_review: bool = True
    estimated_minutes: int = Field(ge=1, le=120)


class LessonModule(Contract):
    """Cohesive teaching module grouped by prerequisite depth."""

    module_id: str = Field(min_length=1, max_length=64)
    sequence_index: int = Field(ge=1)
    depth_level: int = Field(ge=0)
    title: str = Field(min_length=1, max_length=200)
    learning_objectives: tuple[str, ...] = ()
    steps: tuple[LessonOutlineStep, ...] = ()
    module_grounding: GroundingClassification
    evidence_ids: tuple[str, ...] = ()
    estimated_minutes: int = Field(ge=1)


class StructuredLessonOutline(Contract):
    """Versioned, evidence-grounded lesson outline awaiting teacher review."""

    outline_version: str = CURRICULUM_ALGORITHM_VERSION
    source_id: UUID
    version_id: UUID
    derived_extraction_id: UUID
    source_sha256: Digest
    input_format: SourceInputFormat
    title: str
    learning_request_only: bool = False
    all_evidence_verified: bool = False
    requires_teacher_review: bool = True
    has_prerequisite_cycles: bool = False
    prerequisite_analysis: PrerequisiteAnalysis
    modules: tuple[LessonModule, ...] = ()
    estimated_total_minutes: int = Field(ge=0)
    supported_steps_count: int = Field(ge=0)
    review_required_steps_count: int = Field(ge=0)
    notes: tuple[str, ...] = ()


class KnowledgeReviewRequest(Contract):
    concept_extraction_id: UUID
    decision: ReviewDecision
    review_notes: str | None = Field(default=None, max_length=2000)
    approved_concept_ids: tuple[str, ...] = ()
    rejected_concept_ids: tuple[str, ...] = ()
    acknowledge_learning_request: bool = False


class KnowledgePublishRequest(Contract):
    concept_extraction_id: UUID
    publish: bool = True


class KnowledgeReviewRecord(Contract):
    review_id: UUID
    tenant_id: UUID
    source_id: UUID
    version_id: UUID
    concept_extraction_id: UUID
    review_version: int = Field(ge=1)
    decision: ReviewDecision
    review_notes: str | None = None
    approved_concept_ids: tuple[str, ...] = ()
    rejected_concept_ids: tuple[str, ...] = ()
    reviewed_by_user_id: UUID
    created_at: datetime


class KnowledgePublicationState(Contract):
    tenant_id: UUID
    source_id: UUID
    version_id: UUID
    concept_extraction_id: UUID
    concept_version: int = Field(ge=1)
    source_sha256: Digest
    review_status: PublicationReviewStatus
    latest_review_id: UUID | None = None
    approved_review_id: UUID | None = None
    teacher_approved: bool = False
    published_to_students: bool = False
    published_by_user_id: UUID | None = None
    published_at: datetime | None = None
    updated_at: datetime
    learning_request_only: bool = False
    all_evidence_verified: bool = False
    outline: StructuredLessonOutline


class PublishedLessonPackage(Contract):
    """Student-accessible educational knowledge and lesson outline after approval and publication."""

    tenant_id: UUID
    source_id: UUID
    version_id: UUID
    derived_extraction_id: UUID
    concept_extraction_id: UUID
    concept_version: int = Field(ge=1)
    approved_review_id: UUID
    source_sha256: Digest
    input_format: SourceInputFormat
    title: str
    teacher_approved: bool = True
    published_to_students: bool = True
    learning_request_only: bool = False
    all_evidence_verified: bool = False
    published_at: datetime
    knowledge: ExtractedKnowledgeGraph
    outline: StructuredLessonOutline


INDEX_ALGORITHM_VERSION = "hybrid_index:1.0"
RETRIEVAL_ALGORITHM_VERSION = "hybrid_graph_rrf:1.0"
RetrievalMode = Literal["lexical", "semantic", "hybrid", "hybrid_graph"]


class KnowledgeIndexTriggerRequest(Contract):
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=128)


class KnowledgeIndexTriggerResult(Contract):
    job_id: UUID
    job_state: JobState
    derived_extraction_id: UUID
    concept_extraction_id: UUID | None = None
    index_version: int | None = None
    idempotent_replay: bool = False


class KnowledgeIndexStatus(Contract):
    tenant_id: UUID
    source_id: UUID
    version_id: UUID
    indexed: bool
    index_version: int | None = None
    derived_extraction_id: UUID | None = None
    concept_extraction_id: UUID | None = None
    chunk_count: int = Field(ge=0, default=0)
    embedding_model: str | None = None
    embedding_model_version: str | None = None
    chunk_algorithm_version: str | None = None
    updated_at: datetime | None = None


class KnowledgeRetrieveRequest(Contract):
    query: str = Field(min_length=2, max_length=1000)
    offering_id: UUID | None = None
    mode: RetrievalMode = "hybrid"
    top_k: int = Field(default=5, ge=1, le=20)
    candidate_k: int = Field(default=20, ge=1, le=50)
    max_context_tokens: int = Field(default=1800, ge=128, le=6000)
    include_student_submissions: bool = True
    published_only: bool = False
    max_graph_hops: int = Field(default=2, ge=1, le=2)


class RetrievedEvidenceChunk(Contract):
    chunk_record_id: UUID
    source_id: UUID
    version_id: UUID
    derived_extraction_id: UUID
    concept_extraction_id: UUID | None = None
    index_version: int = Field(ge=1)
    chunk_id: str
    chunk_index: int = Field(ge=0)
    section_id: str
    section_path: tuple[str, ...] = ()
    heading_title: str | None = None
    page_start: int = Field(ge=1)
    page_end: int = Field(ge=1)
    evidence_ids: tuple[str, ...] = ()
    origin_kind: str
    visibility: str
    rendered_text: str
    parent_context: str | None = None
    lexical_score: float = 0.0
    semantic_score: float = 0.0
    rrf_score: float = 0.0
    estimated_tokens: int = Field(ge=1)


class GraphEnrichmentConcept(Contract):
    concept_id: str
    name: str
    sense_label: str | None = None
    grounding: GroundingClassification
    canonical_summary: str
    definitions: tuple[str, ...] = ()
    prerequisites: tuple[str, ...] = ()
    relation_paths: tuple[str, ...] = ()
    evidence_ids: tuple[str, ...] = ()
    hop_distance: int = Field(ge=0, le=2)


class KnowledgeRetrievalResult(Contract):
    algorithm_version: str = RETRIEVAL_ALGORITHM_VERSION
    mode: RetrievalMode
    query: str
    embedding_model: str
    embedding_model_version: str
    chunks: tuple[RetrievedEvidenceChunk, ...] = ()
    graph_concepts: tuple[GraphEnrichmentConcept, ...] = ()
    context_text: str = ""
    context_tokens: int = Field(ge=0)
    duplicate_chunks_removed: int = Field(ge=0)
    cycles_avoided: int = Field(ge=0)
    hops_traversed: int = Field(ge=0)
    lexical_candidates: int = Field(ge=0)
    semantic_candidates: int = Field(ge=0)
    retrieval_ms: float = Field(ge=0.0)


QA_PROMPT_VERSION = "qa_prompt:1.1"
QA_SCHEMA_VERSION = "1.0"
QAStatus = Literal["answered", "clarification_needed", "insufficient_evidence"]
QAClaimGrounding = Literal["source_supported", "source_synthesized"]


class RawQACitation(BaseModel):
    model_config = ConfigDict(extra="ignore")

    chunk_ref: str = Field(min_length=1, max_length=64)
    evidence_id: str | None = Field(default=None, max_length=64)
    quote: str | None = Field(default=None, max_length=320)


class RawQAClaim(BaseModel):
    model_config = ConfigDict(extra="ignore")

    statement: str = Field(min_length=1, max_length=800)
    grounding: QAClaimGrounding = "source_supported"
    citations: list[RawQACitation] = Field(default_factory=list, max_length=8)


class RawQAAnswer(BaseModel):
    model_config = ConfigDict(extra="ignore")

    status: QAStatus = "insufficient_evidence"
    answer: str = Field(default="", max_length=2000)
    claims: list[RawQAClaim] = Field(default_factory=list, max_length=12)
    ambiguities: list[str] = Field(default_factory=list, max_length=6)
    clarifying_questions: list[str] = Field(default_factory=list, max_length=6)
    missing_evidence_topics: list[str] = Field(default_factory=list, max_length=6)
    abstention_reason: str | None = Field(default=None, max_length=400)
    general_knowledge_answer: str | None = Field(default=None, max_length=1500)


class KnowledgeQARequest(Contract):
    question: str = Field(min_length=2, max_length=1000)
    offering_id: UUID | None = None
    mode: RetrievalMode = "hybrid_graph"
    top_k: int = Field(default=5, ge=1, le=12)
    candidate_k: int = Field(default=20, ge=1, le=50)
    max_context_tokens: int = Field(default=1800, ge=128, le=4000)
    include_student_submissions: bool = True
    published_only: bool = False
    max_graph_hops: int = Field(default=2, ge=1, le=2)
    allow_general_knowledge: bool = False
    allow_interpretation: bool = False


class QACitation(Contract):
    citation_ref: str = Field(min_length=1, max_length=32)
    chunk_record_id: UUID
    source_id: UUID
    version_id: UUID
    derived_extraction_id: UUID
    concept_extraction_id: UUID | None = None
    chunk_id: str
    section_id: str
    section_path: tuple[str, ...] = ()
    heading_title: str | None = None
    page_start: int = Field(ge=1)
    page_end: int = Field(ge=1)
    evidence_id: str | None = None
    origin_kind: str
    excerpt: str = Field(max_length=500)
    quote_verified: bool = False


class GroundedQAClaim(Contract):
    claim_id: str = Field(min_length=1, max_length=32)
    statement: str = Field(min_length=1, max_length=1000)
    grounding: QAClaimGrounding
    citations: tuple[QACitation, ...] = Field(min_length=1)


class QAInterpretation(Contract):
    entailment_verified: Literal[False] = False
    statement: str = Field(min_length=1, max_length=1000)
    citations: tuple[QACitation, ...] = Field(min_length=1)


class QAClarification(Contract):
    reason: str = Field(min_length=1, max_length=600)
    ambiguities: tuple[str, ...] = ()
    clarifying_questions: tuple[str, ...] = Field(min_length=1)


class QAAbstention(Contract):
    reason: str = Field(min_length=1, max_length=600)
    missing_evidence_topics: tuple[str, ...] = ()


class QAGeneralKnowledge(Contract):
    is_source_grounded: Literal[False] = False
    label: str = (
        "General educational knowledge (not verified against authorized course sources)"
    )
    content: str = Field(min_length=1, max_length=2000)


class QAValidationReport(Contract):
    schema_version: str = QA_SCHEMA_VERSION
    prompt_version: str = QA_PROMPT_VERSION
    retrieval_mode: RetrievalMode
    authorized_chunks_count: int = Field(ge=0)
    authorized_graph_concepts_count: int = Field(ge=0)
    context_tokens: int = Field(ge=0)
    valid_citations_count: int = Field(ge=0)
    rejected_citations_count: int = Field(ge=0)
    dropped_ungrounded_claims_count: int = Field(ge=0)
    general_knowledge_suppressed: bool = False
    prompt_injection_signals_detected: int = Field(ge=0)
    inference_requests: int = Field(ge=0, le=1)
    retrieval_ms: float = Field(ge=0.0)
    inference_ms: float = Field(ge=0.0)
    validation_ms: float = Field(ge=0.0)
    total_ms: float = Field(ge=0.0)
    provider: str
    model: str
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    total_tokens: int = Field(ge=0)
    notes: tuple[str, ...] = ()


class KnowledgeQAResult(Contract):
    schema_version: str = QA_SCHEMA_VERSION
    status: QAStatus | Literal["interpreted"]
    question: str
    answer: str
    source_grounded_claims: tuple[GroundedQAClaim, ...] = ()
    interpretations: tuple[QAInterpretation, ...] = ()
    citations: tuple[QACitation, ...] = ()
    clarification: QAClarification | None = None
    abstention: QAAbstention | None = None
    general_knowledge: QAGeneralKnowledge | None = None
    validation: QAValidationReport


# ---------------------------------------------------------------------------
# Batch 3.5A: Structured Lesson Blueprint contracts (FR-002..FR-005)
# ---------------------------------------------------------------------------

BLUEPRINT_PROMPT_VERSION = "lesson_blueprint_prompt:1.0"
BLUEPRINT_SCHEMA_VERSION = "1.0"

InterpretationLabel = Literal[
    "verbatim_source",
    "synthesized_source",
    "pedagogical_adaptation",
    "worked_derivation",
    "syllabus_request",
    "unverified_draft",
]
SceneTemplateType = Literal[
    "vector_force",
    "process_flow",
    "parts_of_whole",
    "plot_relationship",
    "labeled_diagram",
]
SceneObjectKind = Literal[
    "rectangle",
    "circle",
    "vector_arrow",
    "node",
    "edge",
    "region",
    "axis",
    "curve",
    "point",
    "callout",
    "text_label",
    "array_cells",
]
SceneActionKind = Literal[
    "appear",
    "disappear",
    "highlight",
    "move",
    "increase_vector",
    "decrease_vector",
    "state_change",
    "reveal_step",
    "trace_path",
    "compare",
]
SceneEasing = Literal["linear", "ease_in", "ease_out", "ease_in_out"]
SceneStyleToken = Literal["primary", "secondary", "accent", "muted", "warning"]
AssessmentQuestionType = Literal["mcq", "short_answer", "concept_check"]
AssessmentDifficulty = Literal["foundational", "intermediate", "applied"]
AssessmentGradingMethod = Literal[
    "deterministic_mcq", "deterministic_exact", "formative_rubric"
]


class RawBlueprintObjective(BaseModel):
    model_config = ConfigDict(extra="ignore")

    concept_id: str = Field(min_length=1, max_length=64)
    statement: str = Field(min_length=1, max_length=600)
    bloom_level: BloomLevel = "understand"
    evidence_ids: list[str] = Field(default_factory=list, max_length=8)


class RawBlueprintExplanation(BaseModel):
    model_config = ConfigDict(extra="ignore")

    concept_id: str = Field(min_length=1, max_length=64)
    explanation: str = Field(min_length=1, max_length=1800)
    key_points: list[str] = Field(default_factory=list, max_length=8)
    formulas: list[str] = Field(default_factory=list, max_length=6)
    evidence_ids: list[str] = Field(default_factory=list, max_length=8)


class RawBlueprintWorkedExample(BaseModel):
    model_config = ConfigDict(extra="ignore")

    concept_id: str = Field(min_length=1, max_length=64)
    title: str = Field(min_length=1, max_length=200)
    problem_statement: str = Field(min_length=1, max_length=1000)
    steps: list[str] = Field(default_factory=list, max_length=10)
    final_answer: str = Field(min_length=1, max_length=600)
    evidence_ids: list[str] = Field(default_factory=list, max_length=8)


class RawSceneObject(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str = Field(min_length=1, max_length=64)
    kind: str = Field(min_length=1, max_length=64)
    label: str = Field(min_length=1, max_length=160)
    x: float = 100.0
    y: float = 100.0
    width: float = 160.0
    height: float = 80.0
    style_token: str = "primary"


class RawSceneAction(BaseModel):
    model_config = ConfigDict(extra="ignore")

    kind: str = Field(min_length=1, max_length=64)
    target_object_id: str = Field(min_length=1, max_length=64)
    anchor: str = Field(min_length=1, max_length=48)
    duration_seconds: float = 1.0
    easing: SceneEasing = "ease_in_out"
    delta_x: float = 0.0
    delta_y: float = 0.0
    scale_factor: float = 1.0
    note: str | None = Field(default=None, max_length=240)


class RawBlueprintScene(BaseModel):
    model_config = ConfigDict(extra="ignore")

    scene_id: str = Field(min_length=1, max_length=64)
    concept_ids: list[str] = Field(default_factory=list, max_length=8)
    objective_indices: list[int] = Field(default_factory=list, max_length=8)
    template: str = Field(min_length=1, max_length=64)
    visual_intent: str = Field(min_length=1, max_length=400)
    duration_seconds: float = 12.0
    timing_anchors: list[str] = Field(default_factory=list, max_length=16)
    objects: list[RawSceneObject] = Field(default_factory=list, max_length=24)
    actions: list[RawSceneAction] = Field(default_factory=list, max_length=40)
    asset_refs: list[str] = Field(default_factory=list, max_length=8)
    evidence_ids: list[str] = Field(default_factory=list, max_length=8)


class RawNarrationCue(BaseModel):
    model_config = ConfigDict(extra="ignore")

    cue_id: str = Field(min_length=1, max_length=64)
    anchor: str = Field(min_length=1, max_length=48)
    spoken_phrase: str = Field(min_length=1, max_length=240)
    offset_hint_seconds: float | None = None


class RawBlueprintNarration(BaseModel):
    model_config = ConfigDict(extra="ignore")

    scene_id: str = Field(min_length=1, max_length=64)
    concept_ids: list[str] = Field(default_factory=list, max_length=8)
    script_text: str = Field(min_length=1, max_length=2000)
    caption_text: str = Field(default="", max_length=2000)
    estimated_duration_seconds: float = 12.0
    cues: list[RawNarrationCue] = Field(default_factory=list, max_length=16)
    evidence_ids: list[str] = Field(default_factory=list, max_length=8)


class RawBlueprintAssessment(BaseModel):
    model_config = ConfigDict(extra="ignore")

    concept_id: str = Field(min_length=1, max_length=64)
    question_type: AssessmentQuestionType = "mcq"
    difficulty: AssessmentDifficulty = "foundational"
    stem: str = Field(min_length=1, max_length=800)
    options: list[str] = Field(default_factory=list, max_length=6)
    correct_option_index: int | None = None
    correct_answer: str = Field(default="", max_length=400)
    accepted_aliases: list[str] = Field(default_factory=list, max_length=6)
    explanation: str = Field(min_length=1, max_length=1000)
    evidence_ids: list[str] = Field(default_factory=list, max_length=8)


class RawLessonBlueprintBatch(BaseModel):
    model_config = ConfigDict(extra="ignore")

    lesson_summary: str = Field(default="", max_length=1600)
    objectives: list[RawBlueprintObjective] = Field(default_factory=list, max_length=24)
    explanations: list[RawBlueprintExplanation] = Field(default_factory=list, max_length=24)
    worked_examples: list[RawBlueprintWorkedExample] = Field(default_factory=list, max_length=16)
    scenes: list[RawBlueprintScene] = Field(default_factory=list, max_length=24)
    narrations: list[RawBlueprintNarration] = Field(default_factory=list, max_length=24)
    assessments: list[RawBlueprintAssessment] = Field(default_factory=list, max_length=20)
    warnings: list[str] = Field(default_factory=list, max_length=12)


class BlueprintLearningObjective(Contract):
    objective_id: str = Field(min_length=1, max_length=64)
    concept_id: str = Field(min_length=1, max_length=64)
    statement: str = Field(min_length=1, max_length=600)
    bloom_level: BloomLevel
    grounding: GroundingClassification
    interpretation_label: InterpretationLabel
    evidence_bindings: tuple[ConceptEvidenceBinding, ...] = ()
    needs_review: bool = True


class BlueprintExplanationStep(Contract):
    step_index: int = Field(ge=1)
    depth_level: int = Field(ge=0)
    concept_id: str = Field(min_length=1, max_length=64)
    concept_name: str = Field(min_length=1, max_length=160)
    prerequisite_concept_ids: tuple[str, ...] = ()
    explanation: str = Field(min_length=1, max_length=2000)
    key_points: tuple[str, ...] = ()
    formulas: tuple[str, ...] = ()
    supported_by_source: bool = False
    grounding: GroundingClassification
    interpretation_label: InterpretationLabel
    evidence_bindings: tuple[ConceptEvidenceBinding, ...] = ()
    needs_review: bool = True


class BlueprintWorkedExample(Contract):
    example_id: str = Field(min_length=1, max_length=64)
    concept_id: str = Field(min_length=1, max_length=64)
    title: str = Field(min_length=1, max_length=200)
    problem_statement: str = Field(min_length=1, max_length=1000)
    steps: tuple[str, ...] = Field(min_length=1)
    final_answer: str = Field(min_length=1, max_length=600)
    grounding: GroundingClassification
    interpretation_label: InterpretationLabel
    evidence_bindings: tuple[ConceptEvidenceBinding, ...] = ()
    needs_review: bool = True


class SceneCoordinateSpace(Contract):
    width: int = Field(default=1280, ge=320, le=3840)
    height: int = Field(default=720, ge=240, le=2160)
    unit: Literal["px"] = "px"


class SceneObjectSpec(Contract):
    id: str = Field(min_length=1, max_length=64)
    kind: SceneObjectKind
    label: str = Field(min_length=1, max_length=160)
    x: float = Field(ge=0.0, le=1280.0)
    y: float = Field(ge=0.0, le=720.0)
    width: float = Field(gt=0.0, le=1280.0)
    height: float = Field(gt=0.0, le=720.0)
    style_token: SceneStyleToken = "primary"


class SceneActionSpec(Contract):
    kind: SceneActionKind
    target_object_id: str = Field(min_length=1, max_length=64)
    anchor: str = Field(min_length=1, max_length=48)
    duration_seconds: float = Field(ge=0.1, le=30.0)
    easing: SceneEasing = "ease_in_out"
    delta_x: float = Field(default=0.0, ge=-1280.0, le=1280.0)
    delta_y: float = Field(default=0.0, ge=-720.0, le=720.0)
    scale_factor: float = Field(default=1.0, ge=0.1, le=10.0)
    note: str | None = Field(default=None, max_length=240)


class TypedVisualSceneSpec(Contract):
    scene_id: str = Field(min_length=1, max_length=64)
    order_index: int = Field(ge=1)
    concept_ids: tuple[str, ...] = Field(min_length=1)
    objective_ids: tuple[str, ...] = ()
    template: SceneTemplateType
    visual_intent: str = Field(min_length=1, max_length=400)
    coordinate_space: SceneCoordinateSpace = Field(default_factory=SceneCoordinateSpace)
    duration_seconds: float = Field(ge=1.0, le=120.0)
    timing_anchors: tuple[str, ...] = Field(min_length=1)
    objects: tuple[SceneObjectSpec, ...] = Field(min_length=1, max_length=24)
    actions: tuple[SceneActionSpec, ...] = Field(min_length=1, max_length=40)
    narration_cue_ids: tuple[str, ...] = ()
    asset_refs: tuple[str, ...] = ()
    executable_code_allowed: Literal[False] = False
    grounding: GroundingClassification
    interpretation_label: InterpretationLabel
    evidence_bindings: tuple[ConceptEvidenceBinding, ...] = ()
    needs_review: bool = True


class NarrationCueSpec(Contract):
    cue_id: str = Field(min_length=1, max_length=64)
    anchor: str = Field(min_length=1, max_length=48)
    spoken_phrase: str = Field(min_length=1, max_length=240)
    offset_hint_seconds: float | None = Field(default=None, ge=0.0, le=120.0)


class BlueprintNarrationSegment(Contract):
    segment_id: str = Field(min_length=1, max_length=64)
    order_index: int = Field(ge=1)
    scene_id: str = Field(min_length=1, max_length=64)
    concept_ids: tuple[str, ...] = ()
    script_text: str = Field(min_length=1, max_length=2000)
    caption_text: str = Field(min_length=1, max_length=2000)
    estimated_duration_seconds: float = Field(ge=1.0, le=120.0)
    cues: tuple[NarrationCueSpec, ...] = ()
    grounding: GroundingClassification
    interpretation_label: InterpretationLabel
    evidence_bindings: tuple[ConceptEvidenceBinding, ...] = ()
    needs_review: bool = True


class BlueprintAssessmentItem(Contract):
    question_id: str = Field(min_length=1, max_length=64)
    concept_id: str = Field(min_length=1, max_length=64)
    objective_ids: tuple[str, ...] = ()
    question_type: AssessmentQuestionType
    difficulty: AssessmentDifficulty
    stem: str = Field(min_length=1, max_length=800)
    options: tuple[str, ...] = ()
    correct_option_index: int | None = None
    correct_answer: str = Field(min_length=1, max_length=400)
    accepted_aliases: tuple[str, ...] = ()
    explanation: str = Field(min_length=1, max_length=1000)
    grading_method: AssessmentGradingMethod
    answer_key_verified: bool = True
    grounding: GroundingClassification
    interpretation_label: InterpretationLabel
    evidence_bindings: tuple[ConceptEvidenceBinding, ...] = ()
    needs_review: bool = True


class StudentSafeAssessmentItem(Contract):
    """Student-facing assessment item with secret answer key and aliases stripped."""

    question_id: str = Field(min_length=1, max_length=64)
    concept_id: str = Field(min_length=1, max_length=64)
    objective_ids: tuple[str, ...] = ()
    question_type: AssessmentQuestionType
    difficulty: AssessmentDifficulty
    stem: str = Field(min_length=1, max_length=800)
    options: tuple[str, ...] = ()
    grading_method: AssessmentGradingMethod
    grounding: GroundingClassification
    interpretation_label: InterpretationLabel
    evidence_bindings: tuple[ConceptEvidenceBinding, ...] = ()


class BlueprintValidationReport(Contract):
    schema_version: str = BLUEPRINT_SCHEMA_VERSION
    prompt_version: str = BLUEPRINT_PROMPT_VERSION
    outline_version: str = CURRICULUM_ALGORITHM_VERSION
    prerequisite_order_preserved: bool = True
    learning_request_only: bool = False
    all_evidence_verified: bool = False
    executable_scene_code_blocked: Literal[True] = True
    rejected_evidence_refs_count: int = Field(ge=0)
    rejected_scenes_count: int = Field(ge=0)
    rejected_scene_commands_count: int = Field(ge=0)
    rejected_assessment_items_count: int = Field(ge=0)
    requires_teacher_review: bool = True
    notes: tuple[str, ...] = ()


class LessonBlueprint(Contract):
    blueprint_schema_version: str = BLUEPRINT_SCHEMA_VERSION
    source_id: UUID
    version_id: UUID
    derived_extraction_id: UUID
    concept_extraction_id: UUID
    approved_review_id: UUID
    source_sha256: Digest
    input_format: SourceInputFormat
    title: str
    lesson_summary: str
    learning_request_only: bool = False
    prerequisite_order: tuple[str, ...] = ()
    learning_objectives: tuple[BlueprintLearningObjective, ...] = ()
    explanations: tuple[BlueprintExplanationStep, ...] = ()
    worked_examples: tuple[BlueprintWorkedExample, ...] = ()
    scenes: tuple[TypedVisualSceneSpec, ...] = ()
    narration_language: str = "en"
    tts_synthesized: Literal[False] = False
    narrations: tuple[BlueprintNarrationSegment, ...] = ()
    assessments: tuple[BlueprintAssessmentItem, ...] = ()
    validation: BlueprintValidationReport
    created_at: datetime


class LessonBlueprintRecord(Contract):
    blueprint_id: UUID
    tenant_id: UUID
    source_id: UUID
    version_id: UUID
    derived_extraction_id: UUID
    concept_extraction_id: UUID
    approved_review_id: UUID
    blueprint_version: int = Field(ge=1)
    job_id: UUID | None = None
    provider: str
    model: str
    prompt_version: str
    schema_version: str
    outline_version: str
    input_fingerprint: Digest
    source_sha256: Digest
    artifact_ref: ArtifactVersionRef
    byte_size: int = Field(ge=1)
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    total_tokens: int = Field(ge=0)
    inference_requests: int = Field(ge=0)
    generation_ms: float = Field(ge=0.0)
    validation_ms: float = Field(ge=0.0)
    blueprint: LessonBlueprint
    created_at: datetime


class LessonBlueprintSummary(Contract):
    blueprint_id: UUID
    tenant_id: UUID
    source_id: UUID
    version_id: UUID
    concept_extraction_id: UUID
    approved_review_id: UUID
    blueprint_version: int = Field(ge=1)
    job_id: UUID | None = None
    provider: str
    model: str
    artifact_ref: ArtifactVersionRef
    byte_size: int = Field(ge=1)
    created_at: datetime


class PublishedLessonBlueprint(Contract):
    """Student-accessible approved lesson blueprint with answer keys removed."""

    blueprint_id: UUID
    tenant_id: UUID
    source_id: UUID
    version_id: UUID
    concept_extraction_id: UUID
    approved_review_id: UUID
    blueprint_version: int = Field(ge=1)
    source_sha256: Digest
    title: str
    lesson_summary: str
    teacher_approved: Literal[True] = True
    published_to_students: Literal[True] = True
    learning_request_only: bool = False
    prerequisite_order: tuple[str, ...] = ()
    learning_objectives: tuple[BlueprintLearningObjective, ...] = ()
    explanations: tuple[BlueprintExplanationStep, ...] = ()
    worked_examples: tuple[BlueprintWorkedExample, ...] = ()
    scenes: tuple[TypedVisualSceneSpec, ...] = ()
    narration_language: str = "en"
    tts_synthesized: Literal[False] = False
    narrations: tuple[BlueprintNarrationSegment, ...] = ()
    assessments: tuple[StudentSafeAssessmentItem, ...] = ()
    validation: BlueprintValidationReport
    published_at: datetime


class LessonBlueprintTriggerRequest(Contract):
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=128)


class LessonBlueprintTriggerResult(Contract):
    job_id: UUID
    job_state: JobState
    concept_extraction_id: UUID
    approved_review_id: UUID
    blueprint_id: UUID | None = None
    idempotent_replay: bool = False


class LessonBlueprintApprovalRequest(Contract):
    blueprint_id: UUID
    decision: ReviewDecision
    publish: bool = False
    review_notes: str | None = Field(default=None, max_length=1500)


class LessonBlueprintApprovalState(Contract):
    tenant_id: UUID
    source_id: UUID
    version_id: UUID
    blueprint_id: UUID | None = None
    concept_extraction_id: UUID | None = None
    approved_review_id: UUID | None = None
    approval_status: PublicationReviewStatus
    teacher_approved: bool
    published_to_students: bool
    reviewed_by_user_id: UUID | None = None
    review_notes: str | None = None
    reviewed_at: datetime | None = None
    published_by_user_id: UUID | None = None
    published_at: datetime | None = None
    updated_at: datetime


LearningPromptMode = Literal["question", "explain"]
LearningToolKind = Literal["study_notes", "academic_insights"]
LearningResourceSourceScope = Literal["topic", "conversation", "materials", "combined"]
PersonalLearningAttachmentStatus = Literal["ready", "partial", "unreadable"]
PersonalLearningAttachmentKind = Literal["pdf", "image", "text"]


class PersonalLearningAttachment(Contract):
    attachment_id: UUID
    doc_sha256: Digest
    title: str = Field(min_length=1, max_length=200)
    original_filename: str | None = None
    kind: PersonalLearningAttachmentKind
    input_format: SourceInputFormat
    status: PersonalLearningAttachmentStatus
    status_message: str = Field(min_length=1, max_length=300)
    page_count: int = Field(default=1, ge=0)
    chunk_count: int = Field(default=0, ge=0)
    char_count: int = Field(default=0, ge=0)
    preview_excerpt: str | None = None
    created_at: datetime


class PersonalLearningAttachmentRef(Contract):
    attachment_id: UUID
    doc_sha256: Digest


class PersonalLearningAttachmentTextRequest(Contract):
    title: str | None = Field(default=None, max_length=200)
    content: str = Field(min_length=1, max_length=200_000)


class PersonalLearningSourceRef(Contract):
    attachment_id: UUID
    title: str
    page_start: int = Field(ge=1)
    page_end: int = Field(ge=1)
    section_title: str | None = None
    excerpt: str


class PersonalLearningTurn(Contract):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=4000)


class PersonalLearningRequest(Contract):
    prompt: str = Field(min_length=1, max_length=2000)
    mode: LearningPromptMode = "question"
    session_id: UUID | None = None
    history: tuple[PersonalLearningTurn, ...] = Field(default=(), max_length=10)
    attachments: tuple[PersonalLearningAttachmentRef, ...] = Field(
        default=(), max_length=5
    )
    attachment_ids: tuple[UUID, ...] = Field(default=(), max_length=5)


class PersonalLearningResponse(Contract):
    answer: str
    mode: LearningPromptMode
    model: str
    provider: str
    session_id: UUID | None = None
    session_title: str | None = None
    follow_up_prompts: tuple[str, ...] = ()
    sources: tuple[PersonalLearningSourceRef, ...] = ()
    usage_tokens: int = Field(default=0, ge=0)
    truncated: bool = False


class PersonalLearningSessionSummary(Contract):
    session_id: UUID
    title: str
    mode: LearningPromptMode
    message_count: int = Field(ge=0)
    last_message_preview: str | None = None
    attachment_ids: tuple[UUID, ...] = ()
    created_at: datetime
    updated_at: datetime


class PersonalLearningMessageRecord(Contract):
    message_id: UUID
    sequence_no: int = Field(ge=1)
    role: Literal["user", "assistant"]
    content: str
    mode: LearningPromptMode
    attached_titles: tuple[str, ...] = ()
    sources: tuple[PersonalLearningSourceRef, ...] = ()
    follow_up_prompts: tuple[str, ...] = ()
    truncated: bool = False
    created_at: datetime


class PersonalLearningStudyResource(Contract):
    resource_id: UUID
    session_id: UUID
    session_title: str | None = None
    tool_kind: LearningToolKind
    source_scope: LearningResourceSourceScope
    title: str
    focus_topic: str | None = None
    content: str
    sources: tuple[PersonalLearningSourceRef, ...] = ()
    model: str
    provider: str
    usage_tokens: int = Field(default=0, ge=0)
    truncated: bool = False
    created_at: datetime
    updated_at: datetime


class PersonalLearningResourceGenerateRequest(Contract):
    tool_kind: LearningToolKind
    session_id: UUID | None = None
    topic: str | None = Field(default=None, max_length=500)
    include_conversation: bool = True
    history: tuple[PersonalLearningTurn, ...] = Field(default=(), max_length=10)
    attachments: tuple[PersonalLearningAttachmentRef, ...] = Field(
        default=(), max_length=5
    )
    attachment_ids: tuple[UUID, ...] = Field(default=(), max_length=5)
    replace_resource_id: UUID | None = None


class PersonalLearningSessionDetail(Contract):
    session_id: UUID
    title: str
    mode: LearningPromptMode
    attachments: tuple[PersonalLearningAttachment, ...] = ()
    messages: tuple[PersonalLearningMessageRecord, ...] = ()
    resources: tuple[PersonalLearningStudyResource, ...] = ()
    created_at: datetime
    updated_at: datetime


class PersonalLearningSessionCreateRequest(Contract):
    title: str | None = Field(default=None, max_length=200)
    mode: LearningPromptMode = "question"
    attachment_ids: tuple[UUID, ...] = Field(default=(), max_length=5)


class PersonalLearningSessionUpdateRequest(Contract):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    mode: LearningPromptMode | None = None
    attachment_ids: tuple[UUID, ...] | None = Field(default=None, max_length=5)


class PersonalLearningDeleteResult(Contract):
    deleted: bool = True


