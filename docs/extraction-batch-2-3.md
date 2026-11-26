# Batch 2.3 — Adaptive Educational Document Extraction and OCR Evaluation

## Scope and Architecture

Batch 2.3 delivers extractor-independent, versioned document understanding (`ExtractedDocument`, `DerivedExtractionRecord`) and adaptive routing in `viora.sources`:

- **Native text & Markdown** (`viora.sources.native_text` v1.0): UTF-8 decoding with BOM/`\r\n` normalization, exact byte/char/line `SourcePositionSpan` mapping, section hierarchy, fenced code blocks, and inline/display LaTeX & Unicode math extraction.
- **Fast PDF triage** (`viora.sources.pypdf_triage` v1.0): Bounded `pypdf` inspection (`MAX_PDF_DECOMPRESSED_STREAM_BYTES=16_000_000`, `MAX_PDF_DECODING_WORK_BYTES=32_000_000`, `page_tree_maximum_depth=64`, `MAX_PDF_EXTRACTION_SECONDS=10.0`), non-decompressing `/Image` XObject counting (`_count_page_images_safely`), and cross-page repeated header/footer template detection (`normalize_header_footer_template`).
- **Selective spatial layout & tables** (`viora.sources.pdfplumber_spatial` v2.0): Invoked per page only when triage flags multi-column layout, tables, code blocks, or figures (`max_fallback_pages=50` default). Reconstructs multi-column reading order, parses bordered (`lines` strategy) and borderless (`text` strategy) tables, and detects figure regions (`raster_image`, `vector_diagram`) without fabricating multimodal descriptions.
- **Optional CPU OCR adapter** (`viora.sources.ocr` / `viora.sources.rapidocr_onnx` v3.10): Bounded image decoding (`decode_bounded_image`, `max_pixels=16_000_000`, EXIF transpose) and PDF page rendering (`render_pdf_page_to_rgb`, `dpi=150`, `max_pixels=16_000_000`). Production HTTP ingestion keeps OCR disabled until Batch 2.4 durable background workers.
- **Storage binary integrity**: `LocalFilesystemArtifactStore` uses `O_BINARY` on open flags so Windows text-mode CRLF translation cannot corrupt binary PDFs/images.

## Representative Educational Corpus (CC0-1.0)

All 16 fixtures in `benchmarks/educational_corpus/samples/` are originally authored synthetic educational samples licensed `CC0-1.0` (verified against `benchmarks/educational_corpus/ground_truth.json` SHA-256 digests; zero private or copyrighted material):

| Doc ID | Category | Format | Purpose |
|---|---|---|---|
| `01_markdown_notes` | Plain text / Markdown | `typed_notes` | Headings, formulas (`$v = u + at$`), fenced Python code, lists |
| `02_single_column_textbook` | Single-column textbook | `digital_pdf` (2p) | Repeated running headers/footers isolation |
| `03_multicolumn_scientific` | Multi-column scientific | `digital_pdf` (1p) | Two-column reading-order reconstruction |
| `04_code_blocks` | Programming code | `digital_pdf` (1p) | Monospace indentation preservation |
| `05_tables_bordered_and_borderless` | Tables | `digital_pdf` (2p) | Bordered (p1) and borderless whitespace-aligned (p2) tables |
| `06_math_equations` | Math equations | `digital_pdf` (1p) | Symbol+ToUnicode math glyphs and LaTeX expressions |
| `07_scanned_printed_page_png` / `_pdf` | Scanned printed page | `png` / `digital_pdf` | Printed-text OCR routing & raster bounding-box scaling |
| `08_photographed_notes` | Photographed notes | `jpeg` | Perspective/contrast OCR evaluation |
| `09_handwritten_sample` | Handwritten sample | `png` | Explicit handwriting warning / deferred boundary |
| `10_diagrams_charts_graphs_pdf` / `10_chart_png` | Diagrams & charts | `digital_pdf` / `png` | Figure caption preservation without fabricated descriptions |
| `11_mixed_digital_scanned` | Mixed packet | `digital_pdf` (3p) | Table (p1) + scanned OCR (p2) + multi-column (p3) |
| `12_multilingual_md` / `_pdf` / `_png` | Multilingual | `typed_notes` / `digital_pdf` / `png` | English, Spanish, French, and Hindi script boundaries |

## Reproducible Commands

