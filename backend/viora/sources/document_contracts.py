"""Extractor-independent, versioned educational document contracts."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field

from ..contracts import Contract, Digest

ExtractionMethod = Literal[
    "native_text",
    "digital_pdf_text",
    "layout_aware_pdf",
    "table_parser",
    "ocr",
    "handwriting_ocr",
    "vlm_multimodal",
    "manual",
    "deferred",
]

CoordinateSpace = Literal["pdf_points", "normalized_0_1", "pixels"]

PageClassification = Literal[
    "digital_text",
    "needs_layout",
    "needs_table",
    "needs_ocr",
    "visual_content",
    "uncertain",
]

RecommendedProcessor = Literal["layout", "table", "ocr", "visual", "handwriting"]

SourceDocumentFormat = Literal[
    "plain_text",
    "typed_notes",
    "topic_description",
    "digital_pdf",
    "docx",
    "scanned_pdf",
    "png",
    "jpeg",
]


class BoundingBox(Contract):
    x0: float = Field(ge=0.0)
    y0: float = Field(ge=0.0)
    x1: float = Field(ge=0.0)
    y1: float = Field(ge=0.0)
    coordinate_space: CoordinateSpace = "pdf_points"
    page_width: float | None = Field(default=None, gt=0.0)
    page_height: float | None = Field(default=None, gt=0.0)


class SourcePositionSpan(Contract):
    """Maps original source coordinates to normalized extracted text coordinates."""

    source_start_offset: int = Field(ge=0)
    source_end_offset: int = Field(ge=0)
    source_byte_start: int | None = Field(default=None, ge=0)
    source_byte_end: int | None = Field(default=None, ge=0)
    source_line_start: int | None = Field(default=None, ge=1)
    source_line_end: int | None = Field(default=None, ge=1)
    normalized_start_offset: int = Field(ge=0)
    normalized_end_offset: int = Field(ge=0)


class ExtractionProvenance(Contract):
    extractor_id: str = Field(min_length=1, max_length=128)
    extractor_version: str = Field(min_length=1, max_length=32)
    method: ExtractionMethod
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    raw_locator: str | None = Field(default=None, max_length=256)


class EvidenceReference(Contract):
    """Traceable anchor for future outlines, concept graphs, quizzes, and scenes."""

    evidence_id: str = Field(min_length=1, max_length=64)
    source_id: UUID
    version_id: UUID
    source_sha256: Digest
    section_id: str | None = Field(default=None, max_length=64)
    block_id: str | None = Field(default=None, max_length=64)
    page_number: int | None = Field(default=None, ge=1)
    bbox: BoundingBox | None = None
    span: SourcePositionSpan | None = None
    excerpt: str | None = Field(default=None, max_length=1000)


class ExtractionWarning(Contract):
    warning_id: str = Field(min_length=1, max_length=64)
    code: Literal[
        "ocr_required",
        "ocr_unavailable",
        "ocr_fallback_applied",
        "layout_reconstruction_required",
        "table_extraction_required",
        "visual_content_detected",
        "formula_detected_unparsed",
        "reading_order_uncertain",
        "blank_or_low_text_page",
        "repeated_header_footer_suspected",
        "encoding_normalized",
        "truncation_warning",
        "partial_page_error",
    ]
    severity: Literal["info", "warning", "blocking_for_auto_publish"]
    message: str = Field(min_length=1, max_length=512)
    page_number: int | None = Field(default=None, ge=1)
    section_id: str | None = Field(default=None, max_length=64)
    block_id: str | None = Field(default=None, max_length=64)
    deferred_stage: str | None = Field(default=None, max_length=64)


class TextBlock(Contract):
    block_id: str = Field(min_length=1, max_length=64)
    kind: Literal[
        "heading",
        "paragraph",
        "list_item",
        "quote",
        "code",
        "caption",
        "header_or_footer",
        "unknown",
    ]
    page_number: int | None = Field(default=None, ge=1)
    reading_order: int = Field(ge=0)
    section_id: str | None = Field(default=None, max_length=64)
    bbox: BoundingBox | None = None
    raw_text: str
    normalized_text: str
    span: SourcePositionSpan | None = None
    heading_level: int | None = Field(default=None, ge=1, le=6)
    provenance: ExtractionProvenance


class TableBlock(Contract):
    table_id: str = Field(min_length=1, max_length=64)
    page_number: int | None = Field(default=None, ge=1)
    reading_order: int = Field(ge=0)
    section_id: str | None = Field(default=None, max_length=64)
    bbox: BoundingBox | None = None
    caption: str | None = Field(default=None, max_length=512)
    headers: tuple[str, ...] = ()
    rows: tuple[tuple[str, ...], ...] = ()
    raw_text_fallback: str | None = None
    extraction_state: Literal["parsed", "partial", "detected_unparsed"] = "detected_unparsed"
    provenance: ExtractionProvenance


class FigureReference(Contract):
    """Visual/diagram placeholder; interpretation stays None until dedicated multimodal stage."""

    figure_id: str = Field(min_length=1, max_length=64)
    page_number: int = Field(ge=1)
    reading_order: int = Field(ge=0)
    section_id: str | None = Field(default=None, max_length=64)
    bbox: BoundingBox | None = None
    caption: str | None = Field(default=None, max_length=512)
    kind: Literal[
        "raster_image",
        "vector_diagram",
        "chart",
        "handwriting_region",
        "unknown",
    ] = "unknown"
    image_count_on_page: int | None = Field(default=None, ge=0)
    alt_text_or_description: str | None = None
    interpretation_status: Literal[
        "not_interpreted_visual_deferred",
        "caption_only",
        "interpreted",
    ] = "not_interpreted_visual_deferred"
    provenance: ExtractionProvenance


class FormulaReference(Contract):
    formula_id: str = Field(min_length=1, max_length=64)
    page_number: int | None = Field(default=None, ge=1)
    reading_order: int = Field(ge=0)
    section_id: str | None = Field(default=None, max_length=64)
    bbox: BoundingBox | None = None
    raw_representation: str = Field(min_length=1, max_length=2000)
    latex: str | None = Field(default=None, max_length=2000)
    representation_status: Literal[
        "raw_text_only",
        "native_latex",
        "visual_formula_deferred",
    ] = "raw_text_only"
    provenance: ExtractionProvenance


class SourceSection(Contract):
    section_id: str = Field(min_length=1, max_length=64)
    parent_section_id: str | None = Field(default=None, max_length=64)
    title: str = Field(min_length=1, max_length=300)
    level: int = Field(ge=1, le=6)
    reading_order: int = Field(ge=0)
    page_start: int | None = Field(default=None, ge=1)
    page_end: int | None = Field(default=None, ge=1)
    block_ids: tuple[str, ...] = ()
    table_ids: tuple[str, ...] = ()
    figure_ids: tuple[str, ...] = ()
    formula_ids: tuple[str, ...] = ()
    child_section_ids: tuple[str, ...] = ()
    normalized_start_offset: int = Field(ge=0)
    normalized_end_offset: int = Field(ge=0)


class DocumentPage(Contract):
    page_number: int = Field(ge=1)
    width: float | None = Field(default=None, gt=0.0)
    height: float | None = Field(default=None, gt=0.0)
    classification: PageClassification
    recommended_processors: tuple[RecommendedProcessor, ...] = ()
    confidence: float = Field(ge=0.0, le=1.0)
    reading_order_reliable: bool
    raw_char_count: int = Field(ge=0)
    normalized_char_count: int = Field(ge=0)
    image_count: int = Field(ge=0)
    suspected_multicolumn: bool = False
    suspected_table: bool = False
    suspected_formula: bool = False
    text_block_ids: tuple[str, ...] = ()
    table_ids: tuple[str, ...] = ()
    figure_ids: tuple[str, ...] = ()
    formula_ids: tuple[str, ...] = ()
    warning_ids: tuple[str, ...] = ()
    provenance: ExtractionProvenance


class ExtractionQualityReport(Contract):
    overall_status: Literal[
        "complete",
        "partial_with_warnings",
        "ocr_required",
        "unreadable",
    ]
    usable_for_digital_ingestion: bool
    reading_order_verified: bool
    total_pages: int = Field(ge=0)
    digital_text_pages: int = Field(ge=0)
    pages_needing_layout: int = Field(ge=0)
    pages_needing_table: int = Field(ge=0)
    pages_needing_ocr: int = Field(ge=0)
    pages_with_visuals: int = Field(ge=0)
    pages_uncertain: int = Field(ge=0)
    total_raw_chars: int = Field(ge=0)
    total_normalized_chars: int = Field(ge=0)
    warning_count: int = Field(ge=0)
    notes: tuple[str, ...] = ()


class ExtractedDocument(Contract):
    schema_version: Literal["extracted_document.v1"] = "extracted_document.v1"
    tenant_id: UUID
    source_id: UUID
    version_id: UUID
    source_version: int = Field(ge=1)
    source_sha256: Digest
    input_format: SourceDocumentFormat
    title: str = Field(min_length=1, max_length=300)
    normalized_text: str
    pages: tuple[DocumentPage, ...] = ()
    sections: tuple[SourceSection, ...] = ()
    text_blocks: tuple[TextBlock, ...] = ()
    tables: tuple[TableBlock, ...] = ()
    figures: tuple[FigureReference, ...] = ()
    formulas: tuple[FormulaReference, ...] = ()
    evidence_references: tuple[EvidenceReference, ...] = ()
    warnings: tuple[ExtractionWarning, ...] = ()
    quality: ExtractionQualityReport
    provenance: ExtractionProvenance
    created_at: datetime


class DerivedExtractionRecord(Contract):
    """Versioned derived extraction result stored independently of immutable source_versions."""

    extraction_id: UUID
    tenant_id: UUID
    source_id: UUID
    version_id: UUID
    source_sha256: Digest
    extraction_version: int = Field(ge=1)
    supersedes_extraction_id: UUID | None = None
    document: ExtractedDocument
    created_at: datetime
