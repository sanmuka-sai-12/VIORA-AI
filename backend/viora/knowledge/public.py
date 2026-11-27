"""Public educational knowledge domain interface."""

from . import models
from .chunking import chunk_extracted_document, estimate_tokens
from .curriculum import analyze_prerequisites_and_order, generate_lesson_outline
from .embeddings import (
    EMBEDDING_DIM,
    DeterministicFakeEmbedder,
    LocalOnnxEmbedder,
    TextEmbedder,
    build_embedder_from_settings,
)
from .extractor import (
    ConceptExtractionExecutionResult,
    ConceptExtractor,
    normalize_concept_name,
)
from .indexing import (
    KnowledgeIndexingWorkerHandler,
    SqlKnowledgeIndexRepository,
    compute_chunk_content_sha256,
    compute_embedding_fingerprint,
)
from .ports import ConceptCacheRepository, ConceptExtractionRepository
from .prompts import build_concept_extraction_messages
from .repository import (
    SqlConceptExtractionCache,
    SqlConceptExtractionRepository,
    build_concept_cache_key,
    build_concept_input_fingerprint,
    build_knowledge_scope_key,
    rebind_cached_knowledge_graph,
)
from .retrieval import HybridKnowledgeRetriever
from .router import build_router
from .schemas import (
    CHUNK_ALGORITHM_VERSION,
    CONCEPT_SCHEMA_VERSION,
    CURRICULUM_ALGORITHM_VERSION,
    INDEX_ALGORITHM_VERSION,
    PROMPT_VERSION,
    RETRIEVAL_ALGORITHM_VERSION,
    AdaptiveChunkPlan,
    ChunkingConfig,
    ChunkingMode,
    ConceptCandidate,
    ConceptDefinition,
    ConceptEvidenceBinding,
    ConceptExample,
    ConceptExtractionRecord,
    ConceptExtractionTriggerRequest,
    ConceptExtractionTriggerResult,
    ConceptRelationshipCandidate,
    DocumentChunk,
    ExtractedKnowledgeGraph,
    GraphEnrichmentConcept,
    GroundingClassification,
    KnowledgeIndexStatus,
    KnowledgeIndexTriggerRequest,
    KnowledgeIndexTriggerResult,
    KnowledgePublicationState,
    KnowledgePublishRequest,
    KnowledgeRetrievalResult,
    KnowledgeRetrieveRequest,
    KnowledgeReviewRecord,
    KnowledgeReviewRequest,
    KnowledgeValidationReport,
    LearningObjectiveCandidate,
    LessonModule,
    LessonOutlineStep,
    PrerequisiteAnalysis,
    PrerequisiteCandidate,
    PrerequisiteCycle,
    PublicationReviewStatus,
    PublishedLessonPackage,
    RawConceptCandidate,
    RawConceptExtractionBatch,
    RawRelationshipCandidate,
    RetrievalMode,
    RetrievedEvidenceChunk,
    ReviewDecision,
    StructuredLessonOutline,
    TeachingOrderStep,
)
from .service import ConceptKnowledgeService
from .worker_handler import ConceptExtractionWorkerHandler

__all__ = [
    "CHUNK_ALGORITHM_VERSION",
    "CONCEPT_SCHEMA_VERSION",
    "CURRICULUM_ALGORITHM_VERSION",
    "EMBEDDING_DIM",
    "INDEX_ALGORITHM_VERSION",
    "PROMPT_VERSION",
    "RETRIEVAL_ALGORITHM_VERSION",
    "AdaptiveChunkPlan",
    "ChunkingConfig",
    "ChunkingMode",
    "ConceptCacheRepository",
    "ConceptCandidate",
    "ConceptDefinition",
    "ConceptEvidenceBinding",
    "ConceptExample",
    "ConceptExtractionExecutionResult",
    "ConceptExtractionRecord",
    "ConceptExtractionRepository",
    "ConceptExtractionTriggerRequest",
    "ConceptExtractionTriggerResult",
    "ConceptExtractionWorkerHandler",
    "ConceptExtractor",
    "ConceptKnowledgeService",
    "ConceptRelationshipCandidate",
    "DeterministicFakeEmbedder",
    "DocumentChunk",
    "ExtractedKnowledgeGraph",
    "GraphEnrichmentConcept",
    "GroundingClassification",
    "HybridKnowledgeRetriever",
    "KnowledgeIndexStatus",
    "KnowledgeIndexTriggerRequest",
    "KnowledgeIndexTriggerResult",
    "KnowledgeIndexingWorkerHandler",
    "KnowledgePublicationState",
    "KnowledgePublishRequest",
    "KnowledgeRetrievalResult",
    "KnowledgeRetrieveRequest",
    "KnowledgeReviewRecord",
    "KnowledgeReviewRequest",
    "KnowledgeValidationReport",
    "LearningObjectiveCandidate",
    "LessonModule",
    "LessonOutlineStep",
    "LocalOnnxEmbedder",
    "PrerequisiteAnalysis",
    "PrerequisiteCandidate",
    "PrerequisiteCycle",
    "PublicationReviewStatus",
    "PublishedLessonPackage",
    "RawConceptCandidate",
    "RawConceptExtractionBatch",
    "RawRelationshipCandidate",
    "RetrievalMode",
    "RetrievedEvidenceChunk",
    "ReviewDecision",
    "SqlConceptExtractionCache",
    "SqlConceptExtractionRepository",
    "SqlKnowledgeIndexRepository",
    "StructuredLessonOutline",
    "TeachingOrderStep",
    "TextEmbedder",
    "analyze_prerequisites_and_order",
    "build_concept_cache_key",
    "build_concept_extraction_messages",
    "build_concept_input_fingerprint",
    "build_embedder_from_settings",
    "build_knowledge_scope_key",
    "chunk_extracted_document",
    "compute_chunk_content_sha256",
    "compute_embedding_fingerprint",
    "estimate_tokens",
    "generate_lesson_outline",
    "normalize_concept_name",
    "rebind_cached_knowledge_graph",
    "register_models",
    "registered_table",
    "router",
]

registered_table = models.ConceptExtraction.__table__


def register_models() -> None:
    """Load SQLAlchemy knowledge models into shared Alembic metadata."""
    from . import models as _models  # noqa: F401


def router(access, db=None, *, settings=None, embedder=None):
    return build_router(access, db, settings=settings, embedder=embedder)
