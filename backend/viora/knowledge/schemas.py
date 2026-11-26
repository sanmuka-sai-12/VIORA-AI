"""Versioned Pydantic contracts for structure-aware chunking and concept extraction."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from ..contracts import Contract, Digest, JobState
from ..sources.public import SourceInputFormat

CHUNK_ALGORITHM_VERSION = "struct_chunk:1.0"
PROMPT_VERSION = "concept_prompt:1.1"
CONCEPT_SCHEMA_VERSION = "1.0"

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
    model_config = ConfigDict(extra="ignore")

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
    model_config = ConfigDict(extra="ignore")

    source_concept: str = Field(min_length=1, max_length=160)
    relation_type: RelationshipType = "part_of"
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