```powershell
# 1. Regenerate/verify synthetic corpus fixtures
.\.venv\Scripts\python.exe -m benchmarks.educational_corpus.build_corpus

# 2. Run backend unit + representative benchmark tests (and postgres integration when VIORA_TEST_DATABASE_URL is set)
$env:PYTHONPATH = "backend"
.\.venv\Scripts\pytest -q
.\.venv\Scripts\ruff.exe check .

# 3. Run Batch 2.3 performance & quality evaluation
$env:PYTHONPATH = "backend"
.\.venv\Scripts\python.exe scripts/benchmark_extraction_eval.py

# 4. Verify OpenAPI & TypeScript contract sync
$env:PYTHONPATH = "backend"
.\.venv\Scripts\python.exe -m viora.export_contracts
npx --yes openapi-typescript@7.6.1 contracts/http/openapi.json -o web/src/api/generated.ts
```

## Preserved Benchmark Results (Baseline `.venv`, OCR optional runtime absent)

Measured via `scripts/benchmark_extraction_eval.py` on Windows (Python 3.13.16, `ocr_available=false`):

- **Cold init (`AdaptiveDocumentExtractor`)**: `985.08 ms`, peak RSS `48.54 MB`
- **Latency & memory comparison**:
  - `12p_clean_pdf__fast_pypdf` (15 iters, 12p): p50 `61.91 ms`, p95 `82.96 ms`, `184.56 pages/s`, Python heap peak `792.62 KB`, RSS `51.82 MB`
  - `12p_clean_pdf__full_pdfplumber` (15 iters, 12p): p50 `381.20 ms`, p95 `449.54 ms`, `30.39 pages/s`, Python heap peak `30166.88 KB`, peak RSS `119.68 MB`
  - `12p_clean_pdf__adaptive_selective` (15 iters, 12p): p50 `55.00 ms`, p95 `63.50 ms`, `213.36 pages/s`, Python heap peak `551.17 KB` (~6.9x faster p50 and ~55x lower Python heap peak than unconditional `pdfplumber`)
  - `multicolumn_pdf__fast_pypdf` vs `adaptive_selective`: reading-order accuracy improves from `0.9333` to `1.0000` (p50 `132.02 ms` vs `200.13 ms` full `pdfplumber`)
  - `tables_2p_pdf__fast_pypdf` vs `adaptive_selective`: table cell accuracy improves from `0.0000` to `1.0000` (p50 `183.54 ms`)
  - `markdown_notes__native_text` (30 iters, 1p): p50 `2.57 ms`, p95 `3.63 ms`, `378.48 pages/s`, heap peak `52.00 KB`
- **Routing & evidence**:
  - Corpus `evidence_accuracy`: `1.0` across all 16 documents
  - `routing_accuracy`: `0.9` across 10 checked pages (`11_mixed_digital_scanned` p1 classified `needs_layout` by `fast_only` before adaptive spatial parsing resolved the table)
  - `fallback_frequency`: `0.5` (7 of 14 PDF pages)

## Optional OCR Dependencies, Model Provenance, and Licensing

OCR runtime packages are **optional** for API/CI environments and deployed separately for background workers in Batch 2.4:

| Component | Package / Version | License | Provenance / Notes |
|---|---|---|---|
| `rapidocr` | `3.10.0` (optional worker dep) | Apache-2.0 | Wraps ONNX Runtime inference for printed text detection/orientation/recognition; RapidOCR 3.10 default ONNX weights (`PP-OCRv6_det_small.onnx`, `ch_ppocr_mobile_v2.0_cls_mobile.onnx`, `PP-OCRv6_rec_small.onnx`) fetched/verified on first worker warm-up from upstream RapidOCR release mirrors (never committed to git). |
| `onnxruntime` | `1.32.0` (optional worker dep) | MIT | CPU execution provider for ONNX OCR models. |
| `pypdfium2` | `5.15.2` (via `pdfplumber`) | BSD-3-Clause / Apache-2.0 | Bounded PDF page rasterization (`dpi <= 300`, `max_pixels=16_000_000`). |
| `pillow` | `13.0.0` (via `pdfplumber`) | MIT-CMU | Bounded image decoding (`Image.MAX_IMAGE_PIXELS`) and deterministic grayscale/autocontrast/sharpen preprocessing. |
| `pdfplumber` / `pdfminer.six` | `0.11.11` / `20260107` | MIT | Spatial layout & table extraction on selected pages. |
| `pypdf` | `7.1.1` | BSD-3-Clause | Fast PDF triage and bounded stream limits. |

**Explicit limitations retained:** Handwritten note transcription, Devanagari/Hindi OCR, and semantic interpretation of charts/diagrams remain unsupported/deferred and emit explicit warnings rather than fabricated output.
