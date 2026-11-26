"""Reproducible Batch 2.3 adaptive extraction, quality, memory, and OCR benchmark suite.

Measures:
- Native text extraction vs pypdf fast path vs unconditional full pdfplumber layout vs adaptive selective extraction.
- P50/P95/P99 latency, pages/sec, CPU utilization, Python heap (tracemalloc), and real OS peak RSS memory.
- Cold initialization vs warm execution.
- Quality metrics against ground truth: reading-order accuracy, table cell accuracy, evidence accuracy,
  completeness, routing accuracy / fallback frequency, and OCR CER/WER.
"""

from __future__ import annotations

import io
import json
import subprocess
import sys
import time
import tracemalloc
from pathlib import Path
from typing import Any
from uuid import uuid4

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))
sys.path.insert(0, str(REPO_ROOT / "backend"))

import pdfplumber
from _common import compute_cer_wer, get_process_memory_mb
from viora.perf.public import build_synthetic_corpus, percentile
from viora.sources.extraction import AdaptiveDocumentExtractor
from viora.sources.ocr import RapidOcrBackend, decode_bounded_image
from viora.sources.schemas import clamp_source_limits
from viora.sources.spatial_layout import analyze_page_spatial

GROUND_TRUTH_PATH = REPO_ROOT / "benchmarks" / "educational_corpus" / "ground_truth.json"


def reading_order_accuracy(text: str, ordered_markers: list[str]) -> float:
    if len(ordered_markers) < 2:
        return 1.0 if (not ordered_markers or ordered_markers[0] in text) else 0.0
    positions = [text.find(m) for m in ordered_markers]
    pairs_total = 0
    pairs_ok = 0
    for i in range(len(positions)):
        for j in range(i + 1, len(positions)):
            pairs_total += 1
            if positions[i] != -1 and positions[j] != -1 and positions[i] < positions[j]:
                pairs_ok += 1
    return round(pairs_ok / max(1, pairs_total), 4)


def table_cell_accuracy(doc: Any, expected_tables: list[dict[str, Any]]) -> float:
    if not expected_tables:
        return 1.0
    total_cells = 0
    matched_cells = 0
    for idx, exp in enumerate(expected_tables):
        exp_rows = [exp["headers"], *exp["rows"]] if exp.get("headers") else list(exp["rows"])
        total_cells += sum(len(r) for r in exp_rows)
        if idx >= len(doc.tables):
            continue
        actual_tbl = doc.tables[idx]
        act_rows = (
            [list(actual_tbl.headers), *[list(r) for r in actual_tbl.rows]]
            if actual_tbl.headers
            else [list(r) for r in actual_tbl.rows]
        )
        for r_i, exp_r in enumerate(exp_rows):
            if r_i >= len(act_rows):
                continue
            for c_i, exp_cell in enumerate(exp_r):
                if c_i < len(act_rows[r_i]) and act_rows[r_i][c_i].strip() == str(exp_cell).strip():
                    matched_cells += 1
    return round(matched_cells / max(1, total_cells), 4)


def evidence_accuracy(doc: Any) -> float:
    if not doc.evidence_references:
        return 1.0
    blk_map = {b.block_id: b for b in doc.text_blocks}
    valid = 0
    for ev in doc.evidence_references:
        ok = True
        if ev.span is not None and ev.block_id and ev.block_id in blk_map:
            blk = blk_map[ev.block_id]
            sliced = doc.normalized_text[
                ev.span.normalized_start_offset : ev.span.normalized_end_offset
            ]
            if sliced != blk.normalized_text:
                ok = False
        if (
            ev.bbox is not None
            and ev.bbox.page_width
            and ev.bbox.page_height
            and not (
                0.0 <= ev.bbox.x0 <= ev.bbox.x1 <= ev.bbox.page_width + 0.1
                and 0.0 <= ev.bbox.y0 <= ev.bbox.y1 <= ev.bbox.page_height + 0.1
            )
        ):
            ok = False
        if ok:
            valid += 1
    return round(valid / len(doc.evidence_references), 4)


