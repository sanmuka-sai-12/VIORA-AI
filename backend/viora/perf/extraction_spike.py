from __future__ import annotations

import io
from dataclasses import dataclass

import pdfplumber
from pypdf import PdfReader

from .benchmark import BenchmarkRunner
from .schemas import BenchmarkSummary


def _escape_pdf_text(text: str) -> str:
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def build_minimal_pdf(page_streams: list[str]) -> bytes:
    """Construct a valid multi-page PDF 1.4 document with Helvetica Type1 font."""
    objects: list[bytes] = []

    def add_obj(body: bytes) -> int:
        objects.append(body)
        return len(objects)

    # 1: Catalog, 2: Pages placeholder, 3: Font
    add_obj(b"<< /Type /Catalog /Pages 2 0 R >>")
    add_obj(b"")  # filled after pages are added
    add_obj(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")

    page_ids: list[int] = []
    for stream_text in page_streams:
        stream_bytes = stream_text.encode("latin-1", errors="replace")
        content_body = (
            f"<< /Length {len(stream_bytes)} >>\nstream\n".encode("ascii")
            + stream_bytes
            + b"\nendstream"
        )
        content_id = add_obj(content_body)
        page_body = (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            f"/Resources << /Font << /F1 3 0 R >> >> /Contents {content_id} 0 R >>"
        ).encode("ascii")
        page_ids.append(add_obj(page_body))

    kids = " ".join(f"{pid} 0 R" for pid in page_ids)
    objects[1] = f"<< /Type /Pages /Kids [{kids}] /Count {len(page_ids)} >>".encode(
        "ascii"
    )

    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets: list[int] = [0]
    for idx, obj in enumerate(objects, start=1):
        offsets.append(len(out))
        out.extend(f"{idx} 0 obj\n".encode("ascii"))
        out.extend(obj)
        out.extend(b"\nendobj\n")

    xref_pos = len(out)
    out.extend(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    out.extend(b"0000000000 65535 f \n")
    for off in offsets[1:]:
        out.extend(f"{off:010d} 00000 n \n".encode("ascii"))
    out.extend(
        (
            f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref_pos}\n%%EOF\n"
        ).encode("ascii")
    )
    return bytes(out)


def _text_lines_stream(lines: list[str], *, x: int = 54, start_y: int = 740, step: int = 16) -> str:
    ops = ["BT", "/F1 11 Tf"]
    y = start_y
    for line in lines:
        ops.append(f"1 0 0 1 {x} {y} Tm ({_escape_pdf_text(line)}) Tj")
        y -= step
    ops.append("ET")
    return "\n".join(ops)


def _multi_column_stream() -> tuple[str, list[str], list[str]]:
    left_lines = [
        f"Left Column Concept {i}: Conservation of Momentum rule {i}"
        for i in range(1, 9)
    ]
    right_lines = [
        f"Right Column Note {i}: Worked velocity example {i}"
        for i in range(1, 9)
    ]
    ops = ["BT", "/F1 10 Tf"]
    y = 740
    # Intentionally emit left and right text at equal y bands so naive stream order mixes columns.
    for left, right in zip(left_lines, right_lines, strict=True):
        ops.append(f"1 0 0 1 48 {y} Tm ({_escape_pdf_text(left)}) Tj")
        ops.append(f"1 0 0 1 320 {y} Tm ({_escape_pdf_text(right)}) Tj")
        y -= 20
    ops.append("ET")
    return "\n".join(ops), left_lines, right_lines


def _table_stream() -> tuple[str, list[list[str]]]:
    rows = [
        ["Module", "Topic", "Credits", "Assessment"],
        ["MOD101", "Linear Algebra", "4", "Quiz"],
        ["MOD102", "Probability", "4", "Exam"],
        ["MOD103", "Algorithms", "5", "Lab"],
        ["MOD104", "Databases", "4", "Project"],
    ]
    x_cols = [60, 150, 310, 410, 540]
    top_y = 720
    row_h = 28
    bottom_y = top_y - row_h * len(rows)

    ops = ["0.75 w"]
    for i in range(len(rows) + 1):
        y = top_y - i * row_h
        ops.append(f"{x_cols[0]} {y} m {x_cols[-1]} {y} l S")
    for x in x_cols:
        ops.append(f"{x} {top_y} m {x} {bottom_y} l S")

    ops.append("BT")
    ops.append("/F1 10 Tf")
    for r_idx, row in enumerate(rows):
        y_text = top_y - (r_idx * row_h) - 18
        for c_idx, cell in enumerate(row):
            x_text = x_cols[c_idx] + 6
            ops.append(f"1 0 0 1 {x_text} {y_text} Tm ({_escape_pdf_text(cell)}) Tj")
    ops.append("ET")
    return "\n".join(ops), rows


def _no_text_vector_diagram_stream() -> str:
    # Simulates vector boxes/diagrams without extractable text operators.
    return (
        "1 w\n"
        "72 500 200 140 re S\n"
        "320 500 200 140 re S\n"
        "272 570 m 320 570 l S\n"
        "72 440 448 30 re f"
    )


@dataclass(frozen=True)
class ExtractionCorpus:
    plain_text_bytes: bytes
    plain_text_markers: tuple[str, ...]
    small_pdf: bytes
    small_pdf_markers: tuple[str, ...]
    multipage_pdf: bytes
    multipage_markers: tuple[str, ...]
    multicolumn_pdf: bytes
    multicolumn_left_markers: tuple[str, ...]
    multicolumn_right_markers: tuple[str, ...]
    table_pdf: bytes
    table_rows: list[list[str]]
    notext_pdf: bytes


def build_synthetic_corpus() -> ExtractionCorpus:
    plain_lines = [
        f"Lecture Section {i}: Derivatives measure instantaneous rates of change across intervals."
        for i in range(1, 140)
    ]
    plain_text = "\n".join(plain_lines).encode("utf-8")

    small_lines = [
        "VIORA Sample Lesson: Cell Biology Overview",
        "Mitochondria generate ATP via oxidative phosphorylation.",
        "Ribosomes translate mRNA transcripts into polypeptide chains.",
    ]
    small_pdf = build_minimal_pdf([_text_lines_stream(small_lines)])

    multipage_streams = [
        _text_lines_stream(
            [
                f"Chapter Page {p}: Thermodynamics Law Summary",
                f"MarkerPage{p}: Entropy in isolated systems never decreases.",
                f"Practice Problem {p}.1: Calculate work done at constant pressure.",
            ]
        )
        for p in range(1, 13)
    ]
    multipage_pdf = build_minimal_pdf(multipage_streams)
    multipage_markers = tuple(f"MarkerPage{p}" for p in range(1, 13))

    mc_stream, left_lines, right_lines = _multi_column_stream()
    multicolumn_pdf = build_minimal_pdf([mc_stream])

    tbl_stream, table_rows = _table_stream()
    table_pdf = build_minimal_pdf([tbl_stream])

    notext_pdf = build_minimal_pdf([_no_text_vector_diagram_stream()])

    return ExtractionCorpus(
        plain_text_bytes=plain_text,
        plain_text_markers=("Lecture Section 1", "Lecture Section 139"),
        small_pdf=small_pdf,
        small_pdf_markers=("Mitochondria", "Ribosomes"),
        multipage_pdf=multipage_pdf,
        multipage_markers=multipage_markers,
        multicolumn_pdf=multicolumn_pdf,
        multicolumn_left_markers=tuple(left_lines),
        multicolumn_right_markers=tuple(right_lines),
        table_pdf=table_pdf,
        table_rows=table_rows,
        notext_pdf=notext_pdf,
    )


def extract_with_pypdf(pdf_bytes: bytes) -> tuple[str, int]:
    reader = PdfReader(io.BytesIO(pdf_bytes))
    parts = [(page.extract_text() or "") for page in reader.pages]
    return "\n".join(parts), len(reader.pages)


def extract_with_pdfplumber(
    pdf_bytes: bytes, *, include_tables: bool = False
) -> tuple[str, list[list[list[str | None]]], int]:
    tables: list[list[list[str | None]]] = []
    text_parts: list[str] = []
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            text_parts.append(page.extract_text() or "")
            if include_tables:
                tables.extend(page.extract_tables() or [])
        return "\n".join(text_parts), tables, len(pdf.pages)


def _completeness_ratio(text: str, markers: tuple[str, ...]) -> float:
    if not markers:
        return 1.0
    hits = sum(1 for m in markers if m in text)
    return round(hits / len(markers), 3)


def _is_reading_order_interleaved(
    text: str, left_markers: tuple[str, ...], right_markers: tuple[str, ...]
) -> bool:
    last_left = max((text.find(m) for m in left_markers if m in text), default=-1)
    first_right = min((text.find(m) for m in right_markers if m in text), default=-1)
    if last_left == -1 or first_right == -1:
        return False
    # If columns were separated cleanly, all left markers precede all right markers.
    return first_right < last_left


@dataclass(frozen=True)
class ExtractionBaselineResult:
    benchmarks: list[BenchmarkSummary]
    quality_notes: dict[str, dict[str, object]]


def run_extraction_baseline(*, iterations: int = 15) -> ExtractionBaselineResult:
    corpus = build_synthetic_corpus()
    runner = BenchmarkRunner()
    benchmarks: list[BenchmarkSummary] = []
    quality_notes: dict[str, dict[str, object]] = {}

    # 1. Plain text UTF-8 decode
    benchmarks.append(
        runner.run(
            "extract_plain_text_utf8",
            lambda: corpus.plain_text_bytes.decode("utf-8"),
            iterations=iterations,
            mode="uncached",
            provider_kind="local_cpu",
            characteristics={"bytes": len(corpus.plain_text_bytes), "format": "text/plain"},
        )
    )
    decoded = corpus.plain_text_bytes.decode("utf-8")
    quality_notes["plain_text"] = {
        "completeness": _completeness_ratio(decoded, corpus.plain_text_markers),
        "chars": len(decoded),
    }

    # 2. Small digital PDF (pypdf vs pdfplumber)
    benchmarks.append(
        runner.run(
            "extract_small_pdf_pypdf",
            lambda: extract_with_pypdf(corpus.small_pdf),
            iterations=iterations,
            mode="uncached",
            provider_kind="local_cpu",
            characteristics={"bytes": len(corpus.small_pdf), "pages": 1, "engine": "pypdf"},
        )
    )
    benchmarks.append(
        runner.run(
            "extract_small_pdf_pdfplumber",
            lambda: extract_with_pdfplumber(corpus.small_pdf),
            iterations=iterations,
            mode="uncached",
            provider_kind="local_cpu",
            characteristics={"bytes": len(corpus.small_pdf), "pages": 1, "engine": "pdfplumber"},
        )
    )
    small_pypdf, _ = extract_with_pypdf(corpus.small_pdf)
    small_plumber, _, _ = extract_with_pdfplumber(corpus.small_pdf)
    quality_notes["small_digital_pdf"] = {
        "pypdf_completeness": _completeness_ratio(small_pypdf, corpus.small_pdf_markers),
        "pdfplumber_completeness": _completeness_ratio(small_plumber, corpus.small_pdf_markers),
    }

    # 3. Multi-page digital PDF (12 pages)
    benchmarks.append(
        runner.run(
            "extract_multipage_pdf_pypdf",
            lambda: extract_with_pypdf(corpus.multipage_pdf),
            iterations=iterations,
            mode="uncached",
            provider_kind="local_cpu",
            characteristics={"bytes": len(corpus.multipage_pdf), "pages": 12, "engine": "pypdf"},
        )
    )
    benchmarks.append(
        runner.run(
            "extract_multipage_pdf_pdfplumber",
            lambda: extract_with_pdfplumber(corpus.multipage_pdf),
            iterations=iterations,
            mode="uncached",
            provider_kind="local_cpu",
            characteristics={"bytes": len(corpus.multipage_pdf), "pages": 12, "engine": "pdfplumber"},
        )
    )
    mp_pypdf, mp_pages_1 = extract_with_pypdf(corpus.multipage_pdf)
    mp_plumber, _, mp_pages_2 = extract_with_pdfplumber(corpus.multipage_pdf)
    quality_notes["multipage_digital_pdf"] = {
        "pages": mp_pages_1,
        "pypdf_completeness": _completeness_ratio(mp_pypdf, corpus.multipage_markers),
        "pdfplumber_pages": mp_pages_2,
        "pdfplumber_completeness": _completeness_ratio(mp_plumber, corpus.multipage_markers),
    }

    # 4. Multi-column layout PDF
    benchmarks.append(
        runner.run(
            "extract_multicolumn_pdf_pypdf",
            lambda: extract_with_pypdf(corpus.multicolumn_pdf),
            iterations=iterations,
            mode="uncached",
            provider_kind="local_cpu",
            characteristics={"bytes": len(corpus.multicolumn_pdf), "columns": 2, "engine": "pypdf"},
        )
    )
    benchmarks.append(
        runner.run(
            "extract_multicolumn_pdf_pdfplumber",
            lambda: extract_with_pdfplumber(corpus.multicolumn_pdf),
            iterations=iterations,
            mode="uncached",
            provider_kind="local_cpu",
            characteristics={"bytes": len(corpus.multicolumn_pdf), "columns": 2, "engine": "pdfplumber"},
        )
    )
    mc_pypdf, _ = extract_with_pypdf(corpus.multicolumn_pdf)
    mc_plumber, _, _ = extract_with_pdfplumber(corpus.multicolumn_pdf)
    all_mc = corpus.multicolumn_left_markers + corpus.multicolumn_right_markers
    quality_notes["multicolumn_pdf"] = {
        "pypdf_completeness": _completeness_ratio(mc_pypdf, all_mc),
        "pypdf_interleaved_columns": _is_reading_order_interleaved(
            mc_pypdf, corpus.multicolumn_left_markers, corpus.multicolumn_right_markers
        ),
        "pdfplumber_completeness": _completeness_ratio(mc_plumber, all_mc),
        "pdfplumber_interleaved_columns": _is_reading_order_interleaved(
            mc_plumber, corpus.multicolumn_left_markers, corpus.multicolumn_right_markers
        ),
    }

    # 5. Table PDF
    benchmarks.append(
        runner.run(
            "extract_table_pdf_pypdf",
            lambda: extract_with_pypdf(corpus.table_pdf),
            iterations=iterations,
            mode="uncached",
            provider_kind="local_cpu",
            characteristics={"bytes": len(corpus.table_pdf), "rows": len(corpus.table_rows), "engine": "pypdf"},
        )
    )
    benchmarks.append(
        runner.run(
            "extract_table_pdf_pdfplumber_tables",
            lambda: extract_with_pdfplumber(corpus.table_pdf, include_tables=True),
            iterations=iterations,
            mode="uncached",
            provider_kind="local_cpu",
            characteristics={"bytes": len(corpus.table_pdf), "rows": len(corpus.table_rows), "engine": "pdfplumber"},
        )
    )
    tbl_pypdf, _ = extract_with_pypdf(corpus.table_pdf)
    tbl_plumber, extracted_tables, _ = extract_with_pdfplumber(
        corpus.table_pdf, include_tables=True
    )
    quality_notes["table_pdf"] = {
        "pypdf_chars": len(tbl_pypdf),
        "pypdf_preserves_table_rows": False,
        "pdfplumber_chars": len(tbl_plumber),
        "pdfplumber_tables_detected": len(extracted_tables),
        "pdfplumber_first_table_rows": len(extracted_tables[0]) if extracted_tables else 0,
    }

    # 6. No/low extractable text PDF
    benchmarks.append(
        runner.run(
            "extract_notext_pdf_pypdf",
            lambda: extract_with_pypdf(corpus.notext_pdf),
            iterations=iterations,
            mode="uncached",
            provider_kind="local_cpu",
            characteristics={"bytes": len(corpus.notext_pdf), "extractable_text": False, "engine": "pypdf"},
        )
    )
    benchmarks.append(
        runner.run(
            "extract_notext_pdf_pdfplumber",
            lambda: extract_with_pdfplumber(corpus.notext_pdf),
            iterations=iterations,
            mode="uncached",
            provider_kind="local_cpu",
            characteristics={"bytes": len(corpus.notext_pdf), "extractable_text": False, "engine": "pdfplumber"},
        )
    )
    nt_pypdf, _ = extract_with_pypdf(corpus.notext_pdf)
    nt_plumber, _, _ = extract_with_pdfplumber(corpus.notext_pdf)
    quality_notes["no_extractable_text_pdf"] = {
        "pypdf_chars": len(nt_pypdf.strip()),
        "pdfplumber_chars": len(nt_plumber.strip()),
        "requires_ocr_fallback": len(nt_pypdf.strip()) == 0 and len(nt_plumber.strip()) == 0,
    }

    return ExtractionBaselineResult(benchmarks=benchmarks, quality_notes=quality_notes)
