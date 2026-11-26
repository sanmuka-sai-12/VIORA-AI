"""Unit tests for unified document contracts, adaptive extraction routing, validation, and orphan cleanup."""

import io
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import ValidationError
from pypdf import PdfWriter
from pypdf.errors import PdfReadError
from viora.config import Settings
from viora.contracts import ArtifactVersionRef
from viora.perf.public import build_synthetic_corpus
from viora.security import AccessError
from viora.sources.document_contracts import (
    BoundingBox,
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
from viora.sources.extraction import (
    AdaptiveDocumentExtractor,
    sanitize_original_filename,
    validate_and_sniff_text_input,
    validate_and_sniff_upload,
)
from viora.sources.schemas import (
    HARD_MAX_PDF_PAGES,
    HARD_MAX_SOURCE_BYTES,
    HARD_MAX_TEXT_CHARS,
    SourceIngestionLimits,
    clamp_source_limits,
)
from viora.storage.public import StorageAuthorizationError, local_artifact_store


def _make_pdf_bytes(
    *,
    num_pages: int = 1,
    encrypt_password: str | None = None,
    with_image_on_page: int | None = None,
) -> bytes:
    """Create tiny valid synthetic PDFs (optionally encrypted or zero-page or raster-image embedded)."""
    writer = PdfWriter()
    if num_pages == 0:
        buf = io.BytesIO()
        writer.write(buf)
        return buf.getvalue()

    for _ in range(num_pages):
        writer.add_blank_page(width=612, height=792)

    if with_image_on_page is not None:
        # Build a valid 1-pixel PNG using zlib/struct (zero external deps) and attach via reportlab/pypdf?
        # Simpler: embed an Image XObject directly into the PDF page resources dictionary!
        from pypdf.generic import (
            DecodedStreamObject,
            DictionaryObject,
            NameObject,
            NumberObject,
        )

        img = DecodedStreamObject()
        img[NameObject("/Type")] = NameObject("/XObject")
        img[NameObject("/Subtype")] = NameObject("/Image")
        img[NameObject("/Width")] = NumberObject(1)
        img[NameObject("/Height")] = NumberObject(1)
        img[NameObject("/ColorSpace")] = NameObject("/DeviceGray")
        img[NameObject("/BitsPerComponent")] = NumberObject(8)
        img.set_data(b"\x80")

        page = writer.pages[with_image_on_page]
        xobj_dict = DictionaryObject()
        img_ref = writer._add_object(img)
        xobj_dict[NameObject("/Im1")] = img_ref
        resources = page.get("/Resources")
        if resources is None:
            resources = DictionaryObject()
            page[NameObject("/Resources")] = resources
        resources[NameObject("/XObject")] = xobj_dict

    if encrypt_password is not None:
        writer.encrypt(encrypt_password)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def test_unified_document_contracts_validation_and_roundtrip() -> None:
    tenant, source, version = uuid4(), uuid4(), uuid4()
    sha = "a" * 64
    prov = ExtractionProvenance(
        extractor_id="viora.sources.native_text",
        extractor_version="1.0",
        method="native_text",
        confidence=1.0,
    )
    span = SourcePositionSpan(
        source_start_offset=0,
        source_end_offset=5,
        source_byte_start=0,
        source_byte_end=5,
        source_line_start=1,
        source_line_end=1,
        normalized_start_offset=0,
        normalized_end_offset=5,
    )
    bbox = BoundingBox(
        x0=10.0,
        y0=20.0,
        x1=100.0,
        y1=40.0,
        coordinate_space="pdf_points",
        page_width=612.0,
        page_height=792.0,
    )
    block = TextBlock(
        block_id="blk-1",
        kind="paragraph",
        page_number=1,
        reading_order=0,
        section_id="sec-1",
        bbox=bbox,
        raw_text="Hello",
        normalized_text="Hello",
        span=span,
        provenance=prov,
    )
    table = TableBlock(
        table_id="tbl-1",
        page_number=1,
        reading_order=1,
        section_id="sec-1",
        extraction_state="detected_unparsed",
        raw_text_fallback="A   B   C",
        provenance=prov,
    )
    figure = FigureReference(
        figure_id="fig-1",
        page_number=1,
        reading_order=2,
        section_id="sec-1",
        kind="raster_image",
        image_count_on_page=1,
        alt_text_or_description=None,
        interpretation_status="not_interpreted_visual_deferred",
        provenance=prov,
    )
    formula = FormulaReference(
        formula_id="frm-1",
        page_number=1,
        reading_order=3,
        section_id="sec-1",
        raw_representation="$E = mc^2$",
        latex="E = mc^2",
        representation_status="native_latex",
        provenance=prov,
    )
    evidence = EvidenceReference(
        evidence_id="ev-1",
        source_id=source,
        version_id=version,
        source_sha256=sha,
        section_id="sec-1",
        block_id="blk-1",
        page_number=1,
        bbox=bbox,
        span=span,
        excerpt="Hello",
    )
    warning = ExtractionWarning(
        warning_id="warn-1",
        code="visual_content_detected",
        severity="info",
        message="Visual element deferred",
        page_number=1,
    )
    section = SourceSection(
        section_id="sec-1",
        parent_section_id=None,
        title="Intro",
        level=1,
        reading_order=0,
        page_start=1,
        page_end=1,
        block_ids=("blk-1",),
        table_ids=("tbl-1",),
        figure_ids=("fig-1",),
        formula_ids=("frm-1",),
        normalized_start_offset=0,
        normalized_end_offset=5,
    )
    page = DocumentPage(
        page_number=1,
        width=612.0,
        height=792.0,
        classification="visual_content",
        recommended_processors=("visual", "table"),
        confidence=0.8,
        reading_order_reliable=False,
        raw_char_count=5,
        normalized_char_count=5,
        image_count=1,
        suspected_table=True,
        suspected_formula=True,
        text_block_ids=("blk-1",),
        table_ids=("tbl-1",),
        figure_ids=("fig-1",),
        formula_ids=("frm-1",),
        warning_ids=("warn-1",),
        provenance=prov,
    )
    quality = ExtractionQualityReport(
        overall_status="partial_with_warnings",
        usable_for_digital_ingestion=True,
        reading_order_verified=False,
        total_pages=1,
        digital_text_pages=0,
        pages_needing_layout=0,
        pages_needing_table=1,
        pages_needing_ocr=0,
        pages_with_visuals=1,
        pages_uncertain=0,
        total_raw_chars=5,
        total_normalized_chars=5,
        warning_count=1,
    )
    doc = ExtractedDocument(
        tenant_id=tenant,
        source_id=source,
        version_id=version,
        source_version=1,
        source_sha256=sha,
        input_format="digital_pdf",
        title="Sample Physics",
        normalized_text="Hello",
        pages=(page,),
        sections=(section,),
        text_blocks=(block,),
        tables=(table,),
        figures=(figure,),
        formulas=(formula,),
        evidence_references=(evidence,),
        warnings=(warning,),
        quality=quality,
        provenance=prov,
        created_at=datetime.now(UTC),
    )

    reloaded = ExtractedDocument.model_validate_json(doc.model_dump_json())
    assert reloaded == doc
    with pytest.raises(ValidationError):
        ExtractedDocument.model_validate({**doc.model_dump(), "unexpected": True})


def test_native_text_extraction_preserves_offsets_unicode_sections_and_formulas() -> None:
    tenant, source, version = uuid4(), uuid4(), uuid4()
    raw = (
        "# Kinematics\r\n\r\n"
        "Velocity relates to Δx/Δt.\r\n"
        "Energy satisfies $E = mc^2$ in relativity.\r\n\r\n"
        "## Newton Laws\r\n"
        "1. Inertia holds when F_net = 0.\r\n"
    )
    limits = clamp_source_limits()
    fmt, raw_bytes, digest, _ = validate_and_sniff_text_input(
        input_format="typed_notes",
        content=raw,
        original_filename="lecture\\notes.md",
        limits=limits,
    )
    assert fmt == "typed_notes"
    extractor = AdaptiveDocumentExtractor()
    doc = extractor.extract(
        tenant_id=tenant,
        source_id=source,
        version_id=version,
        source_version=1,
        source_sha256=digest,
        input_format=fmt,
        title="Mechanics Notes",
        raw_bytes=raw_bytes,
        limits=limits,
    )
    decoded = raw_bytes.decode("utf-8")
    assert doc.quality.usable_for_digital_ingestion is True
    assert doc.quality.reading_order_verified is True
    assert len(doc.sections) >= 3
    assert any(f.latex == "E = mc^2" for f in doc.formulas)
    for blk in doc.text_blocks:
        assert blk.span is not None
        assert (
            doc.normalized_text[
                blk.span.normalized_start_offset : blk.span.normalized_end_offset
            ]
            == blk.normalized_text
        )
        assert (
            decoded[blk.span.source_start_offset : blk.span.source_end_offset]
            == blk.raw_text
        )
        assert blk.span.source_byte_start is not None
        assert blk.span.source_byte_end is not None
        assert (
            raw_bytes[blk.span.source_byte_start : blk.span.source_byte_end].decode(
                "utf-8"
            )
            == blk.raw_text
        )
    for sec in doc.sections:
        assert sec.normalized_end_offset >= sec.normalized_start_offset
        assert (
            len(
                doc.normalized_text[
                    sec.normalized_start_offset : sec.normalized_end_offset
                ]
            )
            > 0
        )


def test_adaptive_pdf_triage_routes_digital_multicolumn_table_ocr_and_images() -> None:
    corpus = build_synthetic_corpus()
    extractor = AdaptiveDocumentExtractor()
    limits = clamp_source_limits()
    tenant = uuid4()

    # 1) Digital small PDF
    small_doc = extractor.extract(
        tenant_id=tenant,
        source_id=uuid4(),
        version_id=uuid4(),
        source_version=1,
        source_sha256="b" * 64,
        input_format="digital_pdf",
        title="Small",
        raw_bytes=corpus.small_pdf,
        limits=limits,
    )
    assert small_doc.quality.usable_for_digital_ingestion is True
    assert small_doc.pages[0].classification == "digital_text"
    assert corpus.small_pdf_markers[0] in small_doc.normalized_text

    # 2) Multi-column PDF -> must NOT claim reliable pypdf reading order
    multi_doc = extractor.extract(
        tenant_id=tenant,
        source_id=uuid4(),
        version_id=uuid4(),
        source_version=1,
        source_sha256="c" * 64,
        input_format="digital_pdf",
        title="MultiCol",
        raw_bytes=corpus.multicolumn_pdf,
        limits=limits,
    )
    assert multi_doc.quality.pages_needing_layout >= 1
    assert multi_doc.pages[0].classification == "needs_layout"
    assert multi_doc.pages[0].reading_order_reliable is False
    assert multi_doc.quality.reading_order_verified is False
    assert any(w.code == "layout_reconstruction_required" for w in multi_doc.warnings)

    # 3) Table PDF -> detected_unparsed TableBlock + warning
    tbl_doc = extractor.extract(
        tenant_id=tenant,
        source_id=uuid4(),
        version_id=uuid4(),
        source_version=1,
        source_sha256="d" * 64,
        input_format="digital_pdf",
        title="TableDoc",
        raw_bytes=corpus.table_pdf,
        limits=limits,
    )
    assert tbl_doc.pages[0].classification == "needs_table"
    assert tbl_doc.tables and tbl_doc.tables[0].extraction_state == "detected_unparsed"
    assert any(w.code == "table_extraction_required" for w in tbl_doc.warnings)

    # 4) Scanned/no-text PDF with embedded raster image -> needs_ocr, zero fabricated OCR
    scanned_pdf = _make_pdf_bytes(num_pages=1, with_image_on_page=0)
    ocr_doc = extractor.extract(
        tenant_id=tenant,
        source_id=uuid4(),
        version_id=uuid4(),
        source_version=1,
        source_sha256="e" * 64,
        input_format="digital_pdf",
        title="Scanned",
        raw_bytes=scanned_pdf,
        limits=limits,
    )
    assert ocr_doc.quality.usable_for_digital_ingestion is False
    assert ocr_doc.quality.overall_status == "ocr_required"
    assert ocr_doc.pages[0].classification == "needs_ocr"
    assert ocr_doc.figures[0].alt_text_or_description is None
    assert (
        ocr_doc.figures[0].interpretation_status == "not_interpreted_visual_deferred"
    )
    assert any(w.code == "ocr_required" for w in ocr_doc.warnings)


def test_input_validation_rejects_spoofing_encryption_malformed_and_oversized() -> None:
    limits = clamp_source_limits(max_bytes=2048, max_pdf_pages=2, max_text_chars=500)

    # Filename traversal & control chars
    assert sanitize_original_filename("../../folder/Notes.txt") == "Notes.txt"
    with pytest.raises(AccessError) as exc:
        sanitize_original_filename("..")
    assert exc.value.code == "invalid_filename"

    # Empty text & whitespace
    for empty in ("", "   \n\t "):
        with pytest.raises(AccessError) as exc:
            validate_and_sniff_text_input(
                input_format="topic_description",
                content=empty,
                original_filename=None,
                limits=limits,
            )
        assert exc.value.code == "empty_input"

    # Spoofed .pdf extension containing plain text
    with pytest.raises(AccessError) as exc:
        validate_and_sniff_upload(
            raw_bytes=b"Just some plain text pretending to be PDF",
            declared_format=None,
            original_filename="spoofed.pdf",
            claimed_content_type="application/pdf",
            limits=limits,
        )
    assert exc.value.status == 415

    # Unsupported extension (.docx / .png)
    with pytest.raises(AccessError) as exc:
        validate_and_sniff_upload(
            raw_bytes=b"PK\x03\x04fake-docx",
            declared_format=None,
            original_filename="slides.docx",
            claimed_content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            limits=limits,
        )
    assert exc.value.status == 415

    # Encrypted PDF
    encrypted = _make_pdf_bytes(num_pages=1, encrypt_password="secret")
    with pytest.raises(AccessError) as exc:
        validate_and_sniff_upload(
            raw_bytes=encrypted,
            declared_format="digital_pdf",
            original_filename="locked.pdf",
            claimed_content_type="application/pdf",
            limits=limits,
        )
    assert exc.value.code == "encrypted_pdf"

    # Malformed PDF
    with pytest.raises(AccessError) as exc:
        validate_and_sniff_upload(
            raw_bytes=b"%PDF-1.7\ncorrupted-object-stream",
            declared_format="digital_pdf",
            original_filename="broken.pdf",
            claimed_content_type="application/pdf",
            limits=limits,
        )
    assert exc.value.code == "malformed_pdf"

    # Exceeds page limit
    too_many_pages = _make_pdf_bytes(num_pages=3)
    with pytest.raises(AccessError) as exc:
        validate_and_sniff_upload(
            raw_bytes=too_many_pages,
            declared_format="digital_pdf",
            original_filename="three_pages.pdf",
            claimed_content_type="application/pdf",
            limits=limits,
        )
    assert exc.value.code == "page_limit_exceeded"

    # Exceeds byte limit
    with pytest.raises(AccessError) as exc:
        validate_and_sniff_upload(
            raw_bytes=b"A" * 3000,
            declared_format="plain_text",
            original_filename="huge.txt",
            claimed_content_type="text/plain",
            limits=limits,
        )
    assert exc.value.status == 413

    # Settings & limits hard upper-bound enforcement
    clamped = clamp_source_limits(
        max_bytes=HARD_MAX_SOURCE_BYTES * 10,
        max_pdf_pages=HARD_MAX_PDF_PAGES * 10,
        max_text_chars=HARD_MAX_TEXT_CHARS * 10,
    )
    assert clamped.max_bytes == HARD_MAX_SOURCE_BYTES
    assert clamped.max_pdf_pages == HARD_MAX_PDF_PAGES
    assert clamped.max_text_chars == HARD_MAX_TEXT_CHARS
    with pytest.raises(ValidationError):
        Settings(
            database_url="postgresql+psycopg://viora_app:pw@127.0.0.1:5432/viora",  # type: ignore[arg-type]
            source_max_bytes=HARD_MAX_SOURCE_BYTES + 1,
        )


def test_local_artifact_store_uncommitted_cleanup(tmp_path: Path) -> None:
    from viora.contracts import TenantContext

    store = local_artifact_store(tmp_path / "artifacts")
    tenant = uuid4()
    other_tenant = uuid4()
    ctx = TenantContext(actor_id=uuid4(), tenant_id=tenant, roles=frozenset({"subject_teacher"}))
    other_ctx = TenantContext(actor_id=uuid4(), tenant_id=other_tenant, roles=frozenset())

    res_id, ver_id = uuid4(), uuid4()
    stored = store.put_bytes(
        ctx,
        b"temporary uncommitted payload",
        kind="source_input",
        resource_id=res_id,
        artifact_id=res_id,
        version_id=ver_id,
    )
    assert store.exists(ctx, stored.metadata.ref)
    assert store.delete_uncommitted(ctx, stored.metadata.ref) is True
    assert store.exists(ctx, stored.metadata.ref) is False
    assert store.delete_uncommitted(ctx, stored.metadata.ref) is False

    # Cross-tenant cleanup must raise StorageAuthorizationError
    with pytest.raises(StorageAuthorizationError):
        store.delete_uncommitted(other_ctx, ArtifactVersionRef(
            tenant_id=tenant,
            kind="source_input",
            resource_id=res_id,
            version_id=ver_id,
            sha256="a" * 64,
        ))
    # Ensure PdfReadError referenced keeps mypy/ruff happy
    assert issubclass(PdfReadError, Exception)
    assert SourceIngestionLimits().max_bytes <= HARD_MAX_SOURCE_BYTES


def test_pdf_decompression_limit_and_non_decompressing_image_count() -> None:
    import zlib

    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject
    from viora.sources.extraction import (
        MAX_PDF_DECOMPRESSED_STREAM_BYTES,
        _count_page_images_safely,
    )

    # Build a synthetic PDF with a FlateDecode stream exceeding MAX_PDF_DECOMPRESSED_STREAM_BYTES
    bomb_payload = zlib.compress(b" " * (MAX_PDF_DECOMPRESSED_STREAM_BYTES + 4096), level=9)
    obj1 = b"<< /Type /Catalog /Pages 2 0 R >>"
    obj2 = b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>"
    obj3 = b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R >>"
    obj4 = (
        f"<< /Length {len(bomb_payload)} /Filter /FlateDecode >>\nstream\n".encode("ascii")
        + bomb_payload
        + b"\nendstream"
    )
    objs = [obj1, obj2, obj3, obj4]
    out = bytearray(b"%PDF-1.4\n")
    offs = [0]
    for idx, body in enumerate(objs, start=1):
        offs.append(len(out))
        out.extend(f"{idx} 0 obj\n".encode("ascii"))
        out.extend(body)
        out.extend(b"\nendobj\n")
    xref = len(out)
    out.extend(f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode("ascii"))
    for o in offs[1:]:
        out.extend(f"{o:010d} 00000 n \n".encode("ascii"))
    out.extend(
        f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode(
            "ascii"
        )
    )

    extractor = AdaptiveDocumentExtractor()
    limits = clamp_source_limits()
    doc = extractor.extract(
        tenant_id=uuid4(),
        source_id=uuid4(),
        version_id=uuid4(),
        source_version=1,
        source_sha256="f" * 64,
        input_format="digital_pdf",
        title="Bomb",
        raw_bytes=bytes(out),
        limits=limits,
    )
    # Bounded pypdf LimitReachedError is caught cleanly as partial_page_error rather than 500/OOM
    assert doc.quality.overall_status == "unreadable"
    assert any(w.code == "partial_page_error" for w in doc.warnings)

    # Verify _count_page_images_safely counts /Image XObjects without decoding corrupt streams
    corrupt_img = DecodedStreamObject()
    corrupt_img[NameObject("/Type")] = NameObject("/XObject")
    corrupt_img[NameObject("/Subtype")] = NameObject("/Image")
    corrupt_img[NameObject("/Filter")] = NameObject("/FlateDecode")
    corrupt_img._data = b"not-valid-zlib-would-fail-if-decompressed"
    res = DictionaryObject()
    res[NameObject("/XObject")] = DictionaryObject({NameObject("/ImBad"): corrupt_img})
    fake_page = DictionaryObject({NameObject("/Resources"): res})
    assert _count_page_images_safely(fake_page) == 1
