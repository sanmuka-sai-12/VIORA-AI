"""Representative educational corpus, adaptive extraction, OCR fallback, and regression tests (Task F)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from PIL import Image
from viora.contracts import TenantContext
from viora.security import AccessError
from viora.sources.document_contracts import DerivedExtractionRecord
from viora.sources.extraction import AdaptiveDocumentExtractor
from viora.sources.ocr import (
    OcrLine,
    OcrPageResult,
    OcrUnavailableError,
    RapidOcrBackend,
)
from viora.sources.schemas import clamp_source_limits
from viora.storage.public import local_artifact_store

REPO_ROOT = Path(__file__).resolve().parents[2]
CORPUS_DIR = REPO_ROOT / "benchmarks" / "educational_corpus"
MANIFEST_PATH = CORPUS_DIR / "ground_truth.json"


def _load_sample(doc_id: str) -> tuple[dict, bytes]:
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    item = next(d for d in manifest["documents"] if d["doc_id"] == doc_id)
    return item, (CORPUS_DIR / item["relative_path"]).read_bytes()


class _RecordingOcrBackend:
    """Deterministic fake OCR backend proving selective page routing and coordinate scaling."""

    def __init__(self, text_lines: tuple[str, ...]) -> None:
        self.text_lines = text_lines
        self.calls: list[tuple[int, int]] = []

    @property
    def engine_id(self) -> str:
        return "test.ocr.stub"

    @property
    def engine_version(self) -> str:
        return "1.0"

    def is_available(self) -> bool:
        return True

    def recognize_image(
        self, image: Image.Image, *, preprocess: bool = False
    ) -> OcrPageResult:
        _ = preprocess
        self.calls.append(image.size)
        lines = tuple(
            OcrLine(
                text=txt,
                confidence=0.94,
                x0=20.0,
                y0=20.0 + idx * 30.0,
                x1=float(max(40, image.width - 20)),
                y1=45.0 + idx * 30.0,
            )
            for idx, txt in enumerate(self.text_lines)
        )
        return OcrPageResult(
            lines=lines,
            image_width=image.width,
            image_height=image.height,
            engine_id=self.engine_id,
            engine_version=self.engine_version,
            mean_confidence=0.94,
        )


class _UnavailableOcrBackend:
    @property
    def engine_id(self) -> str:
        return "test.ocr.unavailable"

    @property
    def engine_version(self) -> str:
        return "0.0"

    def is_available(self) -> bool:
        return False

    def recognize_image(
        self, image: Image.Image, *, preprocess: bool = False
    ) -> OcrPageResult:
        _ = (image, preprocess)
        raise OcrUnavailableError("OCR runtime unavailable")


def test_multicolumn_reading_order_code_blocks_and_repeated_headers() -> None:
    ext = AdaptiveDocumentExtractor()
    limits = clamp_source_limits()
    tenant = uuid4()

    mc_item, mc_bytes = _load_sample("03_multicolumn_scientific")
    mc_doc = ext.extract(
        tenant_id=tenant,
        source_id=uuid4(),
        version_id=uuid4(),
        source_version=1,
        source_sha256=mc_item["sha256"],
        input_format="digital_pdf",
        title="Multi-column Scientific",
        raw_bytes=mc_bytes,
        limits=limits,
    )
    assert mc_doc.quality.reading_order_verified is True
    markers = mc_item["ground_truth"]["expected_ordered_markers"]
    positions = [mc_doc.normalized_text.find(m) for m in markers]
    assert all(pos >= 0 for pos in positions)
    assert positions == sorted(positions)

    # Code blocks preserve indentation in both Markdown and monospace PDF
    md_item, md_bytes = _load_sample("01_markdown_notes")
    md_doc = ext.extract(
        tenant_id=tenant,
        source_id=uuid4(),
        version_id=uuid4(),
        source_version=1,
        source_sha256=md_item["sha256"],
        input_format="typed_notes",
        title="MD Notes",
        raw_bytes=md_bytes,
        limits=limits,
    )
    assert any(b.kind == "code" and "    return x0" in b.normalized_text for b in md_doc.text_blocks)
    assert len(md_doc.formulas) >= 2

    code_item, code_bytes = _load_sample("04_code_blocks")
    code_doc = ext.extract(
        tenant_id=tenant,
        source_id=uuid4(),
        version_id=uuid4(),
        source_version=1,
        source_sha256=code_item["sha256"],
        input_format="digital_pdf",
        title="Binary Search PDF",
        raw_bytes=code_bytes,
        limits=limits,
    )
    assert any(
        b.kind == "code" and "    lo, hi = 0, len(nums) - 1" in b.normalized_text
        for b in code_doc.text_blocks
    )

    # Repeated headers/footers separated cleanly without losing provenance
    tb_item, tb_bytes = _load_sample("02_single_column_textbook")
    tb_doc = ext.extract(
        tenant_id=tenant,
        source_id=uuid4(),
        version_id=uuid4(),
        source_version=1,
        source_sha256=tb_item["sha256"],
        input_format="digital_pdf",
        title="Textbook",
        raw_bytes=tb_bytes,
        limits=limits,
    )
    assert any(b.kind == "header_or_footer" for b in tb_doc.text_blocks)
    assert any(w.code == "repeated_header_footer_suspected" for w in tb_doc.warnings)


def test_bordered_and_borderless_tables_math_and_diagrams_preservation() -> None:
    ext = AdaptiveDocumentExtractor()
    limits = clamp_source_limits()
    tenant = uuid4()

    tbl_item, tbl_bytes = _load_sample("05_tables_bordered_and_borderless")
    tbl_doc = ext.extract(
        tenant_id=tenant,
        source_id=uuid4(),
        version_id=uuid4(),
        source_version=1,
        source_sha256=tbl_item["sha256"],
        input_format="digital_pdf",
        title="Tables",
        raw_bytes=tbl_bytes,
        limits=limits,
    )
    assert len(tbl_doc.tables) == 2
    t1, t2 = tbl_doc.tables
    assert t1.extraction_state == "parsed"
    assert t1.headers == ("Gas", "MolarMass", "Cv_J_molK", "Gamma")
    assert len(t1.rows) == 4
    assert t1.caption and "Table 1" in t1.caption
    assert t1.bbox is not None
    assert t2.extraction_state == "parsed"
    assert t2.headers == ("Algorithm", "BestCase", "WorstCase", "Space")
    assert len(t2.rows) == 4
    assert any(w.code == "table_extraction_required" for w in tbl_doc.warnings)

    math_item, math_bytes = _load_sample("06_math_equations")
    math_doc = ext.extract(
        tenant_id=tenant,
        source_id=uuid4(),
        version_id=uuid4(),
        source_version=1,
        source_sha256=math_item["sha256"],
        input_format="digital_pdf",
        title="Math",
        raw_bytes=math_bytes,
        limits=limits,
    )
    for sym in math_item["ground_truth"]["expected_unicode_symbols"]:
        assert sym in math_doc.normalized_text
    assert any(f.latex == "E = mc^2" for f in math_doc.formulas)

    diag_item, diag_bytes = _load_sample("10_diagrams_charts_graphs_pdf")
    diag_doc = ext.extract(
        tenant_id=tenant,
        source_id=uuid4(),
        version_id=uuid4(),
        source_version=1,
        source_sha256=diag_item["sha256"],
        input_format="digital_pdf",
        title="Diagrams",
        raw_bytes=diag_bytes,
        limits=limits,
    )
    assert len(diag_doc.figures) == 2
    assert diag_doc.figures[0].kind == "vector_diagram"
    assert diag_doc.figures[0].caption and "Figure 1" in diag_doc.figures[0].caption
    assert diag_doc.figures[0].interpretation_status == "caption_only"
    assert diag_doc.figures[0].alt_text_or_description is None
    assert diag_doc.figures[1].kind == "raster_image"
    assert diag_doc.figures[1].caption and "Figure 2" in diag_doc.figures[1].caption


def test_mixed_digital_scanned_selective_ocr_and_unavailable_ocr_behavior() -> None:
    limits = clamp_source_limits()
    tenant = uuid4()
    mix_item, mix_bytes = _load_sample("11_mixed_digital_scanned")

    # 1. Without OCR backend: digital pages succeed, scanned page 2 emits ocr_required
    no_ocr_ext = AdaptiveDocumentExtractor()
    no_ocr_doc = no_ocr_ext.extract(
        tenant_id=tenant,
        source_id=uuid4(),
        version_id=uuid4(),
        source_version=1,
        source_sha256=mix_item["sha256"],
        input_format="digital_pdf",
        title="MixedNoOCR",
        raw_bytes=mix_bytes,
        limits=limits,
    )
    assert no_ocr_doc.quality.overall_status == "partial_with_warnings"
    assert no_ocr_doc.quality.usable_for_digital_ingestion is True
    assert no_ocr_doc.pages[1].classification == "needs_ocr"
    assert any(w.code == "ocr_required" and w.page_number == 2 for w in no_ocr_doc.warnings)

    # 2. With unavailable OCR backend: emits ocr_unavailable and never fabricates OCR
    unavail_ext = AdaptiveDocumentExtractor(ocr_backend=_UnavailableOcrBackend())
    _, scan_png_bytes = _load_sample("07_scanned_printed_page_png")
    unavail_doc = unavail_ext.extract(
        tenant_id=tenant,
        source_id=uuid4(),
        version_id=uuid4(),
        source_version=1,
        source_sha256="a" * 64,
        input_format="png",
        title="ScanPNG",
        raw_bytes=scan_png_bytes,
        limits=limits,
    )
    assert unavail_doc.quality.overall_status == "ocr_required"
    assert unavail_doc.quality.usable_for_digital_ingestion is False
    assert unavail_doc.normalized_text == ""
    assert any(w.code == "ocr_unavailable" for w in unavail_doc.warnings)

    # Verify real RapidOcrBackend behaves cleanly when optional package is absent (or present)
    real_ocr = RapidOcrBackend()
    if not real_ocr.is_available():
        with pytest.raises(OcrUnavailableError):
            real_ocr.recognize_image(Image.new("RGB", (32, 32), "white"))

    # 3. With active OCR backend: ONLY scanned page 2 is sent to OCR (not pages 1 or 3!)
    rec_ocr = _RecordingOcrBackend(("Conservation of Mechanical Energy", "K + U = const"))
    ocr_ext = AdaptiveDocumentExtractor(ocr_backend=rec_ocr)
    ocr_doc = ocr_ext.extract(
        tenant_id=tenant,
        source_id=uuid4(),
        version_id=uuid4(),
        source_version=1,
        source_sha256=mix_item["sha256"],
        input_format="digital_pdf",
        title="MixedWithOCR",
        raw_bytes=mix_bytes,
        limits=limits,
    )
    assert len(rec_ocr.calls) == 1
    assert "Conservation of Mechanical Energy" in ocr_doc.normalized_text
    assert len(ocr_doc.tables) == 1
    assert any(w.code == "ocr_fallback_applied" and w.page_number == 2 for w in ocr_doc.warnings)
    # Page 2 OCR blocks carry scaled pdf_points bounding boxes
    p2_blocks = [b for b in ocr_doc.text_blocks if b.page_number == 2]
    assert p2_blocks and all(
        b.bbox is not None and b.bbox.coordinate_space == "pdf_points" for b in p2_blocks
    )


def test_evidence_consistency_determinism_resource_limits_and_derived_contract(
    tmp_path: Path,
) -> None:
    limits = clamp_source_limits()
    tenant = uuid4()
    source_id = uuid4()
    version_id = uuid4()
    ext = AdaptiveDocumentExtractor()

    # Determinism + coordinate/evidence consistency across digital & multilingual documents
    for doc_id in ("03_multicolumn_scientific", "05_tables_bordered_and_borderless", "12_multilingual_md"):
        item, raw = _load_sample(doc_id)
        doc1 = ext.extract(
            tenant_id=tenant,
            source_id=source_id,
            version_id=version_id,
            source_version=1,
            source_sha256=item["sha256"],
            input_format=item["input_format"],  # type: ignore[arg-type]
            title=item["doc_id"],
            raw_bytes=raw,
            limits=limits,
        )
        doc2 = ext.extract(
            tenant_id=tenant,
            source_id=source_id,
            version_id=version_id,
            source_version=1,
            source_sha256=item["sha256"],
            input_format=item["input_format"],  # type: ignore[arg-type]
            title=item["doc_id"],
            raw_bytes=raw,
            limits=limits,
        )
        assert doc1.normalized_text == doc2.normalized_text
        assert doc1.text_blocks == doc2.text_blocks
        assert doc1.tables == doc2.tables
        assert doc1.quality == doc2.quality

        blk_map = {b.block_id: b for b in doc1.text_blocks}
        for ev in doc1.evidence_references:
            if ev.span is not None and ev.block_id and ev.block_id in blk_map:
                assert (
                    doc1.normalized_text[
                        ev.span.normalized_start_offset : ev.span.normalized_end_offset
                    ]
                    == blk_map[ev.block_id].normalized_text
                )
            if ev.bbox is not None and ev.bbox.page_width and ev.bbox.page_height:
                assert 0.0 <= ev.bbox.x0 <= ev.bbox.x1 <= ev.bbox.page_width + 0.1
                assert 0.0 <= ev.bbox.y0 <= ev.bbox.y1 <= ev.bbox.page_height + 0.1

    # Malformed image rejected cleanly
    with pytest.raises(AccessError) as exc:
        ext.extract(
            tenant_id=tenant,
            source_id=source_id,
            version_id=version_id,
            source_version=1,
            source_sha256="a" * 64,
            input_format="png",
            title="BadImage",
            raw_bytes=b"not-a-valid-png",
            limits=limits,
        )
    assert exc.value.code == "malformed_image"

    # Bounded fallback page limit respected with explicit truncation_warning
    _, tbl_bytes = _load_sample("05_tables_bordered_and_borderless")
    capped_ext = AdaptiveDocumentExtractor(max_fallback_pages=1)
    capped_doc = capped_ext.extract(
        tenant_id=tenant,
        source_id=source_id,
        version_id=version_id,
        source_version=1,
        source_sha256="b" * 64,
        input_format="digital_pdf",
        title="CappedFallback",
        raw_bytes=tbl_bytes,
        limits=limits,
    )
    assert any(w.code == "truncation_warning" and w.page_number == 2 for w in capped_doc.warnings)

    # Preserve immutable original source bytes while building a versioned DerivedExtractionRecord
    store = local_artifact_store(tmp_path / "artifacts")
    ctx = TenantContext(actor_id=uuid4(), tenant_id=tenant, roles=frozenset({"subject_teacher"}))
    _mix_item, mix_bytes = _load_sample("11_mixed_digital_scanned")
    stored = store.put_bytes(
        ctx,
        mix_bytes,
        kind="source_input",
        resource_id=source_id,
        content_type="application/pdf",
        artifact_id=source_id,
        version_id=version_id,
    )
    re_read = store.read(ctx, stored.metadata.ref)
    assert re_read == mix_bytes

    derived_doc = AdaptiveDocumentExtractor(
        ocr_backend=_RecordingOcrBackend(("Derived OCR Line",))
    ).extract(
        tenant_id=tenant,
        source_id=source_id,
        version_id=version_id,
        source_version=1,
        source_sha256=stored.metadata.ref.sha256,
        input_format="digital_pdf",
        title="Mixed",
        raw_bytes=re_read,
        limits=limits,
    )
    derived_rec = DerivedExtractionRecord(
        extraction_id=uuid4(),
        tenant_id=tenant,
        source_id=source_id,
        version_id=version_id,
        source_sha256=stored.metadata.ref.sha256,
        extraction_version=2,
        document=derived_doc,
        created_at=datetime.now(UTC),
    )
    reloaded = DerivedExtractionRecord.model_validate_json(derived_rec.model_dump_json())
    assert reloaded == derived_rec
    assert store.read(ctx, stored.metadata.ref) == mix_bytes