def _full_pdfplumber_baseline(raw_bytes: bytes) -> tuple[str, int]:
    """Unconditional full pdfplumber layout+table pass across ALL pages."""
    parts: list[str] = []
    with pdfplumber.open(io.BytesIO(raw_bytes)) as pdf:
        for page in pdf.pages:
            sp = analyze_page_spatial(page, extract_tables=True, allow_borderless_tables=True)
            for b in sp.blocks:
                if b.kind != "header_or_footer":
                    parts.append(b.text)
            for t in sp.tables:
                if t.headers:
                    parts.append(" | ".join(t.headers))
                for r in t.rows:
                    parts.append(" | ".join(r))
        return "\n\n".join(parts), len(pdf.pages)


def _time_workload(
    name: str,
    fn: Any,
    *,
    iterations: int,
    pages_per_call: int,
    characteristics: dict[str, Any],
) -> dict[str, Any]:
    latencies: list[float] = []
    tracemalloc.start()
    tracemalloc.reset_peak()
    cpu_start = time.process_time()
    wall_start = time.monotonic_ns()
    for _ in range(iterations):
        t0 = time.monotonic_ns()
        fn()
        latencies.append((time.monotonic_ns() - t0) / 1_000_000.0)
    wall_sec = max(1e-9, (time.monotonic_ns() - wall_start) / 1_000_000_000.0)
    cpu_sec = max(0.0, time.process_time() - cpu_start)
    _, py_peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    latencies.sort()
    os_mem = get_process_memory_mb()
    return {
        "workload": name,
        "iterations": iterations,
        "p50_ms": round(percentile(latencies, 50.0), 3),
        "p95_ms": round(percentile(latencies, 95.0), 3),
        "p99_ms": round(percentile(latencies, 99.0), 3),
        "mean_ms": round(sum(latencies) / len(latencies), 3),
        "pages_per_sec": round((iterations * pages_per_call) / wall_sec, 2),
        "cpu_utilization_pct": round((cpu_sec / wall_sec) * 100.0, 1),
        "python_heap_peak_kb": round(py_peak / 1024.0, 2),
        "process_rss_mb": os_mem["rss_mb"],
        "process_peak_rss_mb": os_mem["peak_rss_mb"],
        "characteristics": characteristics,
    }


def _measure_cold_init_subprocess(mode: str) -> dict[str, Any]:
    code = (
        "import ctypes, ctypes.wintypes, json, sys, time\n"
        f"sys.path.insert(0, {str(REPO_ROOT / 'backend')!r})\n"
        "t0 = time.monotonic_ns()\n"
        "from viora.sources.extraction import AdaptiveDocumentExtractor\n"
        "from viora.sources.ocr import RapidOcrBackend\n"
        "ext = AdaptiveDocumentExtractor()\n"
        "if 'ocr' == " + repr(mode) + ":\n"
        "    b = RapidOcrBackend()\n"
        "    if b.is_available():\n"
        "        b._get_engine()\n"
        "dt_ms = (time.monotonic_ns() - t0) / 1_000_000.0\n"
        "class PMC(ctypes.Structure):\n"
        "    _fields_ = [('cb', ctypes.wintypes.DWORD), ('pfc', ctypes.wintypes.DWORD), ('pws', ctypes.c_size_t), ('ws', ctypes.c_size_t), ('q1', ctypes.c_size_t), ('q2', ctypes.c_size_t), ('q3', ctypes.c_size_t), ('q4', ctypes.c_size_t), ('pf', ctypes.c_size_t), ('ppf', ctypes.c_size_t)]\n"
        "pmc = PMC(); pmc.cb = ctypes.sizeof(pmc)\n"
        "k32 = ctypes.windll.kernel32\n"
        "k32.GetCurrentProcess.restype = ctypes.wintypes.HANDLE\n"
        "k32.K32GetProcessMemoryInfo.argtypes = [ctypes.wintypes.HANDLE, ctypes.POINTER(PMC), ctypes.wintypes.DWORD]\n"
        "k32.K32GetProcessMemoryInfo.restype = ctypes.wintypes.BOOL\n"
        "k32.K32GetProcessMemoryInfo(k32.GetCurrentProcess(), ctypes.byref(pmc), pmc.cb)\n"
        "print(json.dumps({'cold_init_ms': round(dt_ms, 2), 'peak_rss_mb': round(pmc.pws/(1024*1024), 2)}))\n"
    )
    out = subprocess.check_output([sys.executable, "-c", code], text=True)
    return json.loads(out.strip().splitlines()[-1])


