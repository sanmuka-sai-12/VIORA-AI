"""Hardened input validation, format sniffing, and adaptive extraction routing."""

import contextlib
import hashlib
import io
import re
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import PurePosixPath, PureWindowsPath
from typing import Any
from uuid import UUID

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
    SourcePositionSpan,
    SourceSection,
    TableBlock,
    TextBlock,
)
from .schemas import SourceIngestionLimits, SourceInputFormat, TextInputFormat

NATIVE_EXTRACTOR_ID = "viora.sources.native_text"
NATIVE_EXTRACTOR_VERSION = "1.0"
PYPDF_EXTRACTOR_ID = "viora.sources.pypdf_triage"
PYPDF_EXTRACTOR_VERSION = "1.0"

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
        ".png",
        ".jpg",
        ".jpeg",
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
_CONTROL_CHARS_RE = re.compile(r"[\x00-\x1f\x7f]")

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


def validate_and_sniff_upload(
    *,
    raw_bytes: bytes,
    declared_format: SourceInputFormat | None,
    original_filename: str | None,
    claimed_content_type: str | None,
    limits: SourceIngestionLimits,
) -> tuple[SourceInputFormat, bytes, str, str | None]:
    """Inspect uploaded bytes directly; reject spoofed extensions, MIME mismatches, and bad PDFs."""
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

    if declared_format == "digital_pdf" or ext == ".pdf" or mime == "application/pdf":
        if not is_pdf_bytes:
            raise AccessError(
                415,
                "unsupported_media_type",
                "Claimed PDF input does not contain valid PDF content",
            )
        _validate_pdf_bytes(raw_bytes, limits)
        return (
            "digital_pdf",
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
    if not formulas and _UNICODE_MATH_RE.search(text):
        for line in text.splitlines():
            stripped = line.strip()
            if stripped and _UNICODE_MATH_RE.search(stripped) and stripped not in seen:
                seen.add(stripped)
                idx += 1
                formulas.append(
                    FormulaReference(
                        formula_id=f"frm-{idx}",
                        page_number=page_number,
                        reading_order=start_order,
                        section_id=section_id,
                        raw_representation=stripped[:2000],
                        latex=None,
                        representation_status="raw_text_only",
                        provenance=provenance,
                    )
                )
    return formulas, idx


@dataclass
class _PdfAssemblyState:
    source_id: UUID
    version_id: UUID
    source_sha256: str
    provenance: ExtractionProvenance
    page_count: int
    title: str
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


class AdaptiveDocumentExtractor:
    """Initial adaptive router: native text normalization + fast pypdf digital triage."""

    def extract(
        self,
        *,
        tenant_id: UUID,
        source_id: UUID,
        version_id: UUID,
        source_version: int,
        source_sha256: str,
        input_format: SourceInputFormat,
        title: str,
        raw_bytes: bytes,
        limits: SourceIngestionLimits,
    ) -> ExtractedDocument:
        if input_format == "digital_pdf":
            return self._extract_digital_pdf(
                tenant_id=tenant_id,
                source_id=source_id,
                version_id=version_id,
                source_version=source_version,
                source_sha256=source_sha256,
                title=title,
                raw_bytes=raw_bytes,
                limits=limits,
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
        input_format: SourceInputFormat,
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
        warnings: list[ExtractionWarning] = []
        if has_bom or "\r\n" in decoded:
            warnings.append(
                ExtractionWarning(
                    warning_id="warn-1",
                    code="encoding_normalized",
                    severity="info",
                    message="Normalized line endings and/or UTF-8 BOM without altering text order.",
                    page_number=1,
                )
            )

        line_spans: list[tuple[int, int, int, int, int, str]] = []
        char_pos = 0
        byte_pos = 3 if has_bom else 0
        for line_no, raw_line in enumerate(decoded.splitlines(keepends=True), start=1):
            stripped_nl = raw_line.rstrip("\r\n")
            char_start = char_pos
            char_end = char_pos + len(stripped_nl)
            byte_start = byte_pos
            byte_end = byte_pos + len(stripped_nl.encode("utf-8"))
            line_spans.append(
                (char_start, char_end, byte_start, byte_end, line_no, stripped_nl)
            )
            char_pos += len(raw_line)
            byte_pos += len(raw_line.encode("utf-8"))

        if not line_spans and decoded:
            line_spans.append(
                (0, len(decoded), 0, len(decoded.encode("utf-8")), 1, decoded)
            )

        blocks: list[TextBlock] = []
        sections: list[SourceSection] = []
        formulas: list[FormulaReference] = []
        evidence_refs: list[EvidenceReference] = []
        normalized_parts: list[str] = []
        norm_cursor = 0
        reading_order = 0
        formula_count = 0

        root_section: dict[str, object] = {
            "section_id": "sec-1",
            "parent_section_id": None,
            "title": title,
            "level": 1,
            "reading_order": 0,
            "block_ids": [],
            "formula_ids": [],
            "child_section_ids": [],
            "norm_start": 0,
            "norm_end": 0,
        }
        sections_work: list[dict[str, object]] = [root_section]
        section_stack: list[dict[str, object]] = [root_section]
        sec_seq = 1

        def append_norm_piece(text_piece: str) -> tuple[int, int]:
            nonlocal norm_cursor
            if normalized_parts:
                norm_cursor += 2
            start = norm_cursor
            normalized_parts.append(text_piece)
            norm_cursor += len(text_piece)
            return start, norm_cursor

        idx = 0
        while idx < len(line_spans):
            c_start, c_end, b_start, b_end, l_no, line_txt = line_spans[idx]
            norm_line = _normalize_line(line_txt)
            if not norm_line:
                idx += 1
                continue

            md_match = _MARKDOWN_HEADING_RE.match(norm_line)
            num_match = _NUMBERED_HEADING_RE.match(norm_line) if not md_match else None
            if md_match or num_match:
                h_level = len(md_match.group(1)) if md_match else 2
                h_title = (md_match.group(2) if md_match else norm_line)[:300]
                sec_seq += 1
                while len(section_stack) > 1 and int(section_stack[-1]["level"]) >= h_level:
                    popped = section_stack.pop()
                    popped["norm_end"] = norm_cursor
                parent_id = str(section_stack[-1]["section_id"]) if section_stack else None

                norm_start, norm_end = append_norm_piece(h_title)
                new_sec_id = f"sec-{sec_seq}"
                new_sec: dict[str, object] = {
                    "section_id": new_sec_id,
                    "parent_section_id": parent_id,
                    "title": h_title,
                    "level": min(h_level, 6),
                    "reading_order": reading_order,
                    "block_ids": [],
                    "formula_ids": [],
                    "child_section_ids": [],
                    "norm_start": norm_start,
                    "norm_end": norm_end,
                }
                if section_stack:
                    section_stack[-1]["child_section_ids"].append(new_sec_id)  # type: ignore[attr-defined]
                sections_work.append(new_sec)
                section_stack.append(new_sec)

                block_id = f"blk-{len(blocks) + 1}"
                span = SourcePositionSpan(
                    source_start_offset=c_start,
                    source_end_offset=c_end,
                    source_byte_start=b_start,
                    source_byte_end=b_end,
                    source_line_start=l_no,
                    source_line_end=l_no,
                    normalized_start_offset=norm_start,
                    normalized_end_offset=norm_end,
                )
                blocks.append(
                    TextBlock(
                        block_id=block_id,
                        kind="heading",
                        page_number=1,
                        reading_order=reading_order,
                        section_id=new_sec_id,
                        raw_text=line_txt,
                        normalized_text=h_title,
                        span=span,
                        heading_level=min(h_level, 6),
                        provenance=provenance,
                    )
                )
                new_sec["block_ids"].append(block_id)  # type: ignore[attr-defined]
                evidence_refs.append(
                    EvidenceReference(
                        evidence_id=f"ev-{len(evidence_refs) + 1}",
                        source_id=source_id,
                        version_id=version_id,
                        source_sha256=source_sha256,
                        section_id=new_sec_id,
                        block_id=block_id,
                        page_number=1,
                        span=span,
                        excerpt=h_title[:1000],
                    )
                )
                reading_order += 1
                idx += 1
                continue

            para_lines: list[tuple[int, int, int, int, int, str]] = []
            while idx < len(line_spans):
                nxt = line_spans[idx]
                nxt_norm = _normalize_line(nxt[5])
                if (
                    not nxt_norm
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
            cur_sec = section_stack[-1]
            cur_sec_id = str(cur_sec["section_id"])
            norm_start, norm_end = append_norm_piece(p_norm)
            for ancestor in section_stack:
                ancestor["norm_end"] = norm_end

            block_kind = (
                "list_item" if _LIST_ITEM_RE.match(para_lines[0][5]) else "paragraph"
            )
            block_id = f"blk-{len(blocks) + 1}"
            span = SourcePositionSpan(
                source_start_offset=para_lines[0][0],
                source_end_offset=para_lines[-1][1],
                source_byte_start=para_lines[0][2],
                source_byte_end=para_lines[-1][3],
                source_line_start=para_lines[0][4],
                source_line_end=para_lines[-1][4],
                normalized_start_offset=norm_start,
                normalized_end_offset=norm_end,
            )
            blocks.append(
                TextBlock(
                    block_id=block_id,
                    kind=block_kind,
                    page_number=1,
                    reading_order=reading_order,
                    section_id=cur_sec_id,
                    raw_text=p_raw,
                    normalized_text=p_norm,
                    span=span,
                    provenance=provenance,
                )
            )
            cur_sec["block_ids"].append(block_id)  # type: ignore[attr-defined]
            new_formulas, formula_count = _extract_formulas_from_text(
                text=p_norm,
                page_number=1,
                section_id=cur_sec_id,
                start_order=reading_order,
                formula_counter_start=formula_count,
                provenance=provenance,
            )
            for frm in new_formulas:
                formulas.append(frm)
                cur_sec["formula_ids"].append(frm.formula_id)  # type: ignore[attr-defined]
            evidence_refs.append(
                EvidenceReference(
                    evidence_id=f"ev-{len(evidence_refs) + 1}",
                    source_id=source_id,
                    version_id=version_id,
                    source_sha256=source_sha256,
                    section_id=cur_sec_id,
                    block_id=block_id,
                    page_number=1,
                    span=span,
                    excerpt=p_norm[:1000],
                )
            )
            reading_order += 1

        for sec in section_stack:
            sec["norm_end"] = norm_cursor

        for sec in sections_work:
            sections.append(
                SourceSection(
                    section_id=str(sec["section_id"]),
                    parent_section_id=(
                        None
                        if sec["parent_section_id"] is None
                        else str(sec["parent_section_id"])
                    ),
                    title=str(sec["title"]),
                    level=int(sec["level"]),
                    reading_order=int(sec["reading_order"]),
                    page_start=1,
                    page_end=1,
                    block_ids=tuple(sec["block_ids"]),  # type: ignore[arg-type]
                    formula_ids=tuple(sec["formula_ids"]),  # type: ignore[arg-type]
                    child_section_ids=tuple(sec["child_section_ids"]),  # type: ignore[arg-type]
                    normalized_start_offset=int(sec["norm_start"]),
                    normalized_end_offset=int(sec["norm_end"]),
                )
            )

        full_normalized = "\n\n".join(normalized_parts)
        page = DocumentPage(
            page_number=1,
            classification="digital_text",
            recommended_processors=(),
            confidence=1.0,
            reading_order_reliable=True,
            raw_char_count=len(decoded),
            normalized_char_count=len(full_normalized),
            image_count=0,
            suspected_formula=bool(formulas),
            text_block_ids=tuple(b.block_id for b in blocks),
            formula_ids=tuple(f.formula_id for f in formulas),
            warning_ids=tuple(w.warning_id for w in warnings),
            provenance=provenance,
        )
        quality = ExtractionQualityReport(
            overall_status="partial_with_warnings" if warnings else "complete",
            usable_for_digital_ingestion=True,
            reading_order_verified=True,
            total_pages=1,
            digital_text_pages=1,
            pages_needing_layout=0,
            pages_needing_table=0,
            pages_needing_ocr=0,
            pages_with_visuals=0,
            pages_uncertain=0,
            total_raw_chars=len(decoded),
            total_normalized_chars=len(full_normalized),
            warning_count=len(warnings),
            notes=("Native UTF-8 text normalization preserved deterministic offsets.",),
        )
        return ExtractedDocument(
            tenant_id=tenant_id,
            source_id=source_id,
            version_id=version_id,
            source_version=source_version,
            source_sha256=source_sha256,
            input_format=input_format,
            title=title,
            normalized_text=full_normalized,
            pages=(page,),
            sections=tuple(sections),
            text_blocks=tuple(blocks),
            tables=(),
            figures=(),
            formulas=tuple(formulas),
            evidence_references=tuple(evidence_refs),
            warnings=tuple(warnings),
            quality=quality,
            provenance=provenance,
            created_at=datetime.now(UTC),
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
        p_start = paragraph_buf[0][0]
        p_end = paragraph_buf[-1][1]
        line_start = paragraph_buf[0][2]
        line_end = paragraph_buf[-1][2]
        first_raw = paragraph_buf[0][3]
        p_raw = page_text[p_start:p_end]
        p_norm = "\n".join(
            _normalize_line(x[3]) for x in paragraph_buf if _normalize_line(x[3])
        )
        paragraph_buf.clear()
        if not p_norm:
            return 0

        sec_id = str(state.current_section["section_id"])
        n_start, n_end = state.append_norm_piece(p_norm)
        state.sections_work[0]["norm_end"] = state.norm_cursor
        state.current_section["norm_end"] = state.norm_cursor
        state.current_section["page_end"] = page_no
        blk_id = f"blk-{len(state.blocks) + 1}"
        span = SourcePositionSpan(
            source_start_offset=p_start,
            source_end_offset=p_end,
            source_line_start=line_start,
            source_line_end=line_end,
            normalized_start_offset=n_start,
            normalized_end_offset=n_end,
        )
        state.blocks.append(
            TextBlock(
                block_id=blk_id,
                kind="list_item" if _LIST_ITEM_RE.match(first_raw) else "paragraph",
                page_number=page_no,
                reading_order=state.reading_order,
                section_id=sec_id,
                raw_text=p_raw,
                normalized_text=p_norm,
                span=span,
                provenance=state.provenance,
            )
        )
        page_block_ids.append(blk_id)
        state.current_section["block_ids"].append(blk_id)  # type: ignore[attr-defined]
        new_formulas, state.frm_seq = _extract_formulas_from_text(
            text=p_norm,
            page_number=page_no,
            section_id=sec_id,
            start_order=state.reading_order,
            formula_counter_start=state.frm_seq,
            provenance=state.provenance,
        )
        for frm in new_formulas:
            state.formulas.append(frm)
            page_formula_ids.append(frm.formula_id)
            state.current_section["formula_ids"].append(frm.formula_id)  # type: ignore[attr-defined]
        if new_formulas:
            page_warn_ids.append(
                state.add_warning(
                    "formula_detected_unparsed",
                    "info",
                    f"Detected {len(new_formulas)} mathematical expression(s) on page {page_no}.",
                    page_number=page_no,
                    block_id=blk_id,
                )
            )
        state.evidence_refs.append(
            EvidenceReference(
                evidence_id=f"ev-{len(state.evidence_refs) + 1}",
                source_id=state.source_id,
                version_id=state.version_id,
                source_sha256=state.source_sha256,
                section_id=sec_id,
                block_id=blk_id,
                page_number=page_no,
                span=span,
                excerpt=p_norm[:1000],
            )
        )
        state.reading_order += 1
        return len(p_norm)

    def _extract_digital_pdf(
        self,
        *,
        tenant_id: UUID,
        source_id: UUID,
        version_id: UUID,
        source_version: int,
        source_sha256: str,
        title: str,
        raw_bytes: bytes,
        limits: SourceIngestionLimits,
    ) -> ExtractedDocument:
        started_at = time.monotonic()
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
                title=title,
                limits=limits,
            )

    def _extract_digital_pdf_pages(
        self,
        *,
        reader: pypdf.PdfReader,
        page_count: int,
        started_at: float,
        tenant_id: UUID,
        source_id: UUID,
        version_id: UUID,
        source_version: int,
        source_sha256: str,
        title: str,
        limits: SourceIngestionLimits,
    ) -> ExtractedDocument:
        provenance = ExtractionProvenance(
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
            provenance=provenance,
            page_count=page_count,
            title=title,
        )

        pages: list[DocumentPage] = []
        total_raw_chars = 0
        first_lines: list[str] = []
        last_lines: list[str] = []

        for page_index in range(page_count):
            if (time.monotonic() - started_at) > MAX_PDF_EXTRACTION_SECONDS:
                raise AccessError(
                    422,
                    "extraction_timeout",
                    "PDF extraction exceeded per-request CPU time budget",
                )
            page_no = page_index + 1
            page_obj = reader.pages[page_index]
            page_warn_ids: list[str] = []
            page_block_ids: list[str] = []
            page_table_ids: list[str] = []
            page_figure_ids: list[str] = []
            page_formula_ids: list[str] = []
            recommended: list[RecommendedProcessor] = []

            page_width: float | None = None
            page_height: float | None = None
            with contextlib.suppress(AttributeError, TypeError, ValueError):
                box = page_obj.mediabox
                w, h = float(box.width), float(box.height)
                if w > 0 and h > 0:
                    page_width, page_height = w, h

            page_error = False
            try:
                page_text = page_obj.extract_text(extraction_mode="layout") or ""
            except LimitReachedError:
                page_text = ""
                page_error = True
                page_warn_ids.append(
                    state.add_warning(
                        "partial_page_error",
                        "warning",
                        f"Page {page_no} exceeded bounded PDF decompression/work limit.",
                        page_number=page_no,
                        deferred_stage="batch_2_3_layout_ocr",
                    )
                )
            except _PYPDF_READ_ERRORS:
                try:
                    page_text = page_obj.extract_text() or ""
                    page_warn_ids.append(
                        state.add_warning(
                            "reading_order_uncertain",
                            "warning",
                            f"Page {page_no} layout mode fell back to plain pypdf stream; reading order may be unreliable.",
                            page_number=page_no,
                            deferred_stage="batch_2_3_layout_aware",
                        )
                    )
                except _PYPDF_READ_ERRORS:
                    page_text = ""
                    page_error = True
                    page_warn_ids.append(
                        state.add_warning(
                            "partial_page_error",
                            "warning",
                            f"Page {page_no} content stream failed during fast pypdf text extraction.",
                            page_number=page_no,
                            deferred_stage="batch_2_3_layout_ocr",
                        )
                    )

            total_raw_chars += len(page_text)
            if total_raw_chars > limits.max_text_chars:
                raise AccessError(
                    413,
                    "text_limit_exceeded",
                    f"Extracted PDF text exceeds limit of {limits.max_text_chars} characters",
                )

            image_count = _count_page_images_safely(page_obj)

            stripped = page_text.strip()
            lines = page_text.splitlines()
            non_empty_lines = [
                _normalize_line(ln) for ln in lines if _normalize_line(ln)
            ]
            if non_empty_lines:
                first_lines.append(non_empty_lines[0])
                last_lines.append(non_empty_lines[-1])

            table_rows: list[str] = []
            two_col_hits = 0
            for ln in lines:
                ln_stripped = ln.strip()
                if not ln_stripped:
                    continue
                if _PIPE_ROW_RE.match(ln_stripped):
                    table_rows.append(ln_stripped)
                    continue
                segments = [seg for seg in _WIDE_SPLIT_RE.split(ln_stripped) if seg]
                if len(segments) >= 3:
                    table_rows.append(ln_stripped)
                elif len(segments) == 2 and _MULTICOLUMN_GAP_RE.search(ln_stripped):
                    two_col_hits += 1

            garbled_chars = sum(1 for ch in stripped if ch == "\ufffd")
            high_garble = len(stripped) > 0 and (garbled_chars / len(stripped)) > 0.15

            suspected_table = len(table_rows) >= 2
            suspected_multicolumn = two_col_hits >= 2 and not suspected_table
            has_images = image_count > 0

            if has_images:
                state.fig_seq += 1
                fid = f"fig-{state.fig_seq}"
                state.figures.append(
                    FigureReference(
                        figure_id=fid,
                        page_number=page_no,
                        reading_order=state.reading_order,
                        section_id=str(state.current_section["section_id"]),
                        kind="raster_image",
                        image_count_on_page=image_count,
                        alt_text_or_description=None,
                        interpretation_status="not_interpreted_visual_deferred",
                        provenance=ExtractionProvenance(
                            extractor_id=PYPDF_EXTRACTOR_ID,
                            extractor_version=PYPDF_EXTRACTOR_VERSION,
                            method="deferred",
                            raw_locator=f"page={page_no};images={image_count}",
                        ),
                    )
                )
                page_figure_ids.append(fid)
                state.current_section["figure_ids"].append(fid)  # type: ignore[attr-defined]
                if "visual" not in recommended:
                    recommended.append("visual")
                page_warn_ids.append(
                    state.add_warning(
                        "visual_content_detected",
                        "info",
                        f"Page {page_no} contains {image_count} embedded image(s); visual interpretation deferred to Batch 2.3.",
                        page_number=page_no,
                        deferred_stage="batch_2_3_multimodal_visual",
                    )
                )

            if suspected_table:
                if "table" not in recommended:
                    recommended.append("table")
                state.tbl_seq += 1
                tid = f"tbl-{state.tbl_seq}"
                raw_table_lines = "\n".join(table_rows)
                state.tables.append(
                    TableBlock(
                        table_id=tid,
                        page_number=page_no,
                        reading_order=state.reading_order,
                        section_id=str(state.current_section["section_id"]),
                        raw_text_fallback=(
                            raw_table_lines[:4000] if raw_table_lines else None
                        ),
                        extraction_state="detected_unparsed",
                        provenance=ExtractionProvenance(
                            extractor_id=PYPDF_EXTRACTOR_ID,
                            extractor_version=PYPDF_EXTRACTOR_VERSION,
                            method="deferred",
                            raw_locator=f"page={page_no};table_heuristic",
                        ),
                    )
                )
                page_table_ids.append(tid)
                state.current_section["table_ids"].append(tid)  # type: ignore[attr-defined]
                state.reading_order_verified = False
                page_warn_ids.append(
                    state.add_warning(
                        "table_extraction_required",
                        "warning",
                        f"Page {page_no} appears to contain tabular data; structured table parsing deferred to Batch 2.3.",
                        page_number=page_no,
                        deferred_stage="batch_2_3_table_parser",
                    )
                )

            if suspected_multicolumn:
                if "layout" not in recommended:
                    recommended.append("layout")
                state.reading_order_verified = False
                page_warn_ids.append(
                    state.add_warning(
                        "layout_reconstruction_required",
                        "warning",
                        f"Page {page_no} exhibits wide horizontal column gaps; pypdf reading order is unreliable and layout reconstruction is required.",
                        page_number=page_no,
                        deferred_stage="batch_2_3_layout_aware",
                    )
                )

            low_text = len(stripped) < 20
            if low_text and has_images:
                classification: PageClassification = "needs_ocr"
                if "ocr" not in recommended:
                    recommended.insert(0, "ocr")
                confidence = 0.25
                reading_order_reliable = False
                state.reading_order_verified = False
                page_warn_ids.append(
                    state.add_warning(
                        "ocr_required",
                        "blocking_for_auto_publish",
                        f"Page {page_no} contains embedded image(s) with minimal extractable text; OCR is deferred to Batch 2.3.",
                        page_number=page_no,
                        deferred_stage="batch_2_3_ocr",
                    )
                )
            elif page_error or high_garble or (low_text and not has_images):
                classification = "uncertain"
                if "ocr" not in recommended:
                    recommended.append("ocr")
                confidence = 0.2 if (page_error or high_garble) else 0.4
                reading_order_reliable = False
                state.reading_order_verified = False
                page_warn_ids.append(
                    state.add_warning(
                        "blank_or_low_text_page",
                        "warning",
                        f"Page {page_no} yielded little or garbled extractable text; classification is uncertain.",
                        page_number=page_no,
                        deferred_stage="batch_2_3_ocr",
                    )
                )
            elif suspected_multicolumn:
                classification = "needs_layout"
                confidence = 0.55
                reading_order_reliable = False
            elif suspected_table:
                classification = "needs_table"
                confidence = 0.65
                reading_order_reliable = False
            elif has_images:
                classification = "visual_content"
                confidence = 0.75
                reading_order_reliable = not suspected_multicolumn
            else:
                classification = "digital_text"
                confidence = 0.9
                reading_order_reliable = True

            page_norm_chars = 0
            page_cursor = 0
            paragraph_buf: list[tuple[int, int, int, str]] = []

            for line_no, raw_ln in enumerate(page_text.splitlines(keepends=True), start=1):
                stripped_ln = raw_ln.rstrip("\r\n")
                l_start = page_cursor
                l_end = page_cursor + len(stripped_ln)
                page_cursor += len(raw_ln)
                norm_ln = _normalize_line(stripped_ln)
                if not norm_ln:
                    page_norm_chars += self._commit_pdf_paragraph(
                        state,
                        paragraph_buf=paragraph_buf,
                        page_text=page_text,
                        page_no=page_no,
                        page_block_ids=page_block_ids,
                        page_formula_ids=page_formula_ids,
                        page_warn_ids=page_warn_ids,
                    )
                    continue

                num_match = _NUMBERED_HEADING_RE.match(norm_ln)
                md_match = _MARKDOWN_HEADING_RE.match(norm_ln)
                if md_match or num_match:
                    page_norm_chars += self._commit_pdf_paragraph(
                        state,
                        paragraph_buf=paragraph_buf,
                        page_text=page_text,
                        page_no=page_no,
                        page_block_ids=page_block_ids,
                        page_formula_ids=page_formula_ids,
                        page_warn_ids=page_warn_ids,
                    )
                    state.sec_seq += 1
                    h_level = len(md_match.group(1)) if md_match else 2
                    h_title = (md_match.group(2) if md_match else norm_ln)[:300]
                    new_sec_id = f"sec-{state.sec_seq}"
                    n_start, n_end = state.append_norm_piece(h_title)
                    page_norm_chars += len(h_title)
                    new_sec: dict[str, object] = {
                        "section_id": new_sec_id,
                        "parent_section_id": "sec-1",
                        "title": h_title,
                        "level": min(h_level, 6),
                        "reading_order": state.reading_order,
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
                    root = state.sections_work[0]
                    root["child_section_ids"].append(new_sec_id)  # type: ignore[attr-defined]
                    root["norm_end"] = state.norm_cursor
                    state.sections_work.append(new_sec)
                    state.current_section = new_sec
                    blk_id = f"blk-{len(state.blocks) + 1}"
                    span = SourcePositionSpan(
                        source_start_offset=l_start,
                        source_end_offset=l_end,
                        source_line_start=line_no,
                        source_line_end=line_no,
                        normalized_start_offset=n_start,
                        normalized_end_offset=n_end,
                    )
                    state.blocks.append(
                        TextBlock(
                            block_id=blk_id,
                            kind="heading",
                            page_number=page_no,
                            reading_order=state.reading_order,
                            section_id=new_sec_id,
                            raw_text=stripped_ln,
                            normalized_text=h_title,
                            span=span,
                            heading_level=min(h_level, 6),
                            provenance=provenance,
                        )
                    )
                    page_block_ids.append(blk_id)
                    new_sec["block_ids"].append(blk_id)  # type: ignore[attr-defined]
                    state.evidence_refs.append(
                        EvidenceReference(
                            evidence_id=f"ev-{len(state.evidence_refs) + 1}",
                            source_id=source_id,
                            version_id=version_id,
                            source_sha256=source_sha256,
                            section_id=new_sec_id,
                            block_id=blk_id,
                            page_number=page_no,
                            span=span,
                            excerpt=h_title[:1000],
                        )
                    )
                    state.reading_order += 1
                    continue

                paragraph_buf.append((l_start, l_end, line_no, stripped_ln))

            page_norm_chars += self._commit_pdf_paragraph(
                state,
                paragraph_buf=paragraph_buf,
                page_text=page_text,
                page_no=page_no,
                page_block_ids=page_block_ids,
                page_formula_ids=page_formula_ids,
                page_warn_ids=page_warn_ids,
            )

            pages.append(
                DocumentPage(
                    page_number=page_no,
                    width=page_width,
                    height=page_height,
                    classification=classification,
                    recommended_processors=tuple(recommended),
                    confidence=confidence,
                    reading_order_reliable=reading_order_reliable,
                    raw_char_count=len(page_text),
                    normalized_char_count=page_norm_chars,
                    image_count=image_count,
                    suspected_multicolumn=suspected_multicolumn,
                    suspected_table=suspected_table,
                    suspected_formula=bool(page_formula_ids),
                    text_block_ids=tuple(page_block_ids),
                    table_ids=tuple(page_table_ids),
                    figure_ids=tuple(page_figure_ids),
                    formula_ids=tuple(page_formula_ids),
                    warning_ids=tuple(page_warn_ids),
                    provenance=provenance,
                )
            )

        if page_count >= 3 and first_lines:
            if len(set(first_lines)) == 1 and len(first_lines[0]) > 2:
                state.add_warning(
                    "repeated_header_footer_suspected",
                    "info",
                    "Identical top line detected across pages; repeated header retained rather than discarded.",
                )
            elif len(set(last_lines)) == 1 and len(last_lines[0]) > 2:
                state.add_warning(
                    "repeated_header_footer_suspected",
                    "info",
                    "Identical bottom line detected across pages; repeated footer retained rather than discarded.",
                )

        sections = tuple(
            SourceSection(
                section_id=str(s["section_id"]),
                parent_section_id=(
                    None
                    if s["parent_section_id"] is None
                    else str(s["parent_section_id"])
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
            for s in state.sections_work
        )

        full_normalized = "\n\n".join(state.normalized_parts)
        c_digital = sum(1 for p in pages if p.classification == "digital_text")
        c_layout = sum(1 for p in pages if p.classification == "needs_layout")
        c_table = sum(1 for p in pages if p.classification == "needs_table")
        c_ocr = sum(1 for p in pages if p.classification == "needs_ocr")
        c_visual = sum(1 for p in pages if p.classification == "visual_content")
        c_uncertain = sum(1 for p in pages if p.classification == "uncertain")

        usable = len(full_normalized.strip()) >= limits.min_text_chars
        if not usable:
            overall_status = "ocr_required" if c_ocr > 0 else "unreadable"
        elif state.warnings:
            overall_status = "partial_with_warnings"
        else:
            overall_status = "complete"

        notes: list[str] = [
            "Fast pypdf triage executed; multi-column/table/OCR/visual follow-up deferred to Batch 2.3."
        ]
        if c_layout or c_table:
            notes.append(
                "Complex layout or tables detected; reading order is not guaranteed."
            )

        quality = ExtractionQualityReport(
            overall_status=overall_status,  # type: ignore[arg-type]
            usable_for_digital_ingestion=usable,
            reading_order_verified=state.reading_order_verified,
            total_pages=len(pages),
            digital_text_pages=c_digital,
            pages_needing_layout=c_layout,
            pages_needing_table=c_table,
            pages_needing_ocr=c_ocr,
            pages_with_visuals=c_visual,
            pages_uncertain=c_uncertain,
            total_raw_chars=total_raw_chars,
            total_normalized_chars=len(full_normalized),
            warning_count=len(state.warnings),
            notes=tuple(notes),
        )
        return ExtractedDocument(
            tenant_id=tenant_id,
            source_id=source_id,
            version_id=version_id,
            source_version=source_version,
            source_sha256=source_sha256,
            input_format="digital_pdf",
            title=title,
            normalized_text=full_normalized,
            pages=tuple(pages),
            sections=sections,
            text_blocks=tuple(state.blocks),
            tables=tuple(state.tables),
            figures=tuple(state.figures),
            formulas=tuple(state.formulas),
            evidence_references=tuple(state.evidence_refs),
            warnings=tuple(state.warnings),
            quality=quality,
            provenance=provenance,
            created_at=datetime.now(UTC),
        )
