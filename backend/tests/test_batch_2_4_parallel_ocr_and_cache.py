"""Unit tests for Batch 2.4 upload validation, parallel extraction, OCR preprocessing, and caching."""

from __future__ import annotations

import io
import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from PIL import Image
from viora.security import AccessError
from viora.sources.cache import (
    ExtractionCacheConfig,
    build_extraction_cache_key,
    build_page_ocr_cache_key,
    build_scope_key,
    rebind_cached_document,
)
from viora.sources.extraction import (
    AdaptiveDocumentExtractor,
    ExtractionCancelledError,
    validate_and_sniff_upload,
)
from viora.sources.ocr import (
    OcrLine,
    OcrPageResult,
    get_process_ocr_backend,
)
from viora.sources.schemas import clamp_source_limits
from viora.workflow.repository import sanitize_error_message

REPO_ROOT = Path(__file__).resolve().parents[2]
CORPUS_DIR = REPO_ROOT / "benchmarks" / "educational_corpus"
MANIFEST_PATH = CORPUS_DIR / "ground_truth.json"


def _load_sample(doc_id: str) -> tuple[dict, bytes]:
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    item = next(d for d in manifest["documents"] if d["doc_id"] == doc_id)
    return item, (CORPUS_DIR / item["relative_path"]).read_bytes()


def test_upload_validation_accepts_images_and_scanned_pdfs_and_rejects_spoofing() -> None:
    limits = clamp_source_limits()

    _, png_bytes = _load_sample("07_scanned_printed_page_png")
    fmt, raw, _, name = validate_and_sniff_upload(
        raw_bytes=png_bytes,
        declared_format=None,
        original_filename="scan.png",
        claimed_content_type="image/png",
        limits=limits,
    )
    assert fmt == "png"
    assert raw == png_bytes
    assert name == "scan.png"

    _, jpg_bytes = _load_sample("08_photographed_notes")
    fmt_j, _, _, _ = validate_and_sniff_upload(
        raw_bytes=jpg_bytes,
        declared_format="jpeg",
        original_filename="notes.jpg",
        claimed_content_type="image/jpeg",
        limits=limits,
    )
    assert fmt_j == "jpeg"

    _, scan_pdf = _load_sample("07_scanned_printed_page_pdf")
    fmt_s, _, _, _ = validate_and_sniff_upload(
        raw_bytes=scan_pdf,
        declared_format="scanned_pdf",
        original_filename="scanned.pdf",
        claimed_content_type="application/pdf",
        limits=limits,
    )
    assert fmt_s == "scanned_pdf"

    # Spoofed PNG containing plain text -> 415
    with pytest.raises(AccessError) as exc:
        validate_and_sniff_upload(
            raw_bytes=b"Not a real PNG file",
            declared_format=None,
            original_filename="fake.png",
            claimed_content_type="image/png",
            limits=limits,
        )
    assert exc.value.status == 415

    # Corrupted PNG header -> 422 malformed_image
    with pytest.raises(AccessError) as exc_bad:
        validate_and_sniff_upload(
            raw_bytes=b"\x89PNG\r\n\x1a\ncorrupted-idat",
            declared_format="png",
            original_filename="broken.png",
            claimed_content_type="image/png",
            limits=limits,
        )
    assert exc_bad.value.code == "malformed_image"


def test_parallel_extraction_matches_sequential_deterministically_and_supports_cancel() -> None:
    limits = clamp_source_limits()
    tenant, source_id, version_id = uuid4(), uuid4(), uuid4()
    tbl_item, tbl_bytes = _load_sample("05_tables_bordered_and_borderless")

    seq = AdaptiveDocumentExtractor(page_workers=1)
    par = AdaptiveDocumentExtractor(page_workers=2)
    try:
        doc_seq = seq.extract(
            tenant_id=tenant,
            source_id=source_id,
            version_id=version_id,
            source_version=1,
            source_sha256=tbl_item["sha256"],
            input_format="digital_pdf",
            title="Tables",
            raw_bytes=tbl_bytes,
            limits=limits,
        )
        doc_par = par.extract(
            tenant_id=tenant,
            source_id=source_id,
            version_id=version_id,
            source_version=1,
            source_sha256=tbl_item["sha256"],
            input_format="digital_pdf",
            title="Tables",
            raw_bytes=tbl_bytes,
            limits=limits,
        )

        assert doc_par.normalized_text == doc_seq.normalized_text
        assert doc_par.text_blocks == doc_seq.text_blocks
        assert doc_par.tables == doc_seq.tables
        assert doc_par.sections == doc_seq.sections
        assert doc_par.evidence_references == doc_seq.evidence_references
        assert [p.page_number for p in doc_par.pages] == [1, 2]

        # Cooperative cancellation aborts parallel extraction cleanly
        calls = 0

        def _cancel_after_triage() -> bool:
            nonlocal calls
            calls += 1
            return calls >= 2

        with pytest.raises(ExtractionCancelledError):
            par.extract(
                tenant_id=tenant,
                source_id=source_id,
                version_id=version_id,
                source_version=1,
                source_sha256=tbl_item["sha256"],
                input_format="digital_pdf",
                title="TablesCancel",
                raw_bytes=tbl_bytes,
                limits=limits,
                cancel_check=_cancel_after_triage,
            )
    finally:
        par.close()


