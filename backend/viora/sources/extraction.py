"""Hardened input validation, format sniffing, and adaptive extraction routing."""

import contextlib
import hashlib
import io
import multiprocessing as mp
import os
import re
import threading
import time
import weakref
from collections import Counter
from collections.abc import Callable, Iterator
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import PurePosixPath, PureWindowsPath
from typing import Any
from uuid import UUID

import pdfplumber
import pypdf
from pypdf.errors import (
    EmptyFileError,
    FileNotDecryptedError,
    LimitReachedError,
    PdfReadError,
    PyPdfError,
)

from ..security import AccessError
from .document_contracts import (
    BoundingBox,
    DocumentPage,
    EvidenceReference,
    ExtractedDocument,
    ExtractionProvenance,
    ExtractionQualityReport,
    ExtractionWarning,
    FigureReference,
    FormulaReference,
    PageClassification,
    RecommendedProcessor,
    SourceDocumentFormat,
    SourcePositionSpan,
    SourceSection,
    TableBlock,
    TextBlock,
)
from .ocr import (
    OCR_LOW_CONFIDENCE_THRESHOLD,
    OCR_UNSUPPORTED_OR_HANDWRITING_THRESHOLD,
    OcrBackend,
    OcrPageResult,
    RapidOcrBackend,
    decode_bounded_image,
    get_process_ocr_backend,
    hash_pil_image,
    render_pdf_page_to_rgb,
)
from .schemas import SourceIngestionLimits, SourceInputFormat, TextInputFormat
from .spatial_layout import (
    SPATIAL_EXTRACTOR_ID,
    SPATIAL_EXTRACTOR_VERSION,
    SpatialPageResult,
    analyze_page_spatial,
    normalize_header_footer_template,
)

NATIVE_EXTRACTOR_ID = "viora.sources.native_text"
NATIVE_EXTRACTOR_VERSION = "1.0"
PYPDF_EXTRACTOR_ID = "viora.sources.pypdf_triage"
PYPDF_EXTRACTOR_VERSION = "1.0"
ADAPTIVE_EXTRACTOR_ID = "viora.sources.adaptive_extractor"
ADAPTIVE_EXTRACTOR_VERSION = "2.0"

_BINARY_SIGNATURES: tuple[tuple[bytes, str], ...] = (
    (b"%PDF-", "pdf"),
    (b"\x89PNG\r\n\x1a\n", "png"),
    (b"\xff\xd8\xff", "jpeg"),
    (b"GIF87a", "gif"),
    (b"GIF89a", "gif"),
    (b"PK\x03\x04", "zip_or_office"),
    (b"\x1f\x8b", "gzip"),
    (b"\x7fELF", "elf"),
    (b"MZ", "pe_executable"),
    (b"RIFF", "riff"),
)

_UNSUPPORTED_DOC_OR_MEDIA_EXTENSIONS = frozenset(
    {
        ".doc",
        ".docx",
        ".xls",
        ".xlsx",
        ".ppt",
        ".pptx",
        ".gif",
        ".webp",
        ".bmp",
        ".tiff",
        ".mp3",
        ".wav",
        ".mp4",
        ".mkv",
        ".avi",
        ".zip",
        ".tar",
        ".gz",
        ".7z",
        ".exe",
        ".dll",
        ".bat",
        ".sh",
    }
)

_MARKDOWN_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
_NUMBERED_HEADING_RE = re.compile(
    r"^(?:chapter|section|unit|module)\s+\d+(?:\.\d+)*[:.\s-]+(.+?)\s*$",
    re.IGNORECASE,
)
_LIST_ITEM_RE = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+")
_MULTICOLUMN_GAP_RE = re.compile(r"\S.{2,}?\s{4,}\S")
_WIDE_SPLIT_RE = re.compile(r"\s{4,}")
_PIPE_ROW_RE = re.compile(r"^\s*\|[^|]+\|[^|]+\|")
_LATEX_DISPLAY_RE = re.compile(r"\$\$(.+?)\$\$", re.DOTALL)
_LATEX_INLINE_RE = re.compile(r"(?<!\$)\$([^$\n]{2,200})(?<!\$)\$")
_UNICODE_MATH_RE = re.compile(r"[∫∑∏√∞≈≠≤≥±∂∇∈∉∪∩→⇒∀∃]")
_PLAIN_EQ_RE = re.compile(
    r"^(?:[A-Za-z_][A-Za-z0-9_]{0,24}(?:\([^)\n]{1,32}\))?)\s*=\s*([^\n]{2,140})$"
)
_PLAIN_MATH_OP_RE = re.compile(r"(?:[+\-*/^]|=>|\bmod\b|\bTheta\(|\bOmega\(|\bO\()")
_C_CODE_STATEMENT_RE = re.compile(
    r"(?:;|->|\{|\}|\+\+|--|==|!=|&&|\|\||`|(?:^|[\s(,=])\*[A-Za-z_]|\[[^\]]+\]\s*=|"
    r"\b(?:int|size_t|void|char|struct|typedef|static|return|malloc|calloc|realloc|free|printf|assert)\b)"
)
_LABEL_PREFIX_RE = re.compile(r"^[A-Za-z][A-Za-z0-9\s\-/()]{1,42}:\s+(.+)$")
_CONTROL_CHARS_RE = re.compile(r"[\x00-\x1f\x7f]")
_CODE_FENCE_RE = re.compile(r"^\s*```")
_CAPTION_LINE_RE = re.compile(
    r"^(?:figure|fig\.?|table|chart|diagram)\s+\d+(?:\.\d+)?[:.\-]?\s*",
    re.IGNORECASE,
)
_TABLE_CAPTION_RE = re.compile(r"^table\s+\d+(?:\.\d+)?[:.\-]?\s*", re.IGNORECASE)
_CODE_HINT_RE = re.compile(r"^(?:\s{4,}\S|def\s+\w+|class\s+\w+|return\s+|import\s+|for\s+.+:\s*$)")
_CID_GARBLE_RE = re.compile(r"\(cid:\d+\)")

_PYPDF_READ_ERRORS = (
    PyPdfError,
    PdfReadError,
    EmptyFileError,
    ValueError,
    TypeError,
    RecursionError,
    OSError,
    KeyError,
    IndexError,
    ZeroDivisionError,
)

MAX_PDF_DECOMPRESSED_STREAM_BYTES = 16_000_000
MAX_PDF_DECODING_WORK_BYTES = 32_000_000
MAX_PDF_EXTRACTION_SECONDS = 10.0


@contextmanager
def _bounded_pypdf_config() -> Iterator[None]:
    """Clamp pypdf stream decompression and recovery work per parse."""
    with pypdf.apply_configuration(
        maximum_declared_stream_length=MAX_PDF_DECOMPRESSED_STREAM_BYTES,
        array_based_stream_maximum_output_length=MAX_PDF_DECOMPRESSED_STREAM_BYTES,
        jbig2_maximum_output_length=MAX_PDF_DECOMPRESSED_STREAM_BYTES,
        lzw_maximum_output_length=MAX_PDF_DECOMPRESSED_STREAM_BYTES,
        run_length_maximum_output_length=MAX_PDF_DECOMPRESSED_STREAM_BYTES,
        zlib_maximum_output_length=MAX_PDF_DECOMPRESSED_STREAM_BYTES,
        zlib_maximum_recovery_input_length=32_768,
        brotli_maximum_output_length=MAX_PDF_DECOMPRESSED_STREAM_BYTES,
        image_maximum_buffer_size=MAX_PDF_DECOMPRESSED_STREAM_BYTES,
        stream_decoding_work_maximum_length=MAX_PDF_DECODING_WORK_BYTES,
        page_tree_maximum_depth=64,
    ):
        yield


def _count_page_images_safely(page_obj: Any, *, max_depth: int = 4) -> int:
    """Count /Image XObjects in page resources without decompressing raster streams."""
    visited: set[tuple[int, int]] = set()

    def _walk_resources(resources: Any, depth: int) -> int:
        if depth > max_depth or resources is None:
            return 0
        try:
            res_obj = resources.get_object() if hasattr(resources, "get_object") else resources
            if not hasattr(res_obj, "get"):
                return 0
            xobjs = res_obj.get("/XObject")
            if xobjs is None:
                return 0
            xobj_dict = xobjs.get_object() if hasattr(xobjs, "get_object") else xobjs
            if not hasattr(xobj_dict, "items"):
                return 0
        except (*_PYPDF_READ_ERRORS, AttributeError):
            return 0

        count = 0
        for _, ref in list(xobj_dict.items())[:256]:
            try:
                indirect = getattr(ref, "indirect_reference", None)
                if indirect is not None:
                    key = (int(indirect.idnum), int(indirect.generation))
                    if key in visited:
                        continue
                    visited.add(key)
                obj = ref.get_object() if hasattr(ref, "get_object") else ref
                if not hasattr(obj, "get"):
                    continue
                subtype = str(obj.get("/Subtype", ""))
                if subtype == "/Image":
                    count += 1
                elif subtype == "/Form":
                    count += _walk_resources(obj.get("/Resources"), depth + 1)
            except (*_PYPDF_READ_ERRORS, AttributeError):
                continue
        return count

    return _walk_resources(getattr(page_obj, "get", lambda _k: None)("/Resources"), 0)


def sanitize_original_filename(raw_name: str | None) -> str | None:
    """Extract a safe basename without traversal segments or control characters."""
    if raw_name is None:
        return None
    normalized = raw_name.strip().replace("\\", "/")
    if not normalized:
        return None
    if "\x00" in normalized:
        raise AccessError(422, "invalid_filename", "Filename contains invalid NUL byte")
    candidate = PureWindowsPath(PurePosixPath(normalized).name).name
    cleaned = _CONTROL_CHARS_RE.sub("", candidate).strip()
    if not cleaned or cleaned in {".", ".."}:
        raise AccessError(
            422, "invalid_filename", "Filename must not be a traversal reference"
        )
    if len(cleaned) > 255:
        suffix = PurePosixPath(cleaned).suffix[:32]
        stem = cleaned[: 255 - len(suffix)]
        cleaned = f"{stem}{suffix}"
    return cleaned


def _detect_binary_signature(raw_bytes: bytes) -> str | None:
    head = raw_bytes[:1024]
    if b"%PDF-" in head:
        return "pdf"
    for magic, label in _BINARY_SIGNATURES:
        if magic != b"%PDF-" and raw_bytes.startswith(magic):
            return label
    if b"\x00" in raw_bytes[:8192]:
        return "binary_nul"
    return None


def _validate_pdf_bytes(
    raw_bytes: bytes,
    limits: SourceIngestionLimits,
) -> tuple[pypdf.PdfReader, int]:
    if not raw_bytes:
        raise AccessError(422, "empty_input", "Uploaded PDF file is empty")
    if len(raw_bytes) > limits.max_bytes:
        raise AccessError(
            413,
            "payload_too_large",
            f"PDF exceeds configured byte limit of {limits.max_bytes} bytes",
        )
    if b"%PDF-" not in raw_bytes[:1024]:
        raise AccessError(
            415,
            "unsupported_media_type",
            "Content does not match a valid PDF header signature",
        )
    with _bounded_pypdf_config():
        try:
            reader = pypdf.PdfReader(io.BytesIO(raw_bytes), strict=False)
        except FileNotDecryptedError:
            raise AccessError(
                422,
                "encrypted_pdf",
                "Encrypted or password-protected PDFs are not supported",
            ) from None
        except _PYPDF_READ_ERRORS:
            raise AccessError(
                422,
                "malformed_pdf",
                "Malformed or corrupted PDF could not be parsed",
            ) from None

        if reader.is_encrypted:
            raise AccessError(
                422,
                "encrypted_pdf",
                "Encrypted or password-protected PDFs are not supported",
            )
        try:
            page_count = len(reader.pages)
        except FileNotDecryptedError:
            raise AccessError(
                422,
                "encrypted_pdf",
                "Encrypted or password-protected PDFs are not supported",
            ) from None
        except _PYPDF_READ_ERRORS:
            raise AccessError(
                422,
                "malformed_pdf",
                "Malformed PDF page tree could not be inspected",
            ) from None

    if page_count == 0:
        raise AccessError(422, "empty_input", "PDF contains zero pages")
    if page_count > limits.max_pdf_pages:
        raise AccessError(
            413,
            "page_limit_exceeded",
            f"PDF page count {page_count} exceeds limit of {limits.max_pdf_pages}",
        )
    return reader, page_count


def _validate_text_bytes(
    raw_bytes: bytes,
    limits: SourceIngestionLimits,
) -> str:
    if not raw_bytes:
        raise AccessError(422, "empty_input", "Text input cannot be empty")
    if len(raw_bytes) > limits.max_bytes:
        raise AccessError(
            413,
            "payload_too_large",
            f"Text input exceeds byte limit of {limits.max_bytes} bytes",
        )
    detected_bin = _detect_binary_signature(raw_bytes)
    if detected_bin is not None:
        raise AccessError(
            415,
            "unsupported_media_type",
            f"Binary payload ({detected_bin}) rejected for plain-text ingestion",
        )
    try:
        decoded = raw_bytes.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        raise AccessError(
            415,
            "unsupported_media_type",
            "Text payload must be valid UTF-8",
        ) from None
    decoded = decoded.removeprefix("\ufeff")
    if len(decoded) > limits.max_text_chars:
        raise AccessError(
            413,
            "text_limit_exceeded",
            f"Text character count {len(decoded)} exceeds limit of {limits.max_text_chars}",
        )
    if len(decoded.strip()) < limits.min_text_chars:
        raise AccessError(
            422,
            "empty_input",
            "Text input must contain meaningful non-whitespace content",
        )
    return decoded


def validate_and_sniff_text_input(
    *,
    input_format: TextInputFormat,
    content: str,
    original_filename: str | None,
    limits: SourceIngestionLimits,
) -> tuple[SourceInputFormat, bytes, str, str | None]:
    """Validate text payload and normalize canonical UTF-8 bytes without trusting client hints."""
    sanitized_name = sanitize_original_filename(original_filename)
    if sanitized_name and sanitized_name.lower().endswith(".pdf"):
        raise AccessError(
            415,
            "unsupported_media_type",
            "Filename with .pdf extension is not permitted for text registration",
        )
    raw_bytes = content.encode("utf-8", errors="strict")
    _validate_text_bytes(raw_bytes, limits)
    digest = hashlib.sha256(raw_bytes).hexdigest()
    return input_format, raw_bytes, digest, sanitized_name


