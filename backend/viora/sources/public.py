"""Public source domain interface, unified document contracts, and router composition."""

from collections.abc import Iterator

from fastapi import APIRouter
from sqlalchemy.orm import Session

from ..config import Settings
from ..db import Database
from ..security import Access, TransactionalAccess
from ..storage.public import ArtifactStore
from . import models
from .cache import (
    EXTRACTION_ALGORITHM_VERSION,
    PREPROCESSING_VERSION,
    ExtractionCacheConfig,
    SqlExtractionCache,
    build_extraction_cache_key,
    build_page_ocr_cache_key,
    build_scope_key,
    cleanup_stale_storage_temps,
    rebind_cached_document,
    reconcile_orphan_artifacts,
)
from .document_contracts import (
    BoundingBox,
    DerivedExtractionRecord,
    DocumentPage,
    EvidenceReference,
    ExtractedDocument,
    ExtractionProvenance,
    ExtractionQualityReport,
    ExtractionWarning,
    FigureReference,
    FormulaReference,
    SourcePositionSpan,
    SourceSection,
    TableBlock,
    TextBlock,
)
from .extraction import AdaptiveDocumentExtractor, ExtractionCancelledError
from .ocr import (
    RAPIDOCR_EXTRACTOR_ID,
    RAPIDOCR_EXTRACTOR_VERSION,
    OcrBackend,
    OcrPageResult,
    OcrUnavailableError,
    RapidOcrBackend,
)
from .ports import DocumentExtractor, SourceDirectory
from .repository import SqlSourceDirectory, SqlSourceRepository
from .router import build_protected_router, build_router
from .schemas import (
    OfferingSourcePolicyInfo,
    SourceAccessInfo,
    SourceIngestionLimits,
    SourceInputFormat,
    SourceProcessingStatus,
    SourceRegistrationResult,
    SourceSummary,
    SourceVersionDetail,
    SourceVersionSummary,
    clamp_source_limits,
)
from .service import SourceIngestionService

__all__ = [
    "EXTRACTION_ALGORITHM_VERSION",
    "PREPROCESSING_VERSION",
    "RAPIDOCR_EXTRACTOR_ID",
    "RAPIDOCR_EXTRACTOR_VERSION",
    "AdaptiveDocumentExtractor",
    "BoundingBox",
    "DerivedExtractionRecord",
    "DocumentExtractor",
    "DocumentPage",
    "EvidenceReference",
    "ExtractedDocument",
    "ExtractionCacheConfig",
    "ExtractionCancelledError",
    "ExtractionProvenance",
    "ExtractionQualityReport",
    "ExtractionWarning",
    "FigureReference",
    "FormulaReference",
    "OcrBackend",
    "OcrPageResult",
    "OcrUnavailableError",
    "OfferingSourcePolicyInfo",
    "RapidOcrBackend",
    "SourceAccessInfo",
    "SourceDirectory",
    "SourceIngestionLimits",
    "SourceIngestionService",
    "SourceInputFormat",
    "SourcePositionSpan",
    "SourceProcessingStatus",
    "SourceRegistrationResult",
    "SourceSection",
    "SourceSummary",
    "SourceVersionDetail",
    "SourceVersionSummary",
    "SqlExtractionCache",
    "SqlSourceRepository",
    "TableBlock",
    "TextBlock",
    "build_extraction_cache_key",
    "build_page_ocr_cache_key",
    "build_scope_key",
    "clamp_source_limits",
    "cleanup_stale_storage_temps",
    "rebind_cached_document",
    "reconcile_orphan_artifacts",
    "register_models",
    "registered_table",
    "router",
    "source_directory",
]

registered_table = models.Source.__table__


def register_models() -> None:
    """Load SQLAlchemy models for sources, versions, policies, and idempotency."""
    from . import models as _models  # noqa: F401


def source_directory(session: Session) -> SourceDirectory:
    return SqlSourceDirectory(session)


def router(
    db: Database,
    access: Access | TransactionalAccess,
    *,
    settings: Settings | None = None,
    artifact_store: ArtifactStore | None = None,
) -> APIRouter:
    if hasattr(access, "transaction"):
        return build_protected_router(
            access,  # type: ignore[arg-type]
            settings=settings,
            artifact_store=artifact_store,
        )

    def repository() -> Iterator[SqlSourceRepository]:
        with db.sessions.begin() as session:
            yield SqlSourceRepository(session)

    return build_router(access, repository)  # type: ignore[arg-type]