def test_scoped_cache_keys_rebind_and_error_sanitization() -> None:
    offering_a, offering_b = uuid4(), uuid4()
    student_1, student_2 = uuid4(), uuid4()

    scope_t = build_scope_key(
        offering_id=offering_a, origin_kind="teacher_resource", owner_user_id=student_1
    )
    scope_s1 = build_scope_key(
        offering_id=offering_a, origin_kind="student_submission", owner_user_id=student_1
    )
    scope_s2 = build_scope_key(
        offering_id=offering_a, origin_kind="student_submission", owner_user_id=student_2
    )
    scope_ob = build_scope_key(
        offering_id=offering_b, origin_kind="teacher_resource", owner_user_id=student_1
    )
    assert len({scope_t, scope_s1, scope_s2, scope_ob}) == 4

    cfg1 = ExtractionCacheConfig(
        ocr_enabled=True,
        ocr_engine_id="viora.sources.rapidocr_onnx",
        ocr_engine_version="3.10",
        ocr_dpi=150,
        enable_layout_fallback=True,
        enable_table_extraction=True,
        max_fallback_pages=50,
        max_ocr_pages=10,
        max_text_chars=200_000,
        min_text_chars=3,
    )
    cfg2 = ExtractionCacheConfig(
        ocr_enabled=False,
        ocr_engine_id="disabled",
        ocr_engine_version="0",
        ocr_dpi=150,
        enable_layout_fallback=True,
        enable_table_extraction=True,
        max_fallback_pages=50,
        max_ocr_pages=10,
        max_text_chars=200_000,
        min_text_chars=3,
    )
    sha = "a" * 64
    k_t = build_extraction_cache_key(
        scope_key=scope_t, content_sha256=sha, input_format="digital_pdf", config=cfg1
    )
    k_s1 = build_extraction_cache_key(
        scope_key=scope_s1, content_sha256=sha, input_format="digital_pdf", config=cfg1
    )
    k_no_ocr = build_extraction_cache_key(
        scope_key=scope_t, content_sha256=sha, input_format="digital_pdf", config=cfg2
    )
    assert len({k_t, k_s1, k_no_ocr}) == 3

    pk1 = build_page_ocr_cache_key(
        scope_key=scope_t,
        page_image_sha256="b" * 64,
        ocr_engine_id="viora.sources.rapidocr_onnx",
        ocr_engine_version="3.10",
        ocr_dpi=150,
    )
    pk2 = build_page_ocr_cache_key(
        scope_key=scope_s1,
        page_image_sha256="b" * 64,
        ocr_engine_id="viora.sources.rapidocr_onnx",
        ocr_engine_version="3.10",
        ocr_dpi=150,
    )
    assert pk1 != pk2

    # Rebinding cached ExtractedDocument updates source_id, version_id, and EvidenceReferences
    limits = clamp_source_limits()
    ext = AdaptiveDocumentExtractor()
    t1, s1, v1 = uuid4(), uuid4(), uuid4()
    doc1 = ext.extract(
        tenant_id=t1,
        source_id=s1,
        version_id=v1,
        source_version=1,
        source_sha256="c" * 64,
        input_format="typed_notes",
        title="Original",
        raw_bytes=b"# Heading\nNewton second law F = ma.",
        limits=limits,
    )
    s2, v2 = uuid4(), uuid4()
    rebound = rebind_cached_document(
        doc1,
        tenant_id=t1,
        source_id=s2,
        version_id=v2,
        source_version=2,
        source_sha256="c" * 64,
        title="Rebound Title",
        created_at=datetime.now(UTC),
    )
    assert rebound.source_id == s2
    assert rebound.version_id == v2
    assert rebound.source_version == 2
    assert rebound.title == "Rebound Title"
    assert rebound.evidence_references
    assert all(
        ev.source_id == s2 and ev.version_id == v2 for ev in rebound.evidence_references
    )

    # Error sanitization strips file paths, DSNs, and secret tokens
    assert "C:\\" not in sanitize_error_message("Failed opening C:\\Users\\secret\\file.pdf")
    assert "postgresql" not in sanitize_error_message(
        "Error at postgresql+psycopg://user:pass@localhost/db"
    )
    assert sanitize_error_message("Transient OCR timeout") == "Transient OCR timeout"