def _validate_image_bytes(
    raw_bytes: bytes,
    *,
    expected_format: str,
    limits: SourceIngestionLimits,
) -> None:
    if not raw_bytes:
        raise AccessError(422, "empty_input", "Uploaded image is empty")
    if len(raw_bytes) > limits.max_bytes:
        raise AccessError(
            413,
            "payload_too_large",
            f"Image exceeds configured byte limit of {limits.max_bytes} bytes",
        )
    if expected_format == "png" and not raw_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
        raise AccessError(
            415,
            "unsupported_media_type",
            "Claimed PNG input does not match valid PNG header signature",
        )
    if expected_format == "jpeg" and not raw_bytes.startswith(b"\xff\xd8\xff"):
        raise AccessError(
            415,
            "unsupported_media_type",
            "Claimed JPEG input does not match valid JPEG header signature",
        )
    try:
        decode_bounded_image(raw_bytes)
    except (OSError, ValueError) as exc:
        raise AccessError(
            422,
            "malformed_image",
            f"Could not decode image payload safely: {exc}",
        ) from None


def validate_and_sniff_upload(
    *,
    raw_bytes: bytes,
    declared_format: SourceInputFormat | None,
    original_filename: str | None,
    claimed_content_type: str | None,
    limits: SourceIngestionLimits,
) -> tuple[SourceInputFormat, bytes, str, str | None]:
    """Inspect uploaded bytes directly; reject spoofed extensions, MIME mismatches, and bad PDFs/images."""
    sanitized_name = sanitize_original_filename(original_filename)
    if not raw_bytes:
        raise AccessError(422, "empty_input", "Uploaded file is empty")
    if len(raw_bytes) > limits.max_bytes:
        raise AccessError(
            413,
            "payload_too_large",
            f"Upload exceeds configured limit of {limits.max_bytes} bytes",
        )

    ext = PurePosixPath(sanitized_name).suffix.lower() if sanitized_name else ""
    mime = (claimed_content_type or "").split(";", 1)[0].strip().lower()
    is_pdf_bytes = b"%PDF-" in raw_bytes[:1024]
    is_png_bytes = raw_bytes.startswith(b"\x89PNG\r\n\x1a\n")
    is_jpeg_bytes = raw_bytes.startswith(b"\xff\xd8\xff")

    if (
        declared_format in {"digital_pdf", "scanned_pdf"}
        or ext == ".pdf"
        or mime == "application/pdf"
    ):
        if not is_pdf_bytes:
            raise AccessError(
                415,
                "unsupported_media_type",
                "Claimed PDF input does not contain valid PDF content",
            )
        _validate_pdf_bytes(raw_bytes, limits)
        resolved_pdf_fmt: SourceInputFormat = (
            "scanned_pdf" if declared_format == "scanned_pdf" else "digital_pdf"
        )
        return (
            resolved_pdf_fmt,
            raw_bytes,
            hashlib.sha256(raw_bytes).hexdigest(),
            sanitized_name,
        )

    if declared_format == "png" or ext == ".png" or mime == "image/png":
        if declared_format in {"jpeg"} or ext in {".jpg", ".jpeg"} or mime == "image/jpeg":
            raise AccessError(
                415,
                "unsupported_media_type",
                "Conflicting PNG and JPEG file extension/format claims",
            )
        _validate_image_bytes(raw_bytes, expected_format="png", limits=limits)
        return (
            "png",
            raw_bytes,
            hashlib.sha256(raw_bytes).hexdigest(),
            sanitized_name,
        )

    if declared_format == "jpeg" or ext in {".jpg", ".jpeg"} or mime == "image/jpeg":
        _validate_image_bytes(raw_bytes, expected_format="jpeg", limits=limits)
        return (
            "jpeg",
            raw_bytes,
            hashlib.sha256(raw_bytes).hexdigest(),
            sanitized_name,
        )

    if declared_format is None and is_pdf_bytes:
        _validate_pdf_bytes(raw_bytes, limits)
        return (
            "digital_pdf",
            raw_bytes,
            hashlib.sha256(raw_bytes).hexdigest(),
            sanitized_name,
        )

    if declared_format is None and is_png_bytes:
        _validate_image_bytes(raw_bytes, expected_format="png", limits=limits)
        return (
            "png",
            raw_bytes,
            hashlib.sha256(raw_bytes).hexdigest(),
            sanitized_name,
        )

    if declared_format is None and is_jpeg_bytes:
        _validate_image_bytes(raw_bytes, expected_format="jpeg", limits=limits)
        return (
            "jpeg",
            raw_bytes,
            hashlib.sha256(raw_bytes).hexdigest(),
            sanitized_name,
        )

    if ext in _UNSUPPORTED_DOC_OR_MEDIA_EXTENSIONS:
        raise AccessError(
            415,
            "unsupported_media_type",
            f"File format {ext} is not supported in this ingestion stage",
        )

    _validate_text_bytes(raw_bytes, limits)
    resolved_format: SourceInputFormat = declared_format or "typed_notes"
    return resolved_format, raw_bytes, hashlib.sha256(raw_bytes).hexdigest(), sanitized_name


def _normalize_line(line: str) -> str:
    return re.sub(r"[ \t]+", " ", line.strip())


def _is_likely_math_inline(candidate: str) -> bool:
    return any(
        op in candidate for op in ("=", "+", "-", "*", "/", "^", "_", "\\")
    ) or bool(_UNICODE_MATH_RE.search(candidate))


def _extract_plain_math_candidate(line: str) -> str | None:
    """Detect unambiguous plain-text educational equations while excluding C statements."""
    stripped = line.strip()
    if not stripped or len(stripped) > 180 or _C_CODE_STATEMENT_RE.search(stripped):
        return None
    m_lbl = _LABEL_PREFIX_RE.match(stripped)
    cand = m_lbl.group(1).strip() if m_lbl else stripped
    if "=>" in cand:
        cand = cand.split("=>", 1)[0].strip()
    if " for " in cand and "=" in cand:
        cand = cand.split(" for ", 1)[0].strip()
    eq_m = _PLAIN_EQ_RE.match(cand)
    if eq_m is None or len(cand.split()) > 14:
        return None
    rhs = eq_m.group(1)
    if not _PLAIN_MATH_OP_RE.search(cand):
        return None
    # Require algebraic/function token structure beyond casual prose hyphens
    if re.search(r"[A-Za-z0-9_)]\s*(?:[+\-*/^=]|\bmod\b)\s*[A-Za-z0-9_(]", cand) or (
        "(" in rhs and ")" in rhs
    ):
        return cand
    return None


def _extract_formulas_from_text(
    *,
    text: str,
    page_number: int | None,
    section_id: str | None,
    start_order: int,
    formula_counter_start: int,
    provenance: ExtractionProvenance,
) -> tuple[list[FormulaReference], int]:
    formulas: list[FormulaReference] = []
    idx = formula_counter_start
    seen: set[str] = set()
    for match in _LATEX_DISPLAY_RE.finditer(text):
        expr = match.group(1).strip()
        if expr and expr not in seen:
            seen.add(expr)
            idx += 1
            formulas.append(
                FormulaReference(
                    formula_id=f"frm-{idx}",
                    page_number=page_number,
                    reading_order=start_order,
                    section_id=section_id,
                    raw_representation=match.group(0)[:2000],
                    latex=expr[:2000],
                    representation_status="native_latex",
                    provenance=provenance,
                )
            )
    for match in _LATEX_INLINE_RE.finditer(text):
        expr = match.group(1).strip()
        if expr and expr not in seen and _is_likely_math_inline(expr):
            seen.add(expr)
            idx += 1
            formulas.append(
                FormulaReference(
                    formula_id=f"frm-{idx}",
                    page_number=page_number,
                    reading_order=start_order,
                    section_id=section_id,
                    raw_representation=match.group(0)[:2000],
                    latex=expr[:2000],
                    representation_status="native_latex",
                    provenance=provenance,
                )
            )
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if _UNICODE_MATH_RE.search(stripped) and stripped not in seen and not _C_CODE_STATEMENT_RE.search(stripped):
            cand = stripped
        else:
            cand = _extract_plain_math_candidate(stripped) or ""
        if cand and cand not in seen:
            seen.add(cand)
            idx += 1
            formulas.append(
                FormulaReference(
                    formula_id=f"frm-{idx}",
                    page_number=page_number,
                    reading_order=start_order,
                    section_id=section_id,
                    raw_representation=cand[:2000],
                    latex=None,
                    representation_status="raw_text_only",
                    provenance=provenance,
                )
            )
    return formulas, idx


@dataclass
class _TriagePage:
    page_no: int
    page_width: float | None
    page_height: float | None
    page_text: str
    image_count: int
    has_vectors: bool
    page_error: bool
    limit_reached: bool
    layout_fell_back: bool
    high_garble: bool
    low_text: bool
    suspected_table: bool
    suspected_multicolumn: bool
    suspected_code: bool
    suspected_figure: bool
    top_template: str | None
    bottom_template: str | None
    raw_table_rows: tuple[str, ...]


def _page_has_vector_graphics(page_obj: Any) -> bool:
    """Quickly detect vector rect/line drawing operators without full geometry parsing."""
    try:
        contents = page_obj.get_contents()
        if contents is None:
            return False
        data = contents.get_data()
        if not data or len(data) > 512_000:
            return False
        re_hits = data.count(b" re\n") + data.count(b" re ")
        line_hits = data.count(b" l\n") + data.count(b" l ")
        return re_hits >= 2 or line_hits >= 3
    except (*_PYPDF_READ_ERRORS, AttributeError):
        return False


def _scale_ocr_bbox_to_pdf(
    x0: float,
    y0: float,
    x1: float,
    y1: float,
    img_w: int,
    img_h: int,
    pdf_w: float,
    pdf_h: float,
) -> BoundingBox:
    sx = pdf_w / max(1, img_w)
    sy = pdf_h / max(1, img_h)
    bx0 = max(0.0, min(x0 * sx, pdf_w))
    by0 = max(0.0, min(y0 * sy, pdf_h))
    bx1 = max(bx0, min(x1 * sx, pdf_w))
    by1 = max(by0, min(y1 * sy, pdf_h))
    return BoundingBox(
        x0=round(bx0, 2),
        y0=round(by0, 2),
        x1=round(bx1, 2),
        y1=round(by1, 2),
        coordinate_space="pdf_points",
        page_width=round(pdf_w, 2),
        page_height=round(pdf_h, 2),
    )