def main() -> int:
    manifest = json.loads(GROUND_TRUTH_PATH.read_text(encoding="utf-8"))
    limits = clamp_source_limits()
    synth = build_synthetic_corpus()

    ocr_backend = RapidOcrBackend()
    ocr_available = ocr_backend.is_available()
    fast_only = AdaptiveDocumentExtractor(enable_layout_fallback=False)
    adaptive = AdaptiveDocumentExtractor()
    adaptive_ocr = AdaptiveDocumentExtractor(ocr_backend=ocr_backend) if ocr_available else None

    # 1. Cold initialization vs warm execution
    cold_adaptive = _measure_cold_init_subprocess("adaptive")
    cold_ocr = _measure_cold_init_subprocess("ocr") if ocr_available else None

    # 2. Latency & memory comparisons across identical workloads
    perf_rows: list[dict[str, Any]] = []
    tenant = uuid4()

    # Workload A: 12-page clean single-column digital PDF (shows fast-path speedup!)
    perf_rows.append(
        _time_workload(
            "12p_clean_pdf__fast_pypdf",
            lambda: fast_only.extract(
                tenant_id=tenant, source_id=uuid4(), version_id=uuid4(), source_version=1,
                source_sha256="a" * 64, input_format="digital_pdf", title="Clean12p",
                raw_bytes=synth.multipage_pdf, limits=limits,
            ),
            iterations=15,
            pages_per_call=12,
            characteristics={"pages": 12, "bytes": len(synth.multipage_pdf), "strategy": "pypdf_only"},
        )
    )
    perf_rows.append(
        _time_workload(
            "12p_clean_pdf__full_pdfplumber",
            lambda: _full_pdfplumber_baseline(synth.multipage_pdf),
            iterations=15,
            pages_per_call=12,
            characteristics={"pages": 12, "bytes": len(synth.multipage_pdf), "strategy": "full_layout"},
        )
    )
    perf_rows.append(
        _time_workload(
            "12p_clean_pdf__adaptive_selective",
            lambda: adaptive.extract(
                tenant_id=tenant, source_id=uuid4(), version_id=uuid4(), source_version=1,
                source_sha256="a" * 64, input_format="digital_pdf", title="Clean12p",
                raw_bytes=synth.multipage_pdf, limits=limits,
            ),
            iterations=15,
            pages_per_call=12,
            characteristics={"pages": 12, "bytes": len(synth.multipage_pdf), "strategy": "adaptive"},
        )
    )

    # Workload B: Multi-column scientific PDF
    mc_bytes = (REPO_ROOT / "benchmarks" / "educational_corpus" / "samples" / "03_multicolumn_scientific.pdf").read_bytes()
    for label, fn in (
        ("multicolumn_pdf__fast_pypdf", lambda: fast_only.extract(tenant_id=tenant, source_id=uuid4(), version_id=uuid4(), source_version=1, source_sha256="b"*64, input_format="digital_pdf", title="MC", raw_bytes=mc_bytes, limits=limits)),
        ("multicolumn_pdf__full_pdfplumber", lambda: _full_pdfplumber_baseline(mc_bytes)),
        ("multicolumn_pdf__adaptive_selective", lambda: adaptive.extract(tenant_id=tenant, source_id=uuid4(), version_id=uuid4(), source_version=1, source_sha256="b"*64, input_format="digital_pdf", title="MC", raw_bytes=mc_bytes, limits=limits)),
    ):
        perf_rows.append(
            _time_workload(label, fn, iterations=15, pages_per_call=1, characteristics={"pages": 1, "bytes": len(mc_bytes)})
        )

    # Workload C: Bordered + borderless tables PDF (2 pages)
    tbl_bytes = (REPO_ROOT / "benchmarks" / "educational_corpus" / "samples" / "05_tables_bordered_and_borderless.pdf").read_bytes()
    for label, fn in (
        ("tables_2p_pdf__fast_pypdf", lambda: fast_only.extract(tenant_id=tenant, source_id=uuid4(), version_id=uuid4(), source_version=1, source_sha256="c"*64, input_format="digital_pdf", title="Tables", raw_bytes=tbl_bytes, limits=limits)),
        ("tables_2p_pdf__full_pdfplumber", lambda: _full_pdfplumber_baseline(tbl_bytes)),
        ("tables_2p_pdf__adaptive_selective", lambda: adaptive.extract(tenant_id=tenant, source_id=uuid4(), version_id=uuid4(), source_version=1, source_sha256="c"*64, input_format="digital_pdf", title="Tables", raw_bytes=tbl_bytes, limits=limits)),
    ):
        perf_rows.append(
            _time_workload(label, fn, iterations=15, pages_per_call=2, characteristics={"pages": 2, "bytes": len(tbl_bytes)})
        )

    # Workload D: Native markdown extraction
    md_bytes = (REPO_ROOT / "benchmarks" / "educational_corpus" / "samples" / "01_markdown_notes.md").read_bytes()
    perf_rows.append(
        _time_workload(
            "markdown_notes__native_text",
            lambda: adaptive.extract(tenant_id=tenant, source_id=uuid4(), version_id=uuid4(), source_version=1, source_sha256="d"*64, input_format="typed_notes", title="MD", raw_bytes=md_bytes, limits=limits),
            iterations=30,
            pages_per_call=1,
            characteristics={"pages": 1, "bytes": len(md_bytes)},
        )
    )

    # Workload E: Scanned PDF OCR fallback (when OCR backend available)
    if adaptive_ocr is not None:
        scan_pdf_bytes = (REPO_ROOT / "benchmarks" / "educational_corpus" / "samples" / "07_scanned_printed_page.pdf").read_bytes()
        perf_rows.append(
            _time_workload(
                "scanned_pdf_1p__ocr_fallback",
                lambda: adaptive_ocr.extract(tenant_id=tenant, source_id=uuid4(), version_id=uuid4(), source_version=1, source_sha256="e"*64, input_format="digital_pdf", title="ScanPDF", raw_bytes=scan_pdf_bytes, limits=limits),
                iterations=5,
                pages_per_call=1,
                characteristics={"pages": 1, "bytes": len(scan_pdf_bytes), "dpi": 150},
            )
        )

    # 3. Quality evaluation across representative corpus
    quality_rows: list[dict[str, Any]] = []
    routing_correct = 0
    routing_total = 0
    fallback_pages = 0
    total_pdf_pages = 0

    for item in manifest["documents"]:
        fpath = REPO_ROOT / "benchmarks" / "educational_corpus" / item["relative_path"]
        raw = fpath.read_bytes()
        fmt = item["input_format"]
        gt = item["ground_truth"]

        use_ext = adaptive_ocr if (adaptive_ocr is not None and fmt in {"png", "jpeg", "digital_pdf", "scanned_pdf"}) else adaptive
        doc = use_ext.extract(
            tenant_id=tenant,
            source_id=uuid4(),
            version_id=uuid4(),
            source_version=1,
            source_sha256=item["sha256"],
            input_format=fmt,
            title=item["doc_id"],
            raw_bytes=raw,
            limits=limits,
        )
        fast_doc = fast_only.extract(
            tenant_id=tenant,
            source_id=uuid4(),
            version_id=uuid4(),
            source_version=1,
            source_sha256=item["sha256"],
            input_format=fmt,
            title=item["doc_id"],
            raw_bytes=raw,
            limits=limits,
        )

        if fmt == "digital_pdf":
            total_pdf_pages += doc.quality.total_pages
            fallback_pages += doc.quality.pages_needing_layout + doc.quality.pages_needing_table + doc.quality.pages_needing_ocr

        exp_classes = gt.get("expected_classifications")
        if exp_classes:
            # Check fast_only triage classification accuracy
            for p_actual, p_exp in zip(fast_doc.pages, exp_classes, strict=False):
                routing_total += 1
                if p_actual.classification == p_exp:
                    routing_correct += 1

        row: dict[str, Any] = {
            "doc_id": item["doc_id"],
            "category": item["category"],
            "input_format": fmt,
            "evidence_accuracy": evidence_accuracy(doc),
            "overall_status": doc.quality.overall_status,
            "reading_order_verified": doc.quality.reading_order_verified,
        }
        if "expected_ordered_markers" in gt:
            row["adaptive_reading_order_accuracy"] = reading_order_accuracy(
                doc.normalized_text, gt["expected_ordered_markers"]
            )
            row["fast_pypdf_reading_order_accuracy"] = reading_order_accuracy(
                fast_doc.normalized_text, gt["expected_ordered_markers"]
            )
        if "tables" in gt:
            row["adaptive_table_cell_accuracy"] = table_cell_accuracy(doc, gt["tables"])
            row["fast_pypdf_table_cell_accuracy"] = table_cell_accuracy(fast_doc, gt["tables"])
        markers = gt.get("expected_markers") or gt.get("expected_digital_markers") or gt.get("expected_code_markers")
        if markers:
            row["completeness"] = round(
                sum(1 for m in markers if m in doc.normalized_text) / len(markers), 4
            )
        if "expected_text" in gt and ocr_available and fmt in {"png", "jpeg", "digital_pdf"}:
            cer, wer = compute_cer_wer(gt["expected_text"], doc.normalized_text)
            row["ocr_cer"] = cer
            row["ocr_wer"] = wer

        quality_rows.append(row)

    # 4. OCR preprocessing benchmark (raw vs PIL enhanced vs OpenCV CLAHE+denoise if cv2 installed)
    ocr_benchmarks: list[dict[str, Any]] = []
    if ocr_available:
        try:
            import cv2
            import numpy as np
            from PIL import Image

            has_cv2 = True
        except ImportError:
            has_cv2 = False

        for item in manifest["documents"]:
            if item["input_format"] not in {"png", "jpeg"}:
                continue
            fpath = REPO_ROOT / "benchmarks" / "educational_corpus" / item["relative_path"]
            img = decode_bounded_image(fpath.read_bytes())
            gt_txt = item["ground_truth"].get("expected_text", "")
            variants: list[tuple[str, Any]] = [
                ("raw", lambda im=img: ocr_backend.recognize_image(im, preprocess=False)),
                ("pil_autocontrast_sharpen", lambda im=img: ocr_backend.recognize_image(im, preprocess=True)),
            ]
            if has_cv2:
                def _run_cv2(im: Any = img) -> Any:
                    arr = np.asarray(im)
                    gray = cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY)
                    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
                    enhanced = clahe.apply(gray)
                    kernel = np.array([[0, -1, 0], [-1, 5, -1], [0, -1, 0]], dtype=np.float32)
                    sharp = cv2.filter2D(enhanced, -1, kernel)
                    pil_cv = Image.fromarray(sharp).convert("RGB")
                    return ocr_backend.recognize_image(pil_cv, preprocess=False)

                variants.append(("opencv_clahe_sharpen", _run_cv2))

            for variant_name, fn in variants:
                lat: list[float] = []
                last_txt = ""
                res = None
                for _ in range(3):
                    t0 = time.monotonic_ns()
                    res = fn()
                    lat.append((time.monotonic_ns() - t0) / 1_000_000.0)
                    last_txt = "\n".join(ln.text for ln in res.lines)
                lat.sort()
                cer, wer = compute_cer_wer(gt_txt, last_txt) if gt_txt else (None, None)
                ocr_benchmarks.append(
                    {
                        "doc_id": item["doc_id"],
                        "preprocessing": variant_name,
                        "p50_ms": round(percentile(lat, 50.0), 2),
                        "p95_ms": round(percentile(lat, 95.0), 2),
                        "cer": cer,
                        "wer": wer,
                        "mean_conf": res.mean_confidence if res else 0.0,
                    }
                )

    report = {
        "python_executable": sys.executable,
        "ocr_available": ocr_available,
        "cold_init": {"adaptive": cold_adaptive, "ocr": cold_ocr},
        "performance_comparison": perf_rows,
        "routing_summary": {
            "routing_accuracy": round(routing_correct / max(1, routing_total), 4),
            "routing_checked_pages": routing_total,
            "fallback_frequency": round(fallback_pages / max(1, total_pdf_pages), 4),
            "total_corpus_pdf_pages": total_pdf_pages,
        },
        "quality_results": quality_rows,
        "ocr_preprocessing_eval": ocr_benchmarks,
    }
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