def test_low_confidence_and_unsupported_script_warnings_and_optional_live_ocr() -> None:
    limits = clamp_source_limits()

    class _LowConfOcr:
        @property
        def engine_id(self) -> str:
            return "test.low_conf"

        @property
        def engine_version(self) -> str:
            return "1.0"

        def is_available(self) -> bool:
            return True

        def recognize_image(
            self, image: Image.Image, *, preprocess: bool = False
        ) -> OcrPageResult:
            _ = preprocess
            return OcrPageResult(
                lines=(OcrLine("garbled ?", 0.42, 5.0, 5.0, 50.0, 20.0),),
                image_width=image.width,
                image_height=image.height,
                engine_id=self.engine_id,
                engine_version=self.engine_version,
                mean_confidence=0.42,
            )

    buf = io.BytesIO()
    Image.new("RGB", (120, 60), "white").save(buf, format="PNG")
    ext = AdaptiveDocumentExtractor(ocr_backend=_LowConfOcr())
    doc = ext.extract(
        tenant_id=uuid4(),
        source_id=uuid4(),
        version_id=uuid4(),
        source_version=1,
        source_sha256="d" * 64,
        input_format="png",
        title="Handwritten",
        raw_bytes=buf.getvalue(),
        limits=limits,
    )
    assert any(w.code == "unsupported_script_or_handwriting" for w in doc.warnings)

    # Live RapidOCR verification when optional worker dependency is installed
    real_ocr = get_process_ocr_backend(warm_up=False)
    if real_ocr.is_available():
        _, scan_png = _load_sample("07_scanned_printed_page_png")
        live_ext = AdaptiveDocumentExtractor(ocr_backend=real_ocr, page_workers=2)
        live_doc = live_ext.extract(
            tenant_id=uuid4(),
            source_id=uuid4(),
            version_id=uuid4(),
            source_version=1,
            source_sha256="e" * 64,
            input_format="png",
            title="LiveScan",
            raw_bytes=scan_png,
            limits=limits,
        )
        assert live_doc.quality.usable_for_digital_ingestion is True
        assert "Conservation of Mechanical Energy" in live_doc.normalized_text


def test_single_worker_isolated_untrusted_parse_and_reconcile_orphans(
    tmp_path: Path,
) -> None:
    import os
    import time

    from viora.sources.cache import reconcile_orphan_artifacts

    limits = clamp_source_limits()
    _, pdf_bytes = _load_sample("02_single_column_textbook")
    ext = AdaptiveDocumentExtractor(
        page_workers=1,
        isolate_untrusted_parse=True,
        intra_op_thread_budget=1,
    )
    try:
        ext.warm_pool(warm_ocr=False)
        doc = ext.extract(
            tenant_id=uuid4(),
            source_id=uuid4(),
            version_id=uuid4(),
            source_version=1,
            source_sha256="f" * 64,
            input_format="digital_pdf",
            title="Isolated1P",
            raw_bytes=pdf_bytes,
            limits=limits,
        )
        assert "Wave Optics and Interference" in doc.normalized_text
    finally:
        ext.close()

    t_id, res_keep, ver_keep = uuid4(), uuid4(), uuid4()
    res_orphan, ver_orphan = uuid4(), uuid4()
    keep_dir = tmp_path / "tenants" / str(t_id) / str(res_keep)
    orphan_dir = tmp_path / "tenants" / str(t_id) / str(res_orphan)
    keep_dir.mkdir(parents=True)
    orphan_dir.mkdir(parents=True)
    keep_blob = keep_dir / f"{ver_keep}.blob"
    orphan_blob = orphan_dir / f"{ver_orphan}.blob"
    orphan_meta = orphan_dir / f"{ver_orphan}.meta.json"
    keep_blob.write_bytes(b"live")
    orphan_blob.write_bytes(b"orphan")
    orphan_meta.write_text("{}", encoding="utf-8")
    old = time.time() - 7200
    os.utime(keep_blob, (old, old))
    os.utime(orphan_blob, (old, old))
    os.utime(orphan_meta, (old, old))

    summary = reconcile_orphan_artifacts(
        tmp_path,
        list_referenced_storage_keys=lambda _tid: {(res_keep, ver_keep)},
        min_age_seconds=3600,
    )
    assert summary["blobs_removed"] == 1
    assert summary["metas_removed"] == 1
    assert keep_blob.exists()
    assert not orphan_blob.exists()
    assert not orphan_meta.exists()