@dataclass
class _PdfAssemblyState:
    source_id: UUID
    version_id: UUID
    source_sha256: str
    provenance: ExtractionProvenance
    page_count: int
    title: str
    source_version: int = 1
    blocks: list[TextBlock] = field(default_factory=list)
    tables: list[TableBlock] = field(default_factory=list)
    figures: list[FigureReference] = field(default_factory=list)
    formulas: list[FormulaReference] = field(default_factory=list)
    warnings: list[ExtractionWarning] = field(default_factory=list)
    evidence_refs: list[EvidenceReference] = field(default_factory=list)
    normalized_parts: list[str] = field(default_factory=list)
    norm_cursor: int = 0
    reading_order: int = 0
    warn_seq: int = 0
    fig_seq: int = 0
    tbl_seq: int = 0
    frm_seq: int = 0
    sec_seq: int = 1
    reading_order_verified: bool = True
    sections_work: list[dict[str, object]] = field(default_factory=list)
    section_stack: list[dict[str, object]] = field(default_factory=list)
    current_section: dict[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        root: dict[str, object] = {
            "section_id": "sec-1",
            "parent_section_id": None,
            "title": self.title,
            "level": 1,
            "reading_order": 0,
            "page_start": 1,
            "page_end": self.page_count,
            "block_ids": [],
            "table_ids": [],
            "figure_ids": [],
            "formula_ids": [],
            "child_section_ids": [],
            "norm_start": 0,
            "norm_end": 0,
        }
        self.sections_work.append(root)
        self.section_stack.append(root)
        self.current_section = root

    def add_warning(
        self,
        code: str,
        severity: str,
        message: str,
        *,
        page_number: int | None = None,
        section_id: str | None = None,
        block_id: str | None = None,
        deferred_stage: str | None = None,
    ) -> str:
        self.warn_seq += 1
        wid = f"warn-{self.warn_seq}"
        self.warnings.append(
            ExtractionWarning(
                warning_id=wid,
                code=code,  # type: ignore[arg-type]
                severity=severity,  # type: ignore[arg-type]
                message=message[:512],
                page_number=page_number,
                section_id=section_id,
                block_id=block_id,
                deferred_stage=deferred_stage,
            )
        )
        return wid

    def append_norm_piece(self, piece: str) -> tuple[int, int]:
        if self.normalized_parts:
            self.norm_cursor += 2
        start = self.norm_cursor
        self.normalized_parts.append(piece)
        self.norm_cursor += len(piece)
        return start, self.norm_cursor

    def add_evidence(
        self,
        *,
        section_id: str,
        block_id: str | None,
        page_number: int,
        span: SourcePositionSpan,
        excerpt: str,
        bbox: BoundingBox | None = None,
    ) -> None:
        self.evidence_refs.append(
            EvidenceReference(
                evidence_id=f"ev-{len(self.evidence_refs) + 1}",
                source_id=self.source_id,
                version_id=self.version_id,
                source_sha256=self.source_sha256,
                section_id=section_id,
                block_id=block_id,
                page_number=page_number,
                bbox=bbox,
                span=span,
                excerpt=excerpt[:1000],
            )
        )

    def _bump_norm_end(self, norm_end: int, page_number: int) -> None:
        for ancestor in self.section_stack:
            ancestor["norm_end"] = norm_end
        self.current_section["page_end"] = page_number

    def open_section(
        self,
        *,
        title: str,
        level: int,
        page_no: int,
        hierarchical: bool = False,
    ) -> tuple[str, int, int]:
        self.sec_seq += 1
        clamped_level = min(level, 6)
        if hierarchical:
            while len(self.section_stack) > 1 and int(self.section_stack[-1]["level"]) >= level:  # type: ignore[arg-type]
                popped = self.section_stack.pop()
                popped["norm_end"] = self.norm_cursor
            parent = self.section_stack[-1] if self.section_stack else None
        else:
            root = self.sections_work[0]
            self.section_stack = [root]
            parent = root
        n_start, n_end = self.append_norm_piece(title)
        new_sec_id = f"sec-{self.sec_seq}"
        new_sec: dict[str, object] = {
            "section_id": new_sec_id,
            "parent_section_id": None if parent is None else str(parent["section_id"]),
            "title": title,
            "level": clamped_level,
            "reading_order": self.reading_order,
            "page_start": page_no,
            "page_end": page_no,
            "block_ids": [],
            "table_ids": [],
            "figure_ids": [],
            "formula_ids": [],
            "child_section_ids": [],
            "norm_start": n_start,
            "norm_end": n_end,
        }
        if parent is not None:
            parent["child_section_ids"].append(new_sec_id)  # type: ignore[attr-defined]
        if not hierarchical and parent is not None:
            parent["norm_end"] = self.norm_cursor
        self.sections_work.append(new_sec)
        self.section_stack.append(new_sec)
        self.current_section = new_sec
        return new_sec_id, n_start, n_end

    def emit_block(
        self,
        *,
        kind: str,
        page_number: int,
        raw_text: str,
        normalized_text: str,
        provenance: ExtractionProvenance,
        bbox: BoundingBox | None = None,
        section_id: str | None = None,
        heading_level: int | None = None,
        source_start_offset: int = 0,
        source_end_offset: int = 0,
        source_byte_start: int | None = None,
        source_byte_end: int | None = None,
        source_line_start: int | None = None,
        source_line_end: int | None = None,
        pre_norm_span: tuple[int, int] | None = None,
        page_block_ids: list[str] | None = None,
        page_formula_ids: list[str] | None = None,
        page_warn_ids: list[str] | None = None,
        extract_formulas: bool = False,
        warn_on_formulas: bool = False,
    ) -> int:
        sec = self.current_section
        sec_id = section_id or str(sec["section_id"])
        if pre_norm_span is not None:
            n_start, n_end = pre_norm_span
        else:
            n_start, n_end = self.append_norm_piece(normalized_text)
            self._bump_norm_end(n_end, page_number)
        blk_id = f"blk-{len(self.blocks) + 1}"
        span = SourcePositionSpan(
            source_start_offset=source_start_offset,
            source_end_offset=source_end_offset,
            source_byte_start=source_byte_start,
            source_byte_end=source_byte_end,
            source_line_start=source_line_start,
            source_line_end=source_line_end,
            normalized_start_offset=n_start,
            normalized_end_offset=n_end,
        )
        self.blocks.append(
            TextBlock(
                block_id=blk_id,
                kind=kind,  # type: ignore[arg-type]
                page_number=page_number,
                reading_order=self.reading_order,
                section_id=sec_id,
                bbox=bbox,
                raw_text=raw_text,
                normalized_text=normalized_text,
                span=span,
                heading_level=heading_level,
                provenance=provenance,
            )
        )
        sec["block_ids"].append(blk_id)  # type: ignore[attr-defined]
        if page_block_ids is not None:
            page_block_ids.append(blk_id)
        if extract_formulas and kind != "code":
            new_formulas, self.frm_seq = _extract_formulas_from_text(
                text=normalized_text,
                page_number=page_number,
                section_id=sec_id,
                start_order=self.reading_order,
                formula_counter_start=self.frm_seq,
                provenance=provenance,
            )
            for frm in new_formulas:
                self.formulas.append(frm)
                sec["formula_ids"].append(frm.formula_id)  # type: ignore[attr-defined]
                if page_formula_ids is not None:
                    page_formula_ids.append(frm.formula_id)
            if warn_on_formulas and new_formulas:
                wid = self.add_warning(
                    "formula_detected_unparsed",
                    "info",
                    f"Detected {len(new_formulas)} mathematical expression(s) on page {page_number}.",
                    page_number=page_number,
                    block_id=blk_id,
                )
                if page_warn_ids is not None:
                    page_warn_ids.append(wid)
        self.add_evidence(
            section_id=sec_id,
            block_id=blk_id,
            page_number=page_number,
            span=span,
            excerpt=normalized_text,
            bbox=bbox,
        )
        self.reading_order += 1
        return len(normalized_text)

    def finalize_sections(self) -> tuple[SourceSection, ...]:
        for sec in self.section_stack:
            sec["norm_end"] = self.norm_cursor
        return tuple(
            SourceSection(
                section_id=str(s["section_id"]),
                parent_section_id=(
                    None if s["parent_section_id"] is None else str(s["parent_section_id"])
                ),
                title=str(s["title"]),
                level=int(s["level"]),
                reading_order=int(s["reading_order"]),
                page_start=int(s["page_start"]),
                page_end=int(s["page_end"]),
                block_ids=tuple(s["block_ids"]),  # type: ignore[arg-type]
                table_ids=tuple(s["table_ids"]),  # type: ignore[arg-type]
                figure_ids=tuple(s["figure_ids"]),  # type: ignore[arg-type]
                formula_ids=tuple(s["formula_ids"]),  # type: ignore[arg-type]
                child_section_ids=tuple(s["child_section_ids"]),  # type: ignore[arg-type]
                normalized_start_offset=int(s["norm_start"]),
                normalized_end_offset=int(s["norm_end"]),
            )
            for s in self.sections_work
        )

    def build_document(
        self,
        *,
        tenant_id: UUID,
        input_format: SourceDocumentFormat,
        pages: tuple[DocumentPage, ...],
        total_raw_chars: int,
        limits: SourceIngestionLimits,
        notes: tuple[str, ...],
        overall_status: str | None = None,
        usable_override: bool | None = None,
        doc_provenance: ExtractionProvenance | None = None,
    ) -> ExtractedDocument:
        full_normalized = "\n\n".join(self.normalized_parts)
        counts = Counter(p.classification for p in pages)
        usable = (
            len(full_normalized.strip()) >= limits.min_text_chars
            if usable_override is None
            else usable_override
        )
        resolved_status = overall_status
        if resolved_status is None:
            if not usable:
                resolved_status = "ocr_required" if counts["needs_ocr"] > 0 else "unreadable"
            elif self.warnings:
                resolved_status = "partial_with_warnings"
            else:
                resolved_status = "complete"
        quality = ExtractionQualityReport(
            overall_status=resolved_status,  # type: ignore[arg-type]
            usable_for_digital_ingestion=usable,
            reading_order_verified=self.reading_order_verified,
            total_pages=len(pages),
            digital_text_pages=counts["digital_text"],
            pages_needing_layout=counts["needs_layout"],
            pages_needing_table=counts["needs_table"],
            pages_needing_ocr=counts["needs_ocr"],
            pages_with_visuals=counts["visual_content"],
            pages_uncertain=counts["uncertain"],
            total_raw_chars=total_raw_chars,
            total_normalized_chars=len(full_normalized),
            warning_count=len(self.warnings),
            notes=notes,
        )
        return ExtractedDocument(
            tenant_id=tenant_id,
            source_id=self.source_id,
            version_id=self.version_id,
            source_version=self.source_version,
            source_sha256=self.source_sha256,
            input_format=input_format,
            title=self.title,
            normalized_text=full_normalized,
            pages=pages,
            sections=self.finalize_sections(),
            text_blocks=tuple(self.blocks),
            tables=tuple(self.tables),
            figures=tuple(self.figures),
            formulas=tuple(self.formulas),
            evidence_references=tuple(self.evidence_refs),
            warnings=tuple(self.warnings),
            quality=quality,
            provenance=doc_provenance or self.provenance,
            created_at=datetime.now(UTC),
        )


class ExtractionCancelledError(RuntimeError):
    """Raised when cooperative cancellation is observed during document extraction."""


def _pool_worker_init(warm_ocr: bool, intra_threads: int = 1) -> None:
    """Initialize bounded threading and optional OCR model once per worker process."""
    t = str(max(1, intra_threads))
    os.environ["OMP_NUM_THREADS"] = t
    os.environ["OPENBLAS_NUM_THREADS"] = t
    os.environ["MKL_NUM_THREADS"] = t
    os.environ["NUMEXPR_NUM_THREADS"] = t
    os.environ["VIORA_OCR_INTRA_THREADS"] = t
    if warm_ocr:
        get_process_ocr_backend(warm_up=True, intra_op_threads=max(1, intra_threads))


def _pool_run_spatial_page(
    args: tuple[bytes, int, frozenset[str], frozenset[str], bool, bool],
) -> tuple[int, SpatialPageResult, bool]:
    raw_bytes, idx, rep_headers, rep_footers, extract_tables, allow_borderless = args
    with pdfplumber.open(io.BytesIO(raw_bytes)) as plumber_pdf:
        res = analyze_page_spatial(
            plumber_pdf.pages[idx],
            repeated_header_templates=rep_headers,
            repeated_footer_templates=rep_footers,
            extract_tables=extract_tables,
            allow_borderless_tables=allow_borderless,
        )
    route_to_ocr = (
        not res.blocks
        and not res.tables
        and any(f.kind == "raster_image" for f in res.figures)
    )
    return idx, res, route_to_ocr


def _pool_run_ocr_page(
    args: tuple[bytes, int, int, bool, bool],
) -> tuple[int, OcrPageResult]:
    raw_bytes, idx, ocr_dpi, force_preprocess, adaptive_preprocess = args
    backend = get_process_ocr_backend(warm_up=True)
    img = render_pdf_page_to_rgb(raw_bytes, idx, dpi=ocr_dpi)
    if adaptive_preprocess:
        res = backend.recognize_image_adaptive(img, force_preprocess=force_preprocess)
    else:
        res = backend.recognize_image(img, preprocess=force_preprocess)
    return idx, res


def _pool_run_pdf_triage(
    args: tuple[bytes, SourceIngestionLimits],
) -> tuple[int, list[_TriagePage], int, frozenset[str], frozenset[str]]:
    raw_bytes, limits = args
    started_at = time.monotonic()
    with _bounded_pypdf_config():
        reader, page_count = _validate_pdf_bytes(raw_bytes, limits)
        helper = AdaptiveDocumentExtractor(page_workers=1, isolate_untrusted_parse=False)
        triage_pages, total_raw_chars, repeated_headers, repeated_footers = (
            helper._triage_pdf_pages(
                reader=reader,
                page_count=page_count,
                started_at=started_at,
                limits=limits,
            )
        )
    return page_count, triage_pages, total_raw_chars, repeated_headers, repeated_footers


def _pool_run_isolated_binary_extract(
    args: tuple[
        UUID,
        UUID,
        UUID,
        int,
        str,
        SourceDocumentFormat,
        str,
        bytes,
        SourceIngestionLimits,
        bool,
        OcrBackend | None,
        int,
        bool,
        bool,
        bool,
        int,
        int,
        float,
        bool,
    ],
) -> tuple[ExtractedDocument, list[tuple[str, OcrPageResult]]]:
    (
        tenant_id,
        source_id,
        version_id,
        source_version,
        source_sha256,
        input_format,
        title,
        raw_bytes,
        limits,
        ocr_enabled,
        custom_ocr_backend,
        ocr_dpi,
        preprocess_for_ocr,
        enable_layout_fallback,
        enable_table_extraction,
        max_fallback_pages,
        max_ocr_pages,
        task_timeout_seconds,
        enable_adaptive_ocr_preprocess,
    ) = args
    backend: OcrBackend | None
    if custom_ocr_backend is not None:
        backend = custom_ocr_backend
    elif ocr_enabled:
        backend = get_process_ocr_backend(warm_up=True)
    else:
        backend = None
    captured_ocr: list[tuple[str, OcrPageResult]] = []
    extractor = AdaptiveDocumentExtractor(
        ocr_backend=backend,
        enable_layout_fallback=enable_layout_fallback,
        enable_table_extraction=enable_table_extraction,
        max_fallback_pages=max_fallback_pages,
        max_ocr_pages=max_ocr_pages,
        ocr_dpi=ocr_dpi,
        preprocess_for_ocr=preprocess_for_ocr,
        page_workers=1,
        task_timeout_seconds=task_timeout_seconds,
        enable_adaptive_ocr_preprocess=enable_adaptive_ocr_preprocess,
        isolate_untrusted_parse=False,
    )
    doc = extractor.extract(
        tenant_id=tenant_id,
        source_id=source_id,
        version_id=version_id,
        source_version=source_version,
        source_sha256=source_sha256,
        input_format=input_format,
        title=title,
        raw_bytes=raw_bytes,
        limits=limits,
        page_ocr_cache_store=lambda sha, res: captured_ocr.append((sha, res)),
    )
    return doc, captured_ocr


def _terminate_pool_processes(executor: ProcessPoolExecutor) -> None:
    procs = getattr(executor, "_processes", None)
    if isinstance(procs, dict):
        for proc in list(procs.values()):
            with contextlib.suppress(Exception):
                if proc.is_alive():
                    proc.kill()
    executor.shutdown(wait=False, cancel_futures=True)


def _shutdown_pool_holder(holder: dict[str, Any]) -> None:
    pool = holder.get("pool")
    holder["pool"] = None
    holder["workers"] = 0
    holder["warm_ocr"] = False
    if pool is not None:
        with contextlib.suppress(Exception):
            _terminate_pool_processes(pool)


def _run_bounded_process_tasks(
    *,
    fn: Any,
    tasks: list[Any],
    executor: ProcessPoolExecutor,
    timeout_seconds: float,
    cancel_check: Callable[[], bool] | None,
    on_abort: Callable[[], None] | None = None,
) -> list[Any]:
    """Execute independent page tasks in a killable bounded ProcessPoolExecutor."""
    deadline = time.monotonic() + max(0.5, timeout_seconds)
    results: list[Any] = []
    try:
        pending = {executor.submit(fn, item) for item in tasks}
        while pending:
            if cancel_check is not None and cancel_check():
                if on_abort is not None:
                    on_abort()
                else:
                    _terminate_pool_processes(executor)
                raise ExtractionCancelledError("Extraction cancelled cooperatively")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                if on_abort is not None:
                    on_abort()
                else:
                    _terminate_pool_processes(executor)
                raise AccessError(
                    422,
                    "extraction_timeout",
                    "Parallel PDF extraction exceeded worker execution timeout",
                )
            done, pending = wait(
                pending,
                timeout=min(0.5, remaining),
                return_when=FIRST_COMPLETED,
            )
            for fut in done:
                results.append(fut.result())
        return results
    except Exception:
        if on_abort is not None:
            on_abort()
        else:
            _terminate_pool_processes(executor)
        raise


class AdaptiveDocumentExtractor:
    """Quality-aware adaptive extractor: native text, pypdf fast path, pdfplumber spatial, and optional OCR."""

    def __init__(
        self,
        *,
        ocr_backend: OcrBackend | None = None,
        enable_layout_fallback: bool = True,
        enable_table_extraction: bool = True,
        max_fallback_pages: int = 50,
        max_ocr_pages: int = 10,
        ocr_dpi: int = 150,
        preprocess_for_ocr: bool = False,
        page_workers: int = 1,
        task_timeout_seconds: float = 30.0,
        enable_adaptive_ocr_preprocess: bool = True,
        isolate_untrusted_parse: bool = False,
        intra_op_thread_budget: int | None = None,
    ) -> None:
        self.ocr_backend = ocr_backend
        self.enable_layout_fallback = enable_layout_fallback
        self.enable_table_extraction = enable_table_extraction
        self.max_fallback_pages = max(0, max_fallback_pages)
        self.max_ocr_pages = max(0, max_ocr_pages)
        self.ocr_dpi = max(72, min(ocr_dpi, 300))
        self.preprocess_for_ocr = preprocess_for_ocr
        self.page_workers = max(1, min(page_workers, 8))
        self.task_timeout_seconds = max(1.0, task_timeout_seconds)
        self.enable_adaptive_ocr_preprocess = enable_adaptive_ocr_preprocess
        self.isolate_untrusted_parse = isolate_untrusted_parse
        self.intra_op_thread_budget = (
            max(1, intra_op_thread_budget) if intra_op_thread_budget is not None else None
        )
        self._pool_lock = threading.Lock()
        self._pool_holder: dict[str, Any] = {"pool": None, "workers": 0, "warm_ocr": False}
        self._pool_finalizer = weakref.finalize(
            self, _shutdown_pool_holder, self._pool_holder
        )

    def _get_or_create_pool(self, *, warm_ocr: bool) -> ProcessPoolExecutor:
        with self._pool_lock:
            existing: ProcessPoolExecutor | None = self._pool_holder.get("pool")
            existing_workers = int(self._pool_holder.get("workers", 0))
            existing_warm = bool(self._pool_holder.get("warm_ocr", False))
            if (
                existing is not None
                and existing_workers == self.page_workers
                and (existing_warm or not warm_ocr)
            ):
                return existing
            if existing is not None:
                _shutdown_pool_holder(self._pool_holder)
            cpu_count = os.cpu_count() or 2
            intra_threads = (
                self.intra_op_thread_budget
                if self.intra_op_thread_budget is not None
                else max(1, cpu_count // max(1, self.page_workers))
            )
            ctx = mp.get_context("spawn")
            pool = ProcessPoolExecutor(
                max_workers=self.page_workers,
                mp_context=ctx,
                initializer=_pool_worker_init,
                initargs=(warm_ocr, intra_threads),
            )
            self._pool_holder["pool"] = pool
            self._pool_holder["workers"] = self.page_workers
            self._pool_holder["warm_ocr"] = warm_ocr
            return pool

    def _invalidate_pool(self) -> None:
        with self._pool_lock:
            _shutdown_pool_holder(self._pool_holder)

    def warm_pool(self, *, warm_ocr: bool | None = None) -> None:
        """Pre-spawn and warm reusable worker processes when parallel or isolated execution is enabled."""
        if self.page_workers <= 1 and not self.isolate_untrusted_parse:
            return
        use_ocr = (
            isinstance(self.ocr_backend, RapidOcrBackend)
            and self.ocr_backend.is_available()
            if warm_ocr is None
            else warm_ocr
        )
        pool = self._get_or_create_pool(warm_ocr=use_ocr)
        # Force child worker processes to spawn & run initializer now.
        futs = [pool.submit(int, 0) for _ in range(self.page_workers)]
        for fut in futs:
            fut.result()

    def close(self) -> None:
        """Terminate and reap any persistent worker processes owned by this extractor."""
        self._invalidate_pool()

    def _run_ocr_on_image(self, img: Any) -> OcrPageResult:
        assert self.ocr_backend is not None
        if self.enable_adaptive_ocr_preprocess and isinstance(
            self.ocr_backend, RapidOcrBackend
        ):
            return self.ocr_backend.recognize_image_adaptive(
                img, force_preprocess=self.preprocess_for_ocr
            )
        return self.ocr_backend.recognize_image(img, preprocess=self.preprocess_for_ocr)

    def extract(
        self,
        *,
        tenant_id: UUID,
        source_id: UUID,
        version_id: UUID,
        source_version: int,
        source_sha256: str,
        input_format: SourceDocumentFormat,
        title: str,
        raw_bytes: bytes,
        limits: SourceIngestionLimits,
        progress_callback: Callable[[str, dict[str, str | int | float | bool]], None]
        | None = None,
        cancel_check: Callable[[], bool] | None = None,
        page_ocr_cache_lookup: Callable[[str], OcrPageResult | None] | None = None,
        page_ocr_cache_store: Callable[[str, OcrPageResult], None] | None = None,
    ) -> ExtractedDocument:
        if cancel_check is not None and cancel_check():
            raise ExtractionCancelledError("Extraction cancelled before start")
        if (
            self.isolate_untrusted_parse
            and input_format in {"digital_pdf", "scanned_pdf", "png", "jpeg"}
            and (self.page_workers == 1 or input_format in {"png", "jpeg"})
        ):
            ocr_enabled = (
                self.ocr_backend is not None and self.ocr_backend.is_available()
            )
            if progress_callback is not None:
                progress_callback(
                    "isolated_parse",
                    {"isolated_process": True, "format": input_format},
                )
            pool = self._get_or_create_pool(
                warm_ocr=bool(
                    ocr_enabled and isinstance(self.ocr_backend, RapidOcrBackend)
                )
            )
            custom_ocr = (
                self.ocr_backend
                if (
                    self.ocr_backend is not None
                    and not isinstance(self.ocr_backend, RapidOcrBackend)
                )
                else None
            )
            doc, captured_ocr = _run_bounded_process_tasks(
                fn=_pool_run_isolated_binary_extract,
                tasks=[
                    (
                        tenant_id,
                        source_id,
                        version_id,
                        source_version,
                        source_sha256,
                        input_format,
                        title,
                        raw_bytes,
                        limits,
                        ocr_enabled,
                        custom_ocr,
                        self.ocr_dpi,
                        self.preprocess_for_ocr,
                        self.enable_layout_fallback,
                        self.enable_table_extraction,
                        self.max_fallback_pages,
                        self.max_ocr_pages,
                        self.task_timeout_seconds,
                        self.enable_adaptive_ocr_preprocess,
                    )
                ],
                executor=pool,
                timeout_seconds=self.task_timeout_seconds,
                cancel_check=cancel_check,
                on_abort=self._invalidate_pool,
            )[0]
            if page_ocr_cache_store is not None:
                for sha, ocr_res in captured_ocr:
                    page_ocr_cache_store(sha, ocr_res)
            return doc
        if input_format in {"digital_pdf", "scanned_pdf"}:
            return self._extract_digital_pdf(
                tenant_id=tenant_id,
                source_id=source_id,
                version_id=version_id,
                source_version=source_version,
                source_sha256=source_sha256,
                input_format=input_format,
                title=title,
                raw_bytes=raw_bytes,
                limits=limits,
                progress_callback=progress_callback,
                cancel_check=cancel_check,
                page_ocr_cache_lookup=page_ocr_cache_lookup,
                page_ocr_cache_store=page_ocr_cache_store,
            )
        if input_format in {"png", "jpeg"}:
            return self._extract_image(
                tenant_id=tenant_id,
                source_id=source_id,
                version_id=version_id,
                source_version=source_version,
                source_sha256=source_sha256,
                input_format=input_format,
                title=title,
                raw_bytes=raw_bytes,
                limits=limits,
                progress_callback=progress_callback,
            )
        return self._extract_native_text(
            tenant_id=tenant_id,
            source_id=source_id,
            version_id=version_id,
            source_version=source_version,
            source_sha256=source_sha256,
            input_format=input_format,
            title=title,
            raw_bytes=raw_bytes,
            limits=limits,
        )

    def _extract_native_text(
        self,
        *,
        tenant_id: UUID,
        source_id: UUID,
        version_id: UUID,
        source_version: int,
        source_sha256: str,
        input_format: SourceDocumentFormat,
        title: str,
        raw_bytes: bytes,
        limits: SourceIngestionLimits,
    ) -> ExtractedDocument:
        decoded = _validate_text_bytes(raw_bytes, limits)
        has_bom = raw_bytes.startswith(b"\xef\xbb\xbf")
        provenance = ExtractionProvenance(
            extractor_id=NATIVE_EXTRACTOR_ID,
            extractor_version=NATIVE_EXTRACTOR_VERSION,
            method="native_text",
            confidence=1.0,
            raw_locator="utf8_stream",
        )
        state = _PdfAssemblyState(
            source_id=source_id,
            version_id=version_id,
            source_sha256=source_sha256,
            provenance=provenance,
            page_count=1,
            title=title,
            source_version=source_version,
        )
        if has_bom or "\r\n" in decoded:
            state.add_warning(
                "encoding_normalized",
                "info",
                "Normalized line endings and/or UTF-8 BOM without altering text order.",
                page_number=1,
            )

        line_spans: list[tuple[int, int, int, int, int, str]] = []
        char_pos = 0
        byte_pos = 3 if has_bom else 0
        for line_no, raw_line in enumerate(decoded.splitlines(keepends=True), start=1):
            stripped_nl = raw_line.rstrip("\r\n")
            char_end = char_pos + len(stripped_nl)
            byte_end = byte_pos + len(stripped_nl.encode("utf-8"))
            line_spans.append(
                (char_pos, char_end, byte_pos, byte_end, line_no, stripped_nl)
            )
            char_pos += len(raw_line)
            byte_pos += len(raw_line.encode("utf-8"))
        if not line_spans and decoded:
            line_spans.append(
                (0, len(decoded), 0, len(decoded.encode("utf-8")), 1, decoded)
            )

        idx = 0
        while idx < len(line_spans):
            c_start, c_end, b_start, b_end, l_no, line_txt = line_spans[idx]
            norm_line = _normalize_line(line_txt)
            if not norm_line:
                idx += 1
                continue

            if _CODE_FENCE_RE.match(line_txt):
                fence_start = idx
                idx += 1
                code_inner: list[tuple[int, int, int, int, int, str]] = []
                while idx < len(line_spans) and not _CODE_FENCE_RE.match(line_spans[idx][5]):
                    code_inner.append(line_spans[idx])
                    idx += 1
                fence_end = idx if idx < len(line_spans) else len(line_spans) - 1
                if idx < len(line_spans):
                    idx += 1
                raw_span_start, _, byte_span_start, _, line_start, _ = line_spans[fence_start]
                _, raw_span_end, _, byte_span_end, line_end, _ = line_spans[fence_end]
                c_raw = decoded[raw_span_start:raw_span_end]
                c_norm = "\n".join(item[5].rstrip() for item in code_inner) or c_raw
                state.emit_block(
                    kind="code",
                    page_number=1,
                    raw_text=c_raw,
                    normalized_text=c_norm,
                    provenance=provenance,
                    source_start_offset=raw_span_start,
                    source_end_offset=raw_span_end,
                    source_byte_start=byte_span_start,
                    source_byte_end=byte_span_end,
                    source_line_start=line_start,
                    source_line_end=line_end,
                )
                continue

            md_match = _MARKDOWN_HEADING_RE.match(norm_line)
            num_match = _NUMBERED_HEADING_RE.match(norm_line) if not md_match else None
            if md_match or num_match:
                h_level = len(md_match.group(1)) if md_match else 2
                h_title = (md_match.group(2) if md_match else norm_line)[:300]
                sec_id, n_start, n_end = state.open_section(
                    title=h_title, level=h_level, page_no=1, hierarchical=True
                )
                state.emit_block(
                    kind="heading",
                    page_number=1,
                    raw_text=line_txt,
                    normalized_text=h_title,
                    provenance=provenance,
                    section_id=sec_id,
                    heading_level=min(h_level, 6),
                    source_start_offset=c_start,
                    source_end_offset=c_end,
                    source_byte_start=b_start,
                    source_byte_end=b_end,
                    source_line_start=l_no,
                    source_line_end=l_no,
                    pre_norm_span=(n_start, n_end),
                )
                idx += 1
                continue

            para_lines: list[tuple[int, int, int, int, int, str]] = []
            while idx < len(line_spans):
                nxt = line_spans[idx]
                nxt_norm = _normalize_line(nxt[5])
                if (
                    not nxt_norm
                    or _CODE_FENCE_RE.match(nxt[5])
                    or _MARKDOWN_HEADING_RE.match(nxt_norm)
                    or _NUMBERED_HEADING_RE.match(nxt_norm)
                ):
                    break
                para_lines.append(nxt)
                idx += 1

            p_raw = decoded[para_lines[0][0] : para_lines[-1][1]]
            p_norm = "\n".join(
                _normalize_line(item[5])
                for item in para_lines
                if _normalize_line(item[5])
            )
            state.emit_block(
                kind="list_item" if _LIST_ITEM_RE.match(para_lines[0][5]) else "paragraph",
                page_number=1,
                raw_text=p_raw,
                normalized_text=p_norm,
                provenance=provenance,
                source_start_offset=para_lines[0][0],
                source_end_offset=para_lines[-1][1],
                source_byte_start=para_lines[0][2],
                source_byte_end=para_lines[-1][3],
                source_line_start=para_lines[0][4],
                source_line_end=para_lines[-1][4],
                extract_formulas=True,
            )

        page = DocumentPage(
            page_number=1,
            classification="digital_text",
            recommended_processors=(),
            confidence=1.0,
            reading_order_reliable=True,
            raw_char_count=len(decoded),
            normalized_char_count=len("\n\n".join(state.normalized_parts)),
            image_count=0,
            suspected_formula=bool(state.formulas),
            text_block_ids=tuple(b.block_id for b in state.blocks),
            formula_ids=tuple(f.formula_id for f in state.formulas),
            warning_ids=tuple(w.warning_id for w in state.warnings),
            provenance=provenance,
        )
        return state.build_document(
            tenant_id=tenant_id,
            input_format=input_format,
            pages=(page,),
            total_raw_chars=len(decoded),
            limits=limits,
            notes=("Native UTF-8 text normalization preserved deterministic offsets.",),
            overall_status="partial_with_warnings" if state.warnings else "complete",
            usable_override=True,
        )

    def _commit_pdf_paragraph(
        self,
        state: _PdfAssemblyState,
        *,
        paragraph_buf: list[tuple[int, int, int, str]],
        page_text: str,
        page_no: int,
        page_block_ids: list[str],
        page_formula_ids: list[str],
        page_warn_ids: list[str],
    ) -> int:
        if not paragraph_buf:
            return 0
        p_start, p_end = paragraph_buf[0][0], paragraph_buf[-1][1]
        line_start, line_end = paragraph_buf[0][2], paragraph_buf[-1][2]
        first_raw = paragraph_buf[0][3]
        p_raw = page_text[p_start:p_end]
        p_norm = "\n".join(
            _normalize_line(x[3]) for x in paragraph_buf if _normalize_line(x[3])
        )
        paragraph_buf.clear()
        if not p_norm:
            return 0
        return state.emit_block(
            kind="list_item" if _LIST_ITEM_RE.match(first_raw) else "paragraph",
            page_number=page_no,
            raw_text=p_raw,
            normalized_text=p_norm,
            provenance=state.provenance,
            source_start_offset=p_start,
            source_end_offset=p_end,
            source_line_start=line_start,
            source_line_end=line_end,
            page_block_ids=page_block_ids,
            page_formula_ids=page_formula_ids,
            page_warn_ids=page_warn_ids,
            extract_formulas=True,
            warn_on_formulas=True,
        )

    def _extract_image(
        self,
        *,
        tenant_id: UUID,
        source_id: UUID,
        version_id: UUID,
        source_version: int,
        source_sha256: str,
        input_format: SourceDocumentFormat,
        title: str,
        raw_bytes: bytes,
        limits: SourceIngestionLimits,
        progress_callback: Callable[[str, dict[str, str | int | float | bool]], None]
        | None = None,
    ) -> ExtractedDocument:
        if not raw_bytes:
            raise AccessError(422, "empty_input", "Image input is empty")
        if len(raw_bytes) > limits.max_bytes:
            raise AccessError(
                413,
                "payload_too_large",
                f"Image exceeds byte limit of {limits.max_bytes} bytes",
            )
        if progress_callback is not None:
            progress_callback("ocr", {"total_pages": 1, "completed_pages": 0})
        try:
            img = decode_bounded_image(raw_bytes)
        except (OSError, ValueError) as exc:
            raise AccessError(
                422,
                "malformed_image",
                f"Could not decode image payload safely: {exc}",
            ) from None

        iw, ih = float(img.width), float(img.height)
        fig_bbox = BoundingBox(
            x0=0.0,
            y0=0.0,
            x1=iw,
            y1=ih,
            coordinate_space="pixels",
            page_width=iw,
            page_height=ih,
        )
        base_prov = ExtractionProvenance(
            extractor_id=ADAPTIVE_EXTRACTOR_ID,
            extractor_version=ADAPTIVE_EXTRACTOR_VERSION,
            method="ocr" if self.ocr_backend is not None else "deferred",
            confidence=None,
            raw_locator=f"image:{input_format}:{img.width}x{img.height}",
        )
        state = _PdfAssemblyState(
            source_id=source_id,
            version_id=version_id,
            source_sha256=source_sha256,
            provenance=base_prov,
            page_count=1,
            title=title,
            source_version=source_version,
        )
        self._append_deferred_figure(
            state,
            page_no=1,
            bbox=fig_bbox,
            kind="raster_image",
            image_count=1,
            extractor_id=ADAPTIVE_EXTRACTOR_ID,
            extractor_version=ADAPTIVE_EXTRACTOR_VERSION,
            raw_locator=f"image_region={img.width}x{img.height}",
            increment_order=False,
        )
        state.reading_order = 1

        ocr_res: OcrPageResult | None = None
        if self.ocr_backend is not None and self.ocr_backend.is_available():
            ocr_res = self._run_ocr_on_image(img)
            base_prov = ExtractionProvenance(
                extractor_id=ocr_res.engine_id,
                extractor_version=ocr_res.engine_version,
                method="ocr",
                confidence=ocr_res.mean_confidence,
                raw_locator=f"image_ocr:{img.width}x{img.height};preprocess={ocr_res.preprocess_applied}",
            )
            for ln in ocr_res.lines:
                bbox = BoundingBox(
                    x0=round(min(ln.x0, iw), 2),
                    y0=round(min(ln.y0, ih), 2),
                    x1=round(min(ln.x1, iw), 2),
                    y1=round(min(ln.y1, ih), 2),
                    coordinate_space="pixels",
                    page_width=iw,
                    page_height=ih,
                )
                state.emit_block(
                    kind="paragraph",
                    page_number=1,
                    raw_text=ln.text,
                    normalized_text=ln.text,
                    provenance=base_prov,
                    bbox=bbox,
                )
            self._append_ocr_warnings(state, page_no=1, ocr_res=ocr_res, is_image=True)
        else:
            code = "ocr_unavailable" if self.ocr_backend is not None else "ocr_required"
            state.add_warning(
                code,
                "blocking_for_auto_publish",
                "Image requires printed-text OCR or multimodal analysis; no OCR backend is active.",
                page_number=1,
                deferred_stage="batch_2_4_ocr_worker",
            )
        if progress_callback is not None:
            progress_callback("assembling", {"total_pages": 1, "completed_pages": 1})

        full_norm = "\n\n".join(state.normalized_parts)
        usable = len(full_norm.strip()) >= limits.min_text_chars
        classification: PageClassification = "visual_content" if usable else "needs_ocr"
        reliable = usable and (
            ocr_res is not None and ocr_res.mean_confidence >= 0.65
        )
        state.reading_order_verified = reliable
        page = DocumentPage(
            page_number=1,
            width=iw,
            height=ih,
            classification=classification,
            recommended_processors=("visual",) if usable else ("ocr", "visual"),
            confidence=ocr_res.mean_confidence if ocr_res is not None else 0.25,
            reading_order_reliable=reliable,
            raw_char_count=len(full_norm),
            normalized_char_count=len(full_norm),
            image_count=1,
            text_block_ids=tuple(b.block_id for b in state.blocks),
            figure_ids=("fig-1",),
            warning_ids=tuple(w.warning_id for w in state.warnings),
            provenance=base_prov,
        )
        return state.build_document(
            tenant_id=tenant_id,
            input_format=input_format,
            pages=(page,),
            total_raw_chars=len(full_norm),
            limits=limits,
            notes=(
                "Image extraction executed at extractor level; diagram/handwriting interpretation remains deferred.",
            ),
            overall_status="partial_with_warnings" if usable else "ocr_required",
            usable_override=usable,
            doc_provenance=base_prov,
        )

    def _extract_digital_pdf(
        self,
        *,
        tenant_id: UUID,
        source_id: UUID,
        version_id: UUID,
        source_version: int,
        source_sha256: str,
        input_format: SourceDocumentFormat,
        title: str,
        raw_bytes: bytes,
        limits: SourceIngestionLimits,
        progress_callback: Callable[[str, dict[str, str | int | float | bool]], None]
        | None = None,
        cancel_check: Callable[[], bool] | None = None,
        page_ocr_cache_lookup: Callable[[str], OcrPageResult | None] | None = None,
        page_ocr_cache_store: Callable[[str, OcrPageResult], None] | None = None,
    ) -> ExtractedDocument:
        started_at = time.monotonic()
        if self.isolate_untrusted_parse and self.page_workers > 1:
            if progress_callback is not None:
                progress_callback("triage", {"isolated_process": True})
            pool = self._get_or_create_pool(
                warm_ocr=bool(
                    isinstance(self.ocr_backend, RapidOcrBackend)
                    and self.ocr_backend.is_available()
                )
            )
            page_count, triage_pages, total_raw_chars, repeated_headers, repeated_footers = (
                _run_bounded_process_tasks(
                    fn=_pool_run_pdf_triage,
                    tasks=[(raw_bytes, limits)],
                    executor=pool,
                    timeout_seconds=self.task_timeout_seconds,
                    cancel_check=cancel_check,
                    on_abort=self._invalidate_pool,
                )[0]
            )
            return self._extract_digital_pdf_pages(
                reader=None,
                page_count=page_count,
                started_at=started_at,
                tenant_id=tenant_id,
                source_id=source_id,
                version_id=version_id,
                source_version=source_version,
                source_sha256=source_sha256,
                input_format=input_format,
                title=title,
                raw_bytes=raw_bytes,
                limits=limits,
                progress_callback=progress_callback,
                cancel_check=cancel_check,
                page_ocr_cache_lookup=page_ocr_cache_lookup,
                page_ocr_cache_store=page_ocr_cache_store,
                precomputed_triage=(
                    triage_pages,
                    total_raw_chars,
                    repeated_headers,
                    repeated_footers,
                ),
            )
        with _bounded_pypdf_config():
            reader, page_count = _validate_pdf_bytes(raw_bytes, limits)
            return self._extract_digital_pdf_pages(
                reader=reader,
                page_count=page_count,
                started_at=started_at,
                tenant_id=tenant_id,
                source_id=source_id,
                version_id=version_id,
                source_version=source_version,
                source_sha256=source_sha256,
                input_format=input_format,
                title=title,
                raw_bytes=raw_bytes,
                limits=limits,
                progress_callback=progress_callback,
                cancel_check=cancel_check,
                page_ocr_cache_lookup=page_ocr_cache_lookup,
                page_ocr_cache_store=page_ocr_cache_store,
            )

    def _triage_pdf_pages(
        self,
        *,
        reader: pypdf.PdfReader,
        page_count: int,
        started_at: float,
        limits: SourceIngestionLimits,
    ) -> tuple[list[_TriagePage], int, frozenset[str], frozenset[str]]:
        triage_pages: list[_TriagePage] = []
        total_raw_chars = 0
        top_counts: Counter[str] = Counter()
        bot_counts: Counter[str] = Counter()

        for page_index in range(page_count):
            if (time.monotonic() - started_at) > MAX_PDF_EXTRACTION_SECONDS:
                raise AccessError(
                    422,
                    "extraction_timeout",
                    "PDF extraction exceeded per-request CPU time budget",
                )
            page_no = page_index + 1
            page_obj = reader.pages[page_index]
            page_width: float | None = None
            page_height: float | None = None
            with contextlib.suppress(AttributeError, TypeError, ValueError):
                box = page_obj.mediabox
                w, h = float(box.width), float(box.height)
                if w > 0 and h > 0:
                    page_width, page_height = w, h

            page_error = False
            limit_reached = False
            layout_fell_back = False
            try:
                page_text = page_obj.extract_text(extraction_mode="layout") or ""
            except LimitReachedError:
                page_text = ""
                page_error = True
                limit_reached = True
            except _PYPDF_READ_ERRORS:
                try:
                    page_text = page_obj.extract_text() or ""
                    layout_fell_back = True
                except _PYPDF_READ_ERRORS:
                    page_text = ""
                    page_error = True

            total_raw_chars += len(page_text)
            if total_raw_chars > limits.max_text_chars:
                raise AccessError(
                    413,
                    "text_limit_exceeded",
                    f"Extracted PDF text exceeds limit of {limits.max_text_chars} characters",
                )

            image_count = _count_page_images_safely(page_obj)
            has_vectors = False if limit_reached else _page_has_vector_graphics(page_obj)

            stripped = page_text.strip()
            lines = page_text.splitlines()
            non_empty = [normalized for ln in lines if (normalized := _normalize_line(ln))]
            top_tmpl = (
                normalize_header_footer_template(non_empty[0])
                if non_empty
                and 2 < len(non_empty[0]) <= 80
                and not _NUMBERED_HEADING_RE.match(non_empty[0])
                else None
            )
            bot_tmpl = (
                normalize_header_footer_template(non_empty[-1])
                if non_empty and 2 < len(non_empty[-1]) <= 80
                else None
            )
            if top_tmpl:
                top_counts[top_tmpl] += 1
            if bot_tmpl:
                bot_counts[bot_tmpl] += 1

            table_rows: list[str] = []
            two_col_rows: list[str] = []
            max_two_col_words = 0
            code_hits = 0
            caption_hits = 0
            has_table_caption = False
            for ln in lines:
                ln_stripped = ln.strip()
                if not ln_stripped:
                    continue
                if _CAPTION_LINE_RE.match(ln_stripped):
                    caption_hits += 1
                    if _TABLE_CAPTION_RE.match(ln_stripped):
                        has_table_caption = True
                if _CODE_HINT_RE.match(ln.rstrip()):
                    code_hits += 1
                if _PIPE_ROW_RE.match(ln_stripped):
                    table_rows.append(ln_stripped)
                    continue
                segments = [seg for seg in _WIDE_SPLIT_RE.split(ln_stripped) if seg]
                if len(segments) >= 3:
                    table_rows.append(ln_stripped)
                elif len(segments) == 2 and _MULTICOLUMN_GAP_RE.search(ln_stripped):
                    two_col_rows.append(ln_stripped)
                    max_two_col_words = max(
                        max_two_col_words,
                        *(len(seg.split()) for seg in segments),
                    )

            cid_chars = sum(len(m.group(0)) for m in _CID_GARBLE_RE.finditer(stripped))
            garbled_chars = sum(1 for ch in stripped if ch == "\ufffd") + cid_chars
            high_garble = len(stripped) > 0 and (garbled_chars / len(stripped)) > 0.15
            low_text = len(stripped) < 20
            bordered_two_col_table = (
                len(two_col_rows) >= 2
                and has_vectors
                and (has_table_caption or max_two_col_words <= 4)
            )
            if bordered_two_col_table and not table_rows:
                table_rows = two_col_rows
            suspected_table = (
                len(table_rows) >= 2
                or (has_table_caption and has_vectors)
                or bordered_two_col_table
            )
            suspected_multicolumn = len(two_col_rows) >= 2 and not suspected_table
            suspected_code = code_hits >= 2
            suspected_figure = image_count > 0 or has_vectors or caption_hits > 0

            triage_pages.append(
                _TriagePage(
                    page_no=page_no,
                    page_width=page_width,
                    page_height=page_height,
                    page_text=page_text,
                    image_count=image_count,
                    has_vectors=has_vectors,
                    page_error=page_error,
                    limit_reached=limit_reached,
                    layout_fell_back=layout_fell_back,
                    high_garble=high_garble,
                    low_text=low_text,
                    suspected_table=suspected_table,
                    suspected_multicolumn=suspected_multicolumn,
                    suspected_code=suspected_code,
                    suspected_figure=suspected_figure,
                    top_template=top_tmpl,
                    bottom_template=bot_tmpl,
                    raw_table_rows=tuple(table_rows),
                )
            )

        min_repeat = 2 if page_count >= 2 else 999
        repeated_headers = frozenset(k for k, v in top_counts.items() if v >= min_repeat)
        repeated_footers = frozenset(k for k, v in bot_counts.items() if v >= min_repeat)
        return triage_pages, total_raw_chars, repeated_headers, repeated_footers

    def _emit_spatial_page(
        self,
        state: _PdfAssemblyState,
        *,
        triage: _TriagePage,
        spatial: SpatialPageResult,
        page_block_ids: list[str],
        page_table_ids: list[str],
        page_figure_ids: list[str],
        page_formula_ids: list[str],
        page_warn_ids: list[str],
    ) -> int:
        page_no = triage.page_no
        spatial_prov = ExtractionProvenance(
            extractor_id=SPATIAL_EXTRACTOR_ID,
            extractor_version=SPATIAL_EXTRACTOR_VERSION,
            method="layout_aware_pdf",
            confidence=0.92 if spatial.reading_order_reliable else 0.65,
            raw_locator=f"pdfplumber_page={page_no};cols={spatial.column_count}",
        )
        page_norm_chars = 0

        for warn_code, warn_sev, warn_msg in spatial.warnings:
            page_warn_ids.append(
                state.add_warning(warn_code, warn_sev, warn_msg, page_number=page_no)
            )

        for stbl in spatial.tables:
            state.tbl_seq += 1
            tid = f"tbl-{state.tbl_seq}"
            tbl_prov = ExtractionProvenance(
                extractor_id=SPATIAL_EXTRACTOR_ID,
                extractor_version=SPATIAL_EXTRACTOR_VERSION,
                method="table_parser",
                confidence=0.9 if stbl.strategy == "lines" else 0.78,
                raw_locator=f"page={page_no};strategy={stbl.strategy}",
            )
            tbl_text_lines = []
            if stbl.headers:
                tbl_text_lines.append(" | ".join(stbl.headers))
            for row in stbl.rows:
                tbl_text_lines.append(" | ".join(row))
            fallback_txt = "\n".join(tbl_text_lines) if tbl_text_lines else None
            state.tables.append(
                TableBlock(
                    table_id=tid,
                    page_number=page_no,
                    reading_order=state.reading_order,
                    section_id=str(state.current_section["section_id"]),
                    bbox=stbl.bbox,
                    caption=stbl.caption,
                    headers=stbl.headers,
                    rows=stbl.rows,
                    raw_text_fallback=fallback_txt[:4000] if fallback_txt else None,
                    extraction_state=stbl.extraction_state,  # type: ignore[arg-type]
                    provenance=tbl_prov,
                )
            )
            page_table_ids.append(tid)
            state.current_section["table_ids"].append(tid)  # type: ignore[attr-defined]
            if fallback_txt:
                n_start, n_end = state.append_norm_piece(fallback_txt)
                state.sections_work[0]["norm_end"] = state.norm_cursor
                state.current_section["norm_end"] = state.norm_cursor
                page_norm_chars += len(fallback_txt)
                state.evidence_refs.append(
                    EvidenceReference(
                        evidence_id=f"ev-{len(state.evidence_refs) + 1}",
                        source_id=state.source_id,
                        version_id=state.version_id,
                        source_sha256=state.source_sha256,
                        section_id=str(state.current_section["section_id"]),
                        block_id=None,
                        page_number=page_no,
                        bbox=stbl.bbox,
                        span=SourcePositionSpan(
                            source_start_offset=0,
                            source_end_offset=0,
                            normalized_start_offset=n_start,
                            normalized_end_offset=n_end,
                        ),
                        excerpt=fallback_txt[:1000],
                    )
                )
            state.reading_order += 1

        for sfig in spatial.figures:
            fid = self._append_deferred_figure(
                state,
                page_no=page_no,
                bbox=sfig.bbox,
                caption=sfig.caption,
                kind=sfig.kind,
                image_count=triage.image_count or None,
                extractor_id=SPATIAL_EXTRACTOR_ID,
                extractor_version=SPATIAL_EXTRACTOR_VERSION,
                raw_locator=f"page={page_no};kind={sfig.kind}",
            )
            page_figure_ids.append(fid)
            page_warn_ids.append(
                state.add_warning(
                    "visual_content_detected",
                    "info",
                    f"Page {page_no} contains {sfig.kind} region; visual interpretation remains deferred.",
                    page_number=page_no,
                    deferred_stage="batch_2_4_multimodal_visual",
                )
            )

        for sblk in spatial.blocks:
            if sblk.kind == "heading":
                h_level = sblk.heading_level or 2
                h_title = sblk.text[:300]
                sec_id, n_start, n_end = state.open_section(
                    title=h_title, level=h_level, page_no=page_no, hierarchical=False
                )
                page_norm_chars += state.emit_block(
                    kind="heading",
                    page_number=page_no,
                    raw_text=sblk.raw_text,
                    normalized_text=h_title,
                    provenance=spatial_prov,
                    bbox=sblk.bbox,
                    section_id=sec_id,
                    heading_level=sblk.heading_level,
                    pre_norm_span=(n_start, n_end),
                    page_block_ids=page_block_ids,
                    page_formula_ids=page_formula_ids,
                    extract_formulas=True,
                )
            else:
                page_norm_chars += state.emit_block(
                    kind=sblk.kind,
                    page_number=page_no,
                    raw_text=sblk.raw_text,
                    normalized_text=sblk.text,
                    provenance=spatial_prov,
                    bbox=sblk.bbox,
                    page_block_ids=page_block_ids,
                    page_formula_ids=page_formula_ids,
                    extract_formulas=True,
                )

        return page_norm_chars

    @staticmethod
    def _append_deferred_figure(
        state: _PdfAssemblyState,
        *,
        page_no: int,
        kind: str,
        image_count: int | None,
        extractor_id: str,
        extractor_version: str,
        raw_locator: str,
        bbox: BoundingBox | None = None,
        caption: str | None = None,
        increment_order: bool = True,
    ) -> str:
        state.fig_seq += 1
        fid = f"fig-{state.fig_seq}"
        interp = "caption_only" if caption else "not_interpreted_visual_deferred"
        state.figures.append(
            FigureReference(
                figure_id=fid,
                page_number=page_no,
                reading_order=state.reading_order,
                section_id=str(state.current_section["section_id"]),
                bbox=bbox,
                caption=caption,
                kind=kind,  # type: ignore[arg-type]
                image_count_on_page=image_count,
                alt_text_or_description=None,
                interpretation_status=interp,  # type: ignore[arg-type]
                provenance=ExtractionProvenance(
                    extractor_id=extractor_id,
                    extractor_version=extractor_version,
                    method="deferred",
                    raw_locator=raw_locator,
                ),
            )
        )
        state.current_section["figure_ids"].append(fid)  # type: ignore[attr-defined]
        if increment_order:
            state.reading_order += 1
        return fid

    @staticmethod
    def _append_unparsed_table(
        state: _PdfAssemblyState,
        *,
        page_no: int,
        raw_text: str | None,
        raw_locator: str,
        warn_msg: str,
        page_table_ids: list[str],
        page_warn_ids: list[str],
        increment_order: bool = False,
    ) -> None:
        state.tbl_seq += 1
        tid = f"tbl-{state.tbl_seq}"
        state.tables.append(
            TableBlock(
                table_id=tid,
                page_number=page_no,
                reading_order=state.reading_order,
                section_id=str(state.current_section["section_id"]),
                raw_text_fallback=raw_text[:4000] if raw_text else None,
                extraction_state="detected_unparsed",
                provenance=ExtractionProvenance(
                    extractor_id=PYPDF_EXTRACTOR_ID,
                    extractor_version=PYPDF_EXTRACTOR_VERSION,
                    method="deferred",
                    raw_locator=raw_locator,
                ),
            )
        )
        page_table_ids.append(tid)
        state.current_section["table_ids"].append(tid)  # type: ignore[attr-defined]
        if increment_order:
            state.reading_order += 1
        state.reading_order_verified = False
        page_warn_ids.append(
            state.add_warning(
                "table_extraction_required",
                "warning",
                warn_msg,
                page_number=page_no,
                deferred_stage="batch_2_3_table_parser",
            )
        )

    @staticmethod
    def _append_ocr_warnings(
        state: _PdfAssemblyState,
        *,
        page_no: int,
        ocr_res: OcrPageResult,
        page_warn_ids: list[str] | None = None,
        is_image: bool = False,
    ) -> None:
        info_msg = (
            f"Printed-text OCR applied via {ocr_res.engine_id} "
            f"(mean confidence {ocr_res.mean_confidence:.2f}, preprocess={ocr_res.preprocess_applied}); "
            "visual/diagram and handwriting understanding remain deferred."
            if is_image
            else f"Page {page_no} processed with {ocr_res.engine_id} "
            f"(mean confidence {ocr_res.mean_confidence:.2f}, preprocess={ocr_res.preprocess_applied})."
        )
        w1 = state.add_warning("ocr_fallback_applied", "info", info_msg, page_number=page_no)
        if page_warn_ids is not None:
            page_warn_ids.append(w1)
        if not ocr_res.lines or ocr_res.mean_confidence < OCR_UNSUPPORTED_OR_HANDWRITING_THRESHOLD:
            w2 = state.add_warning(
                "unsupported_script_or_handwriting",
                "warning",
                f"Page {page_no} OCR yielded empty or very low confidence output; handwritten notes, Hindi/Indic scripts, or pure diagrams are not reliably supported by printed-text OCR.",
                page_number=page_no,
            )
            if page_warn_ids is not None:
                page_warn_ids.append(w2)
        elif ocr_res.mean_confidence < OCR_LOW_CONFIDENCE_THRESHOLD:
            w3 = state.add_warning(
                "low_confidence_ocr",
                "warning",
                f"Page {page_no} OCR confidence ({ocr_res.mean_confidence:.2f}) is low; review extracted text carefully.",
                page_number=page_no,
            )
            if page_warn_ids is not None:
                page_warn_ids.append(w3)

    def _emit_ocr_page(
        self,
        state: _PdfAssemblyState,
        *,
        triage: _TriagePage,
        ocr_res: OcrPageResult,
        page_block_ids: list[str],
        page_figure_ids: list[str],
        page_formula_ids: list[str],
        page_warn_ids: list[str],
    ) -> int:
        page_no = triage.page_no
        pw = float(triage.page_width or 612.0)
        ph = float(triage.page_height or 792.0)
        ocr_prov = ExtractionProvenance(
            extractor_id=ocr_res.engine_id,
            extractor_version=ocr_res.engine_version,
            method="ocr",
            confidence=ocr_res.mean_confidence,
            raw_locator=f"page={page_no};ocr_dpi={self.ocr_dpi}",
        )
        if triage.image_count > 0:
            page_figure_ids.append(
                self._append_deferred_figure(
                    state,
                    page_no=page_no,
                    bbox=BoundingBox(
                        x0=0.0,
                        y0=0.0,
                        x1=round(pw, 2),
                        y1=round(ph, 2),
                        coordinate_space="pdf_points",
                        page_width=round(pw, 2),
                        page_height=round(ph, 2),
                    ),
                    kind="raster_image",
                    image_count=triage.image_count,
                    extractor_id=ocr_res.engine_id,
                    extractor_version=ocr_res.engine_version,
                    raw_locator=f"page={page_no};images={triage.image_count}",
                )
            )

        page_norm_chars = 0
        for ln in ocr_res.lines:
            bbox = _scale_ocr_bbox_to_pdf(
                ln.x0, ln.y0, ln.x1, ln.y1,
                ocr_res.image_width, ocr_res.image_height, pw, ph,
            )
            page_norm_chars += state.emit_block(
                kind="paragraph",
                page_number=page_no,
                raw_text=ln.text,
                normalized_text=ln.text,
                provenance=ocr_prov,
                bbox=bbox,
                page_block_ids=page_block_ids,
                page_formula_ids=page_formula_ids,
                extract_formulas=True,
            )

        self._append_ocr_warnings(
            state, page_no=page_no, ocr_res=ocr_res, page_warn_ids=page_warn_ids
        )
        return page_norm_chars

    def _run_spatial_fallbacks(
        self,
        *,
        spatial_targets: list[int],
        ocr_targets: list[int],
        triage_pages: list[_TriagePage],
        repeated_headers: frozenset[str],
        repeated_footers: frozenset[str],
        raw_bytes: bytes,
        page_count: int,
        started_at: float,
        progress_callback: Callable[[str, dict[str, str | int | float | bool]], None]
        | None,
        cancel_check: Callable[[], bool] | None,
    ) -> tuple[dict[int, SpatialPageResult], set[int]]:
        spatial_results: dict[int, SpatialPageResult] = {}
        if not spatial_targets:
            return spatial_results, set()
        capped_spatial = spatial_targets[: self.max_fallback_pages]
        spatial_overflow = set(spatial_targets[self.max_fallback_pages :])
        if progress_callback is not None:
            progress_callback(
                "spatial_layout",
                {
                    "total_pages": page_count,
                    "spatial_pages": len(capped_spatial),
                    "ocr_pages": len(ocr_targets),
                },
            )
        if self.page_workers > 1 and (
            len(capped_spatial) >= 4 or self.isolate_untrusted_parse
        ):
            tasks = [
                (
                    raw_bytes,
                    idx,
                    repeated_headers,
                    repeated_footers,
                    self.enable_table_extraction,
                    triage_pages[idx].suspected_table,
                )
                for idx in capped_spatial
            ]
            pool = self._get_or_create_pool(
                warm_ocr=bool(
                    ocr_targets
                    and isinstance(self.ocr_backend, RapidOcrBackend)
                    and self.ocr_backend.is_available()
                )
            )
            for idx, res, route_to_ocr in _run_bounded_process_tasks(
                fn=_pool_run_spatial_page,
                tasks=tasks,
                executor=pool,
                timeout_seconds=self.task_timeout_seconds,
                cancel_check=cancel_check,
                on_abort=self._invalidate_pool,
            ):
                if route_to_ocr:
                    ocr_targets.append(idx)
                else:
                    spatial_results[idx] = res
            return spatial_results, spatial_overflow

        with pdfplumber.open(io.BytesIO(raw_bytes)) as plumber_pdf:
            for idx in capped_spatial:
                if cancel_check is not None and cancel_check():
                    raise ExtractionCancelledError("Extraction cancelled during spatial layout")
                if (time.monotonic() - started_at) > self.task_timeout_seconds:
                    raise AccessError(
                        422,
                        "extraction_timeout",
                        "Spatial PDF layout reconstruction exceeded CPU time budget",
                    )
                res = analyze_page_spatial(
                    plumber_pdf.pages[idx],
                    repeated_header_templates=repeated_headers,
                    repeated_footer_templates=repeated_footers,
                    extract_tables=self.enable_table_extraction,
                    allow_borderless_tables=triage_pages[idx].suspected_table,
                )
                if (
                    not res.blocks
                    and not res.tables
                    and any(f.kind == "raster_image" for f in res.figures)
                ):
                    ocr_targets.append(idx)
                else:
                    spatial_results[idx] = res
        return spatial_results, spatial_overflow

    def _run_ocr_fallbacks(
        self,
        *,
        ocr_targets: list[int],
        spatial_count: int,
        raw_bytes: bytes,
        page_count: int,
        started_at: float,
        progress_callback: Callable[[str, dict[str, str | int | float | bool]], None]
        | None,
        cancel_check: Callable[[], bool] | None,
        page_ocr_cache_lookup: Callable[[str], OcrPageResult | None] | None,
        page_ocr_cache_store: Callable[[str, OcrPageResult], None] | None,
    ) -> tuple[dict[int, OcrPageResult], set[int], int]:
        ocr_results: dict[int, OcrPageResult] = {}
        ocr_overflow: set[int] = set()
        page_cache_hits = 0
        ocr_ready = self.ocr_backend is not None and self.ocr_backend.is_available()
        if not (ocr_targets and ocr_ready and self.ocr_backend is not None):
            return ocr_results, ocr_overflow, page_cache_hits

        ocr_ordered = sorted(set(ocr_targets))
        capped_ocr = ocr_ordered[: self.max_ocr_pages]
        ocr_overflow = set(ocr_ordered[self.max_ocr_pages :])
        if progress_callback is not None:
            progress_callback(
                "ocr",
                {
                    "total_pages": page_count,
                    "spatial_pages": spatial_count,
                    "ocr_pages": len(capped_ocr),
                },
            )
        remaining_ocr: list[tuple[int, str | None]] = []
        for idx in capped_ocr:
            if cancel_check is not None and cancel_check():
                raise ExtractionCancelledError("Extraction cancelled before OCR")
            if page_ocr_cache_lookup is not None:
                img = render_pdf_page_to_rgb(raw_bytes, idx, dpi=self.ocr_dpi)
                img_sha = hash_pil_image(img)
                cached_page = page_ocr_cache_lookup(img_sha)
                if cached_page is not None:
                    ocr_results[idx] = cached_page
                    page_cache_hits += 1
                    continue
                remaining_ocr.append((idx, img_sha))
            else:
                remaining_ocr.append((idx, None))

        if (
            remaining_ocr
            and self.page_workers > 1
            and (len(remaining_ocr) >= 2 or self.isolate_untrusted_parse)
            and isinstance(self.ocr_backend, RapidOcrBackend)
        ):
            tasks = [
                (
                    raw_bytes,
                    idx,
                    self.ocr_dpi,
                    self.preprocess_for_ocr,
                    self.enable_adaptive_ocr_preprocess,
                )
                for idx, _ in remaining_ocr
            ]
            sha_by_idx = dict(remaining_ocr)
            pool = self._get_or_create_pool(warm_ocr=True)
            for idx, ocr_res in _run_bounded_process_tasks(
                fn=_pool_run_ocr_page,
                tasks=tasks,
                executor=pool,
                timeout_seconds=self.task_timeout_seconds,
                cancel_check=cancel_check,
                on_abort=self._invalidate_pool,
            ):
                ocr_results[idx] = ocr_res
                if page_ocr_cache_store is not None:
                    img_sha = sha_by_idx.get(idx)
                    if img_sha is None:
                        img_sha = hash_pil_image(
                            render_pdf_page_to_rgb(raw_bytes, idx, dpi=self.ocr_dpi)
                        )
                    page_ocr_cache_store(img_sha, ocr_res)
        else:
            for idx, img_sha in remaining_ocr:
                if cancel_check is not None and cancel_check():
                    raise ExtractionCancelledError("Extraction cancelled during OCR")
                if (time.monotonic() - started_at) > max(
                    self.task_timeout_seconds, MAX_PDF_EXTRACTION_SECONDS * 2.5
                ):
                    ocr_overflow.add(idx)
                    continue
                img = render_pdf_page_to_rgb(raw_bytes, idx, dpi=self.ocr_dpi)
                ocr_res = self._run_ocr_on_image(img)
                ocr_results[idx] = ocr_res
                if page_ocr_cache_store is not None:
                    page_ocr_cache_store(img_sha or hash_pil_image(img), ocr_res)
        return ocr_results, ocr_overflow, page_cache_hits

    def _assemble_ocr_doc_page(
        self,
        state: _PdfAssemblyState,
        *,
        tp: _TriagePage,
        ocr_res: OcrPageResult,
        limits: SourceIngestionLimits,
        total_raw_chars: int,
    ) -> tuple[DocumentPage, int]:
        page_warn_ids: list[str] = []
        page_block_ids: list[str] = []
        page_figure_ids: list[str] = []
        page_formula_ids: list[str] = []
        page_norm_chars = self._emit_ocr_page(
            state,
            triage=tp,
            ocr_res=ocr_res,
            page_block_ids=page_block_ids,
            page_figure_ids=page_figure_ids,
            page_formula_ids=page_formula_ids,
            page_warn_ids=page_warn_ids,
        )
        total_raw_chars += page_norm_chars
        if total_raw_chars > limits.max_text_chars:
            raise AccessError(
                413,
                "text_limit_exceeded",
                f"Extracted PDF text exceeds limit of {limits.max_text_chars} characters",
            )
        reading_reliable = bool(ocr_res.lines) and ocr_res.mean_confidence >= 0.65
        if not reading_reliable:
            state.reading_order_verified = False
        classification: PageClassification = (
            "visual_content" if page_norm_chars >= 20 else "needs_ocr"
        )
        return (
            DocumentPage(
                page_number=tp.page_no,
                width=tp.page_width,
                height=tp.page_height,
                classification=classification,
                recommended_processors=("ocr",) if classification == "needs_ocr" else ("visual",),
                confidence=ocr_res.mean_confidence,
                reading_order_reliable=reading_reliable,
                raw_char_count=page_norm_chars,
                normalized_char_count=page_norm_chars,
                image_count=tp.image_count,
                suspected_multicolumn=False,
                suspected_table=False,
                suspected_formula=bool(page_formula_ids),
                text_block_ids=tuple(page_block_ids),
                table_ids=(),
                figure_ids=tuple(page_figure_ids),
                formula_ids=tuple(page_formula_ids),
                warning_ids=tuple(page_warn_ids),
                provenance=ExtractionProvenance(
                    extractor_id=ocr_res.engine_id,
                    extractor_version=ocr_res.engine_version,
                    method="ocr",
                    confidence=ocr_res.mean_confidence,
                    raw_locator=f"page={tp.page_no}",
                ),
            ),
            total_raw_chars,
        )

    def _assemble_spatial_doc_page(
        self,
        state: _PdfAssemblyState,
        *,
        tp: _TriagePage,
        sp: SpatialPageResult,
    ) -> DocumentPage:
        page_warn_ids: list[str] = []
        page_block_ids: list[str] = []
        page_table_ids: list[str] = []
        page_figure_ids: list[str] = []
        page_formula_ids: list[str] = []
        recommended: list[RecommendedProcessor] = []
        page_no = tp.page_no
        page_norm_chars = self._emit_spatial_page(
            state,
            triage=tp,
            spatial=sp,
            page_block_ids=page_block_ids,
            page_table_ids=page_table_ids,
            page_figure_ids=page_figure_ids,
            page_formula_ids=page_formula_ids,
            page_warn_ids=page_warn_ids,
        )
        unparsed_table_fallback = not sp.tables and tp.suspected_table and bool(tp.raw_table_rows)
        if unparsed_table_fallback:
            self._append_unparsed_table(
                state,
                page_no=page_no,
                raw_text="\n".join(tp.raw_table_rows),
                raw_locator=f"page={page_no};table_heuristic_fallback",
                warn_msg=f"Page {page_no} appears to contain tabular data; structured cell extraction fell back to raw text.",
                page_table_ids=page_table_ids,
                page_warn_ids=page_warn_ids,
                increment_order=True,
            )
        if not sp.reading_order_reliable:
            state.reading_order_verified = False
        has_page_table = bool(sp.tables) or unparsed_table_fallback
        if sp.suspected_multicolumn:
            classification: PageClassification = "needs_layout"
            recommended.append("layout")
        elif has_page_table:
            classification = "needs_table"
            recommended.append("table")
        elif sp.figures:
            classification = "visual_content"
            recommended.append("visual")
        else:
            classification = "digital_text"
        if sp.figures and "visual" not in recommended:
            recommended.append("visual")
        return DocumentPage(
            page_number=page_no,
            width=sp.width,
            height=sp.height,
            classification=classification,
            recommended_processors=tuple(recommended),
            confidence=0.92 if sp.reading_order_reliable else 0.65,
            reading_order_reliable=sp.reading_order_reliable,
            raw_char_count=len(tp.page_text),
            normalized_char_count=page_norm_chars,
            image_count=tp.image_count,
            suspected_multicolumn=sp.suspected_multicolumn,
            suspected_table=has_page_table,
            suspected_formula=bool(page_formula_ids),
            text_block_ids=tuple(page_block_ids),
            table_ids=tuple(page_table_ids),
            figure_ids=tuple(page_figure_ids),
            formula_ids=tuple(page_formula_ids),
            warning_ids=tuple(page_warn_ids),
            provenance=ExtractionProvenance(
                extractor_id=SPATIAL_EXTRACTOR_ID,
                extractor_version=SPATIAL_EXTRACTOR_VERSION,
                method="layout_aware_pdf",
                confidence=0.92 if sp.reading_order_reliable else 0.65,
                raw_locator=f"page={page_no};cols={sp.column_count}",
            ),
        )

    def _assemble_fast_doc_page(
        self,
        state: _PdfAssemblyState,
        *,
        idx: int,
        tp: _TriagePage,
        spatial_overflow: set[int],
        ocr_overflow: set[int],
        ocr_ready: bool,
        repeated_headers: frozenset[str],
        repeated_footers: frozenset[str],
    ) -> DocumentPage:
        page_no = tp.page_no
        page_warn_ids: list[str] = []
        page_block_ids: list[str] = []
        page_table_ids: list[str] = []
        page_figure_ids: list[str] = []
        page_formula_ids: list[str] = []
        recommended: list[RecommendedProcessor] = []
        fast_prov = ExtractionProvenance(
            extractor_id=PYPDF_EXTRACTOR_ID,
            extractor_version=PYPDF_EXTRACTOR_VERSION,
            method="digital_pdf_text",
            confidence=None,
            raw_locator=f"pypdf_page={page_no}",
        )
        if tp.limit_reached:
            page_warn_ids.append(
                state.add_warning(
                    "partial_page_error",
                    "warning",
                    f"Page {page_no} exceeded bounded PDF decompression/work limit.",
                    page_number=page_no,
                    deferred_stage="batch_2_4_layout_ocr",
                )
            )
        elif tp.page_error:
            page_warn_ids.append(
                state.add_warning(
                    "partial_page_error",
                    "warning",
                    f"Page {page_no} content stream failed during fast pypdf text extraction.",
                    page_number=page_no,
                    deferred_stage="batch_2_4_layout_ocr",
                )
            )
        elif tp.layout_fell_back:
            page_warn_ids.append(
                state.add_warning(
                    "reading_order_uncertain",
                    "warning",
                    f"Page {page_no} layout mode fell back to plain pypdf stream; reading order may be unreliable.",
                    page_number=page_no,
                    deferred_stage="batch_2_3_layout_aware",
                )
            )

        if idx in spatial_overflow:
            page_warn_ids.append(
                state.add_warning(
                    "truncation_warning",
                    "warning",
                    f"Page {page_no} exceeded max_fallback_pages={self.max_fallback_pages} budget; retained fast-path text.",
                    page_number=page_no,
                )
            )

        has_images = tp.image_count > 0
        if has_images:
            page_figure_ids.append(
                self._append_deferred_figure(
                    state,
                    page_no=page_no,
                    kind="raster_image",
                    image_count=tp.image_count,
                    extractor_id=PYPDF_EXTRACTOR_ID,
                    extractor_version=PYPDF_EXTRACTOR_VERSION,
                    raw_locator=f"page={page_no};images={tp.image_count}",
                    increment_order=False,
                )
            )
            recommended.append("visual")
            page_warn_ids.append(
                state.add_warning(
                    "visual_content_detected",
                    "info",
                    f"Page {page_no} contains {tp.image_count} embedded image(s); visual interpretation deferred.",
                    page_number=page_no,
                    deferred_stage="batch_2_4_multimodal_visual",
                )
            )

        if tp.suspected_table:
            recommended.append("table")
            self._append_unparsed_table(
                state,
                page_no=page_no,
                raw_text="\n".join(tp.raw_table_rows) if tp.raw_table_rows else None,
                raw_locator=f"page={page_no};table_heuristic",
                warn_msg=f"Page {page_no} appears to contain tabular data; structured table parsing deferred.",
                page_table_ids=page_table_ids,
                page_warn_ids=page_warn_ids,
            )

        if tp.suspected_multicolumn:
            recommended.append("layout")
            state.reading_order_verified = False
            page_warn_ids.append(
                state.add_warning(
                    "layout_reconstruction_required",
                    "warning",
                    f"Page {page_no} exhibits wide horizontal column gaps; pypdf reading order is unreliable.",
                    page_number=page_no,
                    deferred_stage="batch_2_3_layout_aware",
                )
            )

        if tp.low_text and has_images:
            classification: PageClassification = "needs_ocr"
            if "ocr" not in recommended:
                recommended.insert(0, "ocr")
            confidence = 0.25
            reading_order_reliable = False
            state.reading_order_verified = False
            warn_code = (
                "ocr_unavailable"
                if (self.ocr_backend is not None and not ocr_ready)
                else "ocr_required"
            )
            msg = (
                f"Page {page_no} exceeded max_ocr_pages={self.max_ocr_pages} OCR budget."
                if idx in ocr_overflow
                else f"Page {page_no} contains embedded image(s) with minimal extractable text; printed-text OCR required."
            )
            page_warn_ids.append(
                state.add_warning(
                    warn_code,
                    "blocking_for_auto_publish",
                    msg,
                    page_number=page_no,
                    deferred_stage="batch_2_4_ocr_worker",
                )
            )
        elif tp.page_error or tp.high_garble or (tp.low_text and not has_images):
            classification = "uncertain"
            if "ocr" not in recommended:
                recommended.append("ocr")
            confidence = 0.2 if (tp.page_error or tp.high_garble) else 0.4
            reading_order_reliable = False
            state.reading_order_verified = False
            page_warn_ids.append(
                state.add_warning(
                    "blank_or_low_text_page",
                    "warning",
                    f"Page {page_no} yielded little or garbled extractable text; classification is uncertain.",
                    page_number=page_no,
                    deferred_stage="batch_2_4_ocr_worker",
                )
            )
        elif tp.suspected_multicolumn:
            classification = "needs_layout"
            confidence = 0.55
            reading_order_reliable = False
        elif tp.suspected_table:
            classification = "needs_table"
            confidence = 0.65
            reading_order_reliable = False
        elif has_images:
            classification = "visual_content"
            confidence = 0.75
            reading_order_reliable = not tp.suspected_multicolumn
        else:
            classification = "digital_text"
            confidence = 0.9
            reading_order_reliable = True

        page_norm_chars = 0
        page_cursor = 0
        paragraph_buf: list[tuple[int, int, int, str]] = []
        hf_lines_on_page: list[tuple[int, int, int, str]] = []
        non_empty_indices = [
            i
            for i, raw_ln in enumerate(tp.page_text.splitlines())
            if _normalize_line(raw_ln)
        ]
        first_ne_idx = non_empty_indices[0] if non_empty_indices else -1
        last_ne_idx = non_empty_indices[-1] if non_empty_indices else -1

        for line_idx, raw_ln in enumerate(tp.page_text.splitlines(keepends=True)):
            line_no = line_idx + 1
            stripped_ln = raw_ln.rstrip("\r\n")
            l_start = page_cursor
            l_end = page_cursor + len(stripped_ln)
            page_cursor += len(raw_ln)
            norm_ln = _normalize_line(stripped_ln)
            if not norm_ln:
                page_norm_chars += self._commit_pdf_paragraph(
                    state,
                    paragraph_buf=paragraph_buf,
                    page_text=tp.page_text,
                    page_no=page_no,
                    page_block_ids=page_block_ids,
                    page_formula_ids=page_formula_ids,
                    page_warn_ids=page_warn_ids,
                )
                continue
            tmpl = normalize_header_footer_template(norm_ln)
            if (line_idx == first_ne_idx and tmpl in repeated_headers) or (
                line_idx == last_ne_idx and tmpl in repeated_footers
            ):
                page_norm_chars += self._commit_pdf_paragraph(
                    state,
                    paragraph_buf=paragraph_buf,
                    page_text=tp.page_text,
                    page_no=page_no,
                    page_block_ids=page_block_ids,
                    page_formula_ids=page_formula_ids,
                    page_warn_ids=page_warn_ids,
                )
                hf_lines_on_page.append((l_start, l_end, line_no, stripped_ln))
                continue
            num_match = _NUMBERED_HEADING_RE.match(norm_ln)
            md_match = _MARKDOWN_HEADING_RE.match(norm_ln)
            if md_match or num_match:
                page_norm_chars += self._commit_pdf_paragraph(
                    state,
                    paragraph_buf=paragraph_buf,
                    page_text=tp.page_text,
                    page_no=page_no,
                    page_block_ids=page_block_ids,
                    page_formula_ids=page_formula_ids,
                    page_warn_ids=page_warn_ids,
                )
                h_level = len(md_match.group(1)) if md_match else 2
                h_title = (md_match.group(2) if md_match else norm_ln)[:300]
                sec_id, n_start, n_end = state.open_section(
                    title=h_title, level=h_level, page_no=page_no, hierarchical=False
                )
                page_norm_chars += state.emit_block(
                    kind="heading",
                    page_number=page_no,
                    raw_text=stripped_ln,
                    normalized_text=h_title,
                    provenance=fast_prov,
                    section_id=sec_id,
                    heading_level=min(h_level, 6),
                    source_start_offset=l_start,
                    source_end_offset=l_end,
                    source_line_start=line_no,
                    source_line_end=line_no,
                    pre_norm_span=(n_start, n_end),
                    page_block_ids=page_block_ids,
                )
                continue
            paragraph_buf.append((l_start, l_end, line_no, stripped_ln))

        page_norm_chars += self._commit_pdf_paragraph(
            state,
            paragraph_buf=paragraph_buf,
            page_text=tp.page_text,
            page_no=page_no,
            page_block_ids=page_block_ids,
            page_formula_ids=page_formula_ids,
            page_warn_ids=page_warn_ids,
        )
        for hf_start, hf_end, hf_lno, hf_raw in hf_lines_on_page:
            page_norm_chars += state.emit_block(
                kind="header_or_footer",
                page_number=page_no,
                raw_text=hf_raw,
                normalized_text=_normalize_line(hf_raw),
                provenance=fast_prov,
                source_start_offset=hf_start,
                source_end_offset=hf_end,
                source_line_start=hf_lno,
                source_line_end=hf_lno,
                page_block_ids=page_block_ids,
            )
        return DocumentPage(
            page_number=page_no,
            width=tp.page_width,
            height=tp.page_height,
            classification=classification,
            recommended_processors=tuple(recommended),
            confidence=confidence,
            reading_order_reliable=reading_order_reliable,
            raw_char_count=len(tp.page_text),
            normalized_char_count=page_norm_chars,
            image_count=tp.image_count,
            suspected_multicolumn=tp.suspected_multicolumn,
            suspected_table=tp.suspected_table,
            suspected_formula=bool(page_formula_ids),
            text_block_ids=tuple(page_block_ids),
            table_ids=tuple(page_table_ids),
            figure_ids=tuple(page_figure_ids),
            formula_ids=tuple(page_formula_ids),
            warning_ids=tuple(page_warn_ids),
            provenance=fast_prov,
        )

    def _extract_digital_pdf_pages(
        self,
        *,
        reader: pypdf.PdfReader | None,
        page_count: int,
        started_at: float,
        tenant_id: UUID,
        source_id: UUID,
        version_id: UUID,
        source_version: int,
        source_sha256: str,
        input_format: SourceDocumentFormat,
        title: str,
        raw_bytes: bytes,
        limits: SourceIngestionLimits,
        progress_callback: Callable[[str, dict[str, str | int | float | bool]], None]
        | None = None,
        cancel_check: Callable[[], bool] | None = None,
        page_ocr_cache_lookup: Callable[[str], OcrPageResult | None] | None = None,
        page_ocr_cache_store: Callable[[str, OcrPageResult], None] | None = None,
        precomputed_triage: tuple[
            list[_TriagePage], int, frozenset[str], frozenset[str]
        ]
        | None = None,
    ) -> ExtractedDocument:
        if precomputed_triage is not None:
            triage_pages, total_raw_chars, repeated_headers, repeated_footers = (
                precomputed_triage
            )
        else:
            assert reader is not None
            if progress_callback is not None:
                progress_callback("triage", {"total_pages": page_count, "completed_pages": 0})
            triage_pages, total_raw_chars, repeated_headers, repeated_footers = (
                self._triage_pdf_pages(
                    reader=reader,
                    page_count=page_count,
                    started_at=started_at,
                    limits=limits,
                )
            )
        if cancel_check is not None and cancel_check():
            raise ExtractionCancelledError("Extraction cancelled after triage")

        spatial_targets: list[int] = []
        ocr_targets: list[int] = []
        for idx, tp in enumerate(triage_pages):
            has_images = tp.image_count > 0
            if (tp.low_text and has_images) or tp.page_error or tp.high_garble or (tp.low_text and not has_images):
                ocr_targets.append(idx)
            elif self.enable_layout_fallback and (
                tp.suspected_table or tp.suspected_multicolumn or tp.suspected_code or tp.suspected_figure
            ):
                spatial_targets.append(idx)

        spatial_results, spatial_overflow = self._run_spatial_fallbacks(
            spatial_targets=spatial_targets,
            ocr_targets=ocr_targets,
            triage_pages=triage_pages,
            repeated_headers=repeated_headers,
            repeated_footers=repeated_footers,
            raw_bytes=raw_bytes,
            page_count=page_count,
            started_at=started_at,
            progress_callback=progress_callback,
            cancel_check=cancel_check,
        )
        ocr_results, ocr_overflow, page_cache_hits = self._run_ocr_fallbacks(
            ocr_targets=ocr_targets,
            spatial_count=len(spatial_results),
            raw_bytes=raw_bytes,
            page_count=page_count,
            started_at=started_at,
            progress_callback=progress_callback,
            cancel_check=cancel_check,
            page_ocr_cache_lookup=page_ocr_cache_lookup,
            page_ocr_cache_store=page_ocr_cache_store,
        )
        ocr_ready = self.ocr_backend is not None and self.ocr_backend.is_available()

        if progress_callback is not None:
            progress_callback(
                "assembling",
                {
                    "total_pages": page_count,
                    "completed_pages": page_count,
                    "spatial_pages": len(spatial_results),
                    "ocr_pages": len(ocr_results),
                    "page_cache_hits": page_cache_hits,
                },
            )

        if ocr_results and not spatial_results:
            doc_prov = ExtractionProvenance(
                extractor_id=ADAPTIVE_EXTRACTOR_ID,
                extractor_version=ADAPTIVE_EXTRACTOR_VERSION,
                method="ocr",
                confidence=None,
                raw_locator=f"adaptive_pages={page_count};ocr={len(ocr_results)}",
            )
        elif spatial_results or ocr_results:
            doc_prov = ExtractionProvenance(
                extractor_id=ADAPTIVE_EXTRACTOR_ID,
                extractor_version=ADAPTIVE_EXTRACTOR_VERSION,
                method="layout_aware_pdf",
                confidence=None,
                raw_locator=(
                    f"adaptive_pages={page_count};spatial={len(spatial_results)};ocr={len(ocr_results)}"
                ),
            )
        else:
            doc_prov = ExtractionProvenance(
                extractor_id=PYPDF_EXTRACTOR_ID,
                extractor_version=PYPDF_EXTRACTOR_VERSION,
                method="digital_pdf_text",
                confidence=None,
                raw_locator=f"pypdf_pages={page_count}",
            )

        state = _PdfAssemblyState(
            source_id=source_id,
            version_id=version_id,
            source_sha256=source_sha256,
            provenance=doc_prov,
            page_count=page_count,
            title=title,
            source_version=source_version,
        )

        pages: list[DocumentPage] = []
        for idx, tp in enumerate(triage_pages):
            if idx in ocr_results:
                page_doc, total_raw_chars = self._assemble_ocr_doc_page(
                    state,
                    tp=tp,
                    ocr_res=ocr_results[idx],
                    limits=limits,
                    total_raw_chars=total_raw_chars,
                )
            elif idx in spatial_results:
                page_doc = self._assemble_spatial_doc_page(
                    state, tp=tp, sp=spatial_results[idx]
                )
            else:
                page_doc = self._assemble_fast_doc_page(
                    state,
                    idx=idx,
                    tp=tp,
                    spatial_overflow=spatial_overflow,
                    ocr_overflow=ocr_overflow,
                    ocr_ready=ocr_ready,
                    repeated_headers=repeated_headers,
                    repeated_footers=repeated_footers,
                )
            pages.append(page_doc)

        if repeated_headers:
            state.add_warning(
                "repeated_header_footer_suspected",
                "info",
                "Repeated top header detected across pages and isolated as header_or_footer blocks.",
            )
        if repeated_footers:
            state.add_warning(
                "repeated_header_footer_suspected",
                "info",
                "Repeated bottom footer detected across pages and isolated as header_or_footer blocks.",
            )

        notes: list[str] = [
            f"Adaptive extraction completed: fast_path={page_count - len(spatial_results) - len(ocr_results)}, spatial={len(spatial_results)}, ocr={len(ocr_results)}."
        ]
        if not state.reading_order_verified:
            notes.append(
                "One or more pages have uncertain reading order or deferred OCR/visual content."
            )
        return state.build_document(
            tenant_id=tenant_id,
            input_format=input_format,
            pages=tuple(pages),
            total_raw_chars=total_raw_chars,
            limits=limits,
            notes=tuple(notes),
        )
