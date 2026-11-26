"""Generate legally usable representative educational documents and verified ground truth.

All documents are originally authored open-licensed (CC0) educational samples covering:
1. Plain text and Markdown notes.
2. Single-column textbook pages.
3. Multi-column scientific documents.
4. Programming code blocks.
5. Bordered and borderless tables.
6. Mathematical equations.
7. Scanned printed pages.
8. Photographed notes.
9. Handwritten samples.
10. Diagrams, charts, and graphs.
11. Mixed digital/scanned PDFs.
12. English and representative multilingual samples.
"""

from __future__ import annotations

import hashlib
import io
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFilter, ImageFont

CORPUS_DIR = Path(__file__).resolve().parent
SAMPLES_DIR = CORPUS_DIR / "samples"
GROUND_TRUTH_PATH = CORPUS_DIR / "ground_truth.json"

_FONT_REGULAR = "C:/Windows/Fonts/arial.ttf"
_FONT_BOLD = "C:/Windows/Fonts/arialbd.ttf"
_FONT_MONO = "C:/Windows/Fonts/consola.ttf"
_FONT_SCRIPT = "C:/Windows/Fonts/segoepr.ttf"
_FONT_INDIC = "C:/Windows/Fonts/Nirmala.ttc"

_MATH_CMAP = b"""/CIDInit /ProcSet findresource begin
12 dict begin
begincmap
/CIDSystemInfo << /Registry (Adobe) /Ordering (UCS) /Supplement 0 >> def
/CMapName /Adobe-Identity-UCS def
/CMapType 2 def
1 begincodespacerange
<00> <FF>
endcodespacerange
8 beginbfchar
<44> <0394>
<53> <2211>
<49> <222B>
<52> <221A>
<41> <2248>
<4C> <2264>
<47> <2265>
<50> <2202>
endbfchar
endcmap
CMapName currentdict /CMap defineresource pop
end
end"""


def _esc(text: str) -> str:
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _load_font(path: str, size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    try:
        return ImageFont.truetype(path, size)
    except OSError:
        return ImageFont.load_default()


@dataclass
class PdfImageSpec:
    name: str
    jpeg_bytes: bytes
    width_px: int
    height_px: int
    x: float
    y: float
    draw_w: float
    draw_h: float


@dataclass
class PdfPageSpec:
    content_ops: str
    images: tuple[PdfImageSpec, ...] = ()
    width: float = 612.0
    height: float = 792.0


def build_rich_pdf(pages: list[PdfPageSpec]) -> bytes:
    """Build a deterministic PDF 1.4 document with Helvetica, Bold, Courier, Symbol+ToUnicode, and JPEG XObjects."""
    objects: list[bytes] = []

    def add_obj(body: bytes) -> int:
        objects.append(body)
        return len(objects)

    add_obj(b"<< /Type /Catalog /Pages 2 0 R >>")  # 1
    add_obj(b"")  # 2: Pages placeholder
    f_reg = add_obj(
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>"
    )
    f_bold = add_obj(
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>"
    )
    f_mono = add_obj(
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Courier /Encoding /WinAnsiEncoding >>"
    )
    cmap_id = add_obj(
        f"<< /Length {len(_MATH_CMAP)} >>\nstream\n".encode("ascii")
        + _MATH_CMAP
        + b"\nendstream"
    )
    f_sym = add_obj(
        f"<< /Type /Font /Subtype /Type1 /BaseFont /Symbol /ToUnicode {cmap_id} 0 R >>".encode(
            "ascii"
        )
    )

    page_ids: list[int] = []
    for page in pages:
        xobj_entries: list[str] = []
        prefix_ops: list[str] = []
        for img in page.images:
            img_obj = add_obj(
                f"<< /Type /XObject /Subtype /Image /Width {img.width_px} /Height {img.height_px} "
                f"/ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /DCTDecode "
                f"/Length {len(img.jpeg_bytes)} >>\nstream\n".encode("ascii")
                + img.jpeg_bytes
                + b"\nendstream"
            )
            xobj_entries.append(f"/{img.name} {img_obj} 0 R")
            prefix_ops.append(
                f"q {img.draw_w:.2f} 0 0 {img.draw_h:.2f} {img.x:.2f} {img.y:.2f} cm /{img.name} Do Q"
            )

        full_stream = "\n".join([*prefix_ops, page.content_ops]).strip()
        stream_bytes = full_stream.encode("cp1252", errors="replace")
        content_id = add_obj(
            f"<< /Length {len(stream_bytes)} >>\nstream\n".encode("ascii")
            + stream_bytes
            + b"\nendstream"
        )
        xobj_str = f" /XObject << {' '.join(xobj_entries)} >>" if xobj_entries else ""
        page_body = (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {page.width:.0f} {page.height:.0f}] "
            f"/Resources << /Font << /F1 {f_reg} 0 R /F2 {f_bold} 0 R /F3 {f_mono} 0 R /F4 {f_sym} 0 R >>"
            f"{xobj_str} >> /Contents {content_id} 0 R >>"
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


def _text_block(
    lines: list[str],
    *,
    x: float,
    start_y: float,
    step: float = 15.0,
    font: str = "/F1",
    size: float = 10.5,
) -> str:
    ops = ["BT", f"{font} {size:.1f} Tf"]
    y = start_y
    for ln in lines:
        ops.append(f"1 0 0 1 {x:.1f} {y:.1f} Tm ({_esc(ln)}) Tj")
        y -= step
    ops.append("ET")
    return "\n".join(ops)


def _image_to_jpeg_bytes(img: Image.Image, quality: int = 88) -> bytes:
    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="JPEG", quality=quality)
    return buf.getvalue()


def _image_to_png_bytes(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="PNG")
    return buf.getvalue()


def _render_scanned_printed_page() -> tuple[Image.Image, list[str]]:
    lines = [
        "Chapter 4: Conservation of Mechanical Energy",
        "In a closed system without non-conservative forces such as friction,",
        "the total mechanical energy E remains constant over time.",
        "Kinetic energy is defined as K = 0.5 * m * v^2 and potential energy",
        "near Earth's surface is U = m * g * h.",
        "Therefore, K_initial + U_initial = K_final + U_final.",
        "Worked Example: A 2.0 kg block slides down a frictionless ramp",
        "from height 5.0 m. Its speed at the bottom is 9.90 m/s.",
    ]
    img = Image.new("RGB", (1200, 1500), (247, 245, 240))
    draw = ImageDraw.Draw(img)
    f_head = _load_font(_FONT_BOLD, 34)
    f_body = _load_font(_FONT_REGULAR, 24)
    y = 110
    for idx, line in enumerate(lines):
        draw.text((90, y), line, fill=(22, 22, 25), font=f_head if idx == 0 else f_body)
        y += 68 if idx == 0 else 50
    # Slight scan tilt (0.6 degrees) and gentle blur simulating 150-200 DPI flatbed scan
    rotated = img.rotate(0.6, resample=Image.Resampling.BICUBIC, fillcolor=(244, 242, 236))
    scanned = rotated.filter(ImageFilter.GaussianBlur(radius=0.35))
    return scanned, lines


def _render_photographed_notes() -> tuple[Image.Image, list[str]]:
    lines = [
        "Biology Revision: Cellular Respiration",
        "1. Glycolysis occurs in the cytoplasm and produces 2 ATP.",
        "2. Citric acid cycle runs inside the mitochondrial matrix.",
        "3. Oxidative phosphorylation generates roughly 30 to 32 ATP.",
    ]
    w, h = 1100, 760
    img = Image.new("RGB", (w, h), (236, 232, 220))
    # Uneven smartphone lighting gradient (darker toward bottom-right)
    px = img.load()
    if px is not None:
        for yy in range(h):
            shade = int(24 * (yy / h))
            for xx in range(w):
                dx = int(18 * (xx / w))
                base = 242 - shade - dx
                px[xx, yy] = (base, base - 3, base - 10)
    draw = ImageDraw.Draw(img)
    f_head = _load_font(_FONT_BOLD, 30)
    f_body = _load_font(_FONT_REGULAR, 23)
    y = 95
    for idx, line in enumerate(lines):
        draw.text((85, y), line, fill=(30, 32, 38), font=f_head if idx == 0 else f_body)
        y += 72 if idx == 0 else 58
    photo = img.rotate(-0.8, resample=Image.Resampling.BICUBIC, fillcolor=(210, 205, 194))
    return photo, lines


def _render_handwritten_sample() -> tuple[Image.Image, list[str]]:
    lines = [
        "Physics Lab Notes",
        "Momentum p = m * v",
        "Impulse J = F * dt",
        "Check graph slope",
    ]
    w, h = 1000, 620
    img = Image.new("RGB", (w, h), (252, 250, 244))
    draw = ImageDraw.Draw(img)
    for ry in range(110, h - 40, 75):
        draw.line([(40, ry), (w - 40, ry)], fill=(195, 212, 232), width=2)
    f_script = _load_font(_FONT_SCRIPT, 34)
    y = 75
    for line in lines:
        draw.text((75, y), line, fill=(28, 48, 110), font=f_script)
        y += 95
    return img, lines


def _render_bar_chart() -> tuple[Image.Image, list[str]]:
    w, h = 900, 540
    img = Image.new("RGB", (w, h), "white")
    draw = ImageDraw.Draw(img)
    f_title = _load_font(_FONT_BOLD, 24)
    f_lbl = _load_font(_FONT_REGULAR, 18)
    draw.text((220, 24), "Activation Energy by Catalyst", fill="black", font=f_title)
    draw.line([(110, 80), (110, 440), (820, 440)], fill="black", width=3)
    bars = [("Uncatalyzed", 310, (190, 75, 75)), ("Enzyme A", 145, (65, 130, 195)), ("Enzyme B", 110, (70, 165, 110))]
    x = 190
    for label, height_px, color in bars:
        draw.rectangle([x, 440 - height_px, x + 120, 440], fill=color, outline="black", width=2)
        draw.text((x + 8, 452), label, fill="black", font=f_lbl)
        x += 200
    draw.text((18, 240), "kJ/mol", fill="black", font=f_lbl)
    return img, ["Activation Energy by Catalyst", "Uncatalyzed", "Enzyme A", "Enzyme B", "kJ/mol"]


def _render_multilingual_image() -> tuple[Image.Image, list[str]]:
    lines = [
        "Multilingual Physics Summary",
        "English: Energy is conserved in isolated systems.",
        "Spanish: La energia se conserva en sistemas aislados.",
        "Hindi: ऊर्जा संरक्षण का नियम महत्वपूर्ण है।",
    ]
    img = Image.new("RGB", (1150, 520), "white")
    draw = ImageDraw.Draw(img)
    f_head = _load_font(_FONT_BOLD, 30)
    f_indic = _load_font(_FONT_INDIC, 24)
    y = 65
    for idx, ln in enumerate(lines):
        draw.text((65, y), ln, fill="black", font=f_head if idx == 0 else f_indic)
        y += 75
    return img, lines


def generate_corpus() -> dict[str, Any]:
    SAMPLES_DIR.mkdir(parents=True, exist_ok=True)
    docs_meta: list[dict[str, Any]] = []

    def save_doc(
        doc_id: str,
        filename: str,
        raw_bytes: bytes,
        *,
        category: str,
        input_format: str,
        ground_truth: dict[str, Any],
    ) -> None:
        out_path = SAMPLES_DIR / filename
        out_path.write_bytes(raw_bytes)
        docs_meta.append(
            {
                "doc_id": doc_id,
                "filename": filename,
                "relative_path": f"samples/{filename}",
                "category": category,
                "input_format": input_format,
                "size_bytes": len(raw_bytes),
                "sha256": hashlib.sha256(raw_bytes).hexdigest(),
                "ground_truth": ground_truth,
            }
        )

    # 1. Markdown notes (headings, list, math, code)
    md_notes = (
        b"# Classical Kinematics\n\n"
        b"Kinematics describes motion without reference to forces.\n"
        b"Average velocity equals displacement divided by elapsed time.\n\n"
        b"## Equations of Constant Acceleration\n"
        b"The final velocity satisfies $v = u + at$.\n"
        b"$$\\Delta x = v_0 t + \\frac{1}{2} a t^2$$\n\n"
        b"## Simulation Snippet\n"
        b"```python\n"
        b"def position(x0: float, v0: float, a: float, t: float) -> float:\n"
        b"    return x0 + v0 * t + 0.5 * a * t * t\n"
        b"```\n\n"
        b"- SI unit of velocity: m/s\n"
        b"- SI unit of acceleration: m/s^2\n"
    )
    save_doc(
        "01_markdown_notes",
        "01_markdown_notes.md",
        md_notes,
        category="plain_text_and_markdown",
        input_format="typed_notes",
        ground_truth={
            "expected_headings": ["Classical Kinematics", "Equations of Constant Acceleration", "Simulation Snippet"],
            "expected_formulas": ["v = u + at", "\\Delta x = v_0 t + \\frac{1}{2} a t^2"],
            "expected_code_markers": ["def position(x0: float, v0: float, a: float, t: float) -> float:"],
            "expected_ordered_markers": [
                "Classical Kinematics",
                "Kinematics describes motion",
                "Equations of Constant Acceleration",
                "v = u + at",
                "def position",
                "SI unit of velocity",
            ],
            "expected_classifications": ["digital_text"],
        },
    )

    # 2. Single-column textbook (2 pages, repeated header/footer)
    p1_text = _text_block(
        ["VIORA Open Physics - Chapter 3: Optics"], x=72, start_y=758, font="/F1", size=9.0
    ) + "\n" + _text_block(
        ["Chapter 3: Wave Optics and Interference"], x=72, start_y=712, font="/F2", size=14.0
    ) + "\n" + _text_block(
        [
            "Light exhibits both wave and particle properties depending on the scale",
            "of observation. When coherent light passes through two narrow slits,",
            "constructive and destructive interference fringes appear on a screen.",
            "Section 3.1: Huygens Wavefront Principle",
            "Every point on a primary wavefront acts as a source of secondary",
            "spherical wavelets that propagate forward at the wave speed.",
        ],
        x=72,
        start_y=675,
        step=16,
    ) + "\n" + _text_block(["Open Physics Textbook - Page 1"], x=230, start_y=36, font="/F1", size=9.0)

    p2_text = _text_block(
        ["VIORA Open Physics - Chapter 3: Optics"], x=72, start_y=758, font="/F1", size=9.0
    ) + "\n" + _text_block(
        [
            "Section 3.2: Young Double-Slit Experiment",
            "Path difference d * sin(theta) = m * lambda produces bright maxima",
            "for integer order m. Increasing wavelength widens fringe spacing.",
            "Diffraction limits optical resolution in telescopes and microscopes.",
        ],
        x=72,
        start_y=712,
        step=16,
    ) + "\n" + _text_block(["Open Physics Textbook - Page 2"], x=230, start_y=36, font="/F1", size=9.0)

    single_pdf = build_rich_pdf([PdfPageSpec(p1_text), PdfPageSpec(p2_text)])
    save_doc(
        "02_single_column_textbook",
        "02_single_column_textbook.pdf",
        single_pdf,
        category="single_column_textbook",
        input_format="digital_pdf",
        ground_truth={
            "expected_headings": ["Chapter 3: Wave Optics and Interference"],
            "expected_repeated_headers_footers": [
                "VIORA Open Physics - Chapter 3: Optics",
                "Open Physics Textbook - Page 1",
                "Open Physics Textbook - Page 2",
            ],
            "expected_ordered_markers": [
                "Chapter 3: Wave Optics and Interference",
                "Light exhibits both wave and particle properties",
                "Huygens Wavefront Principle",
                "Young Double-Slit Experiment",
                "Diffraction limits optical resolution",
            ],
            "expected_classifications": ["digital_text", "digital_text"],
        },
    )

    # 3. Multi-column scientific document (full-width title + abstract + 2 columns + footer)
    left_col = [
        "1. Introduction",
        "Neural retrieval systems require",
        "faithful document segmentation.",
        "When academic papers use two",
        "columns, naive text extraction",
        "interleaves left and right lines.",
        "Spatial column clustering restores",
        "the author reading sequence.",
    ]
    right_col = [
        "2. Experimental Method",
        "We evaluate reading order across",
        "educational PDFs with headers,",
        "tables, and multi-column prose.",
        "Each block records PDF point",
        "bounding boxes so downstream",
        "citations remain verifiable.",
        "Latency stays under 50 ms.",
    ]
    mc_ops = [
        _text_block(["Spatial Document Understanding in Educational Corpora"], x=64, start_y=732, font="/F2", size=14.0),
        _text_block(
            [
                "Abstract: Accurate reading-order reconstruction prevents cross-column sentence",
                "corruption before educational concept extraction and retrieval indexing.",
            ],
            x=64,
            start_y=702,
            step=14,
            font="/F1",
            size=10.0,
        ),
        "BT /F1 10.0 Tf",
    ]
    y = 640.0
    for l_ln, r_ln in zip(left_col, right_col, strict=True):
        # Emit interleaved on same Y to ensure naive stream reading fails
        mc_ops.append(f"1 0 0 1 56 {y:.1f} Tm ({_esc(l_ln)}) Tj")
        mc_ops.append(f"1 0 0 1 322 {y:.1f} Tm ({_esc(r_ln)}) Tj")
        y -= 18.0
    mc_ops.append("ET")
    mc_ops.append(_text_block(["VIORA Benchmark Paper - Page 1"], x=225, start_y=34, font="/F1", size=9.0))
    mc_pdf = build_rich_pdf([PdfPageSpec("\n".join(mc_ops))])
    save_doc(
        "03_multicolumn_scientific",
        "03_multicolumn_scientific.pdf",
        mc_pdf,
        category="multi_column_scientific",
        input_format="digital_pdf",
        ground_truth={
            "expected_headings": ["Spatial Document Understanding in Educational Corpora"],
            "expected_ordered_markers": [
                "Spatial Document Understanding in Educational Corpora",
                "Abstract: Accurate reading-order reconstruction",
                "1. Introduction",
                "Spatial column clustering restores",
                "2. Experimental Method",
                "Latency stays under 50 ms.",
            ],
            "expected_classifications": ["needs_layout"],
        },
    )

    # 4. Programming code blocks PDF
    code_ops = "\n".join(
        [
            _text_block(["CS102: Binary Search Implementation"], x=60, start_y=730, font="/F2", size=14.0),
            _text_block(
                [
                    "Binary search locates a target value inside a sorted array in",
                    "O(log n) worst-case comparisons by halving the search interval.",
                ],
                x=60,
                start_y=695,
                step=16,
            ),
            _text_block(
                [
                    "def binary_search(nums, target):",
                    "    lo, hi = 0, len(nums) - 1",
                    "    while lo <= hi:",
                    "        mid = (lo + hi) // 2",
                    "        if nums[mid] == target:",
                    "            return mid",
                    "        if nums[mid] < target:",
                    "            lo = mid + 1",
                    "        else:",
                    "            hi = mid - 1",
                    "    return -1",
                ],
                x=76,
                start_y=635,
                step=15,
                font="/F3",
                size=10.0,
            ),
            _text_block(
                ["Always verify loop invariants when modifying index bounds."],
                x=60,
                start_y=445,
            ),
        ]
    )
    code_pdf = build_rich_pdf([PdfPageSpec(code_ops)])
    save_doc(
        "04_code_blocks",
        "04_code_blocks.pdf",
        code_pdf,
        category="programming_code_blocks",
        input_format="digital_pdf",
        ground_truth={
            "expected_code_markers": [
                "def binary_search(nums, target):",
                "    lo, hi = 0, len(nums) - 1",
                "        mid = (lo + hi) // 2",
            ],
            "expected_ordered_markers": [
                "CS102: Binary Search Implementation",
                "def binary_search(nums, target):",
                "Always verify loop invariants",
            ],
        },
    )

    # 5. Bordered and borderless tables PDF
    bordered_rows = [
        ["Gas", "MolarMass", "Cv_J_molK", "Gamma"],
        ["Helium", "4.00", "12.47", "1.67"],
        ["Nitrogen", "28.01", "20.80", "1.40"],
        ["Oxygen", "32.00", "21.05", "1.40"],
        ["CO2", "44.01", "28.46", "1.30"],
    ]
    x_cols = [60, 175, 290, 415, 520]
    top_y = 660
    row_h = 28
    bottom_y = top_y - row_h * len(bordered_rows)
    t1_ops = [
        _text_block(["Table 1: Thermodynamic Properties of Gases"], x=60, start_y=690, font="/F2", size=11.5),
        "0.75 w",
    ]
    for i in range(len(bordered_rows) + 1):
        yy = top_y - i * row_h
        t1_ops.append(f"{x_cols[0]} {yy} m {x_cols[-1]} {yy} l S")
    for xx in x_cols:
        t1_ops.append(f"{xx} {top_y} m {xx} {bottom_y} l S")
    t1_ops.append("BT /F1 10.0 Tf")
    for r_i, row in enumerate(bordered_rows):
        yt = top_y - r_i * row_h - 19
        for c_i, cell in enumerate(row):
            t1_ops.append(f"1 0 0 1 {x_cols[c_i] + 8} {yt} Tm ({_esc(cell)}) Tj")
    t1_ops.append("ET")
    t1_ops.append(_text_block(["Values measured at 298 K and 1 atm."], x=60, start_y=bottom_y - 30))

    borderless_rows = [
        ["Algorithm", "BestCase", "WorstCase", "Space"],
        ["QuickSort", "O(nlogn)", "O(n^2)", "O(logn)"],
        ["MergeSort", "O(nlogn)", "O(nlogn)", "O(n)"],
        ["HeapSort", "O(nlogn)", "O(nlogn)", "O(1)"],
        ["BinarySearch", "O(1)", "O(logn)", "O(1)"],
    ]
    bx_cols = [60, 195, 315, 435]
    t2_ops = [
        _text_block(["Table 2: Algorithm Complexity Comparison"], x=60, start_y=690, font="/F2", size=11.5),
        "BT /F1 10.0 Tf",
    ]
    for r_i, row in enumerate(borderless_rows):
        yt = 650 - r_i * 24
        for c_i, cell in enumerate(row):
            t2_ops.append(f"1 0 0 1 {bx_cols[c_i]} {yt} Tm ({_esc(cell)}) Tj")
    t2_ops.append("ET")
    t2_ops.append(_text_block(["All bounds assume comparison-based sorting."], x=60, start_y=495))

    tables_pdf = build_rich_pdf([PdfPageSpec("\n".join(t1_ops)), PdfPageSpec("\n".join(t2_ops))])
    save_doc(
        "05_tables_bordered_and_borderless",
        "05_tables_bordered_and_borderless.pdf",
        tables_pdf,
        category="bordered_and_borderless_tables",
        input_format="digital_pdf",
        ground_truth={
            "tables": [
                {"page": 1, "kind": "bordered", "headers": bordered_rows[0], "rows": bordered_rows[1:], "caption_contains": "Table 1"},
                {"page": 2, "kind": "borderless", "headers": borderless_rows[0], "rows": borderless_rows[1:], "caption_contains": "Table 2"},
            ],
            "expected_classifications": ["needs_table", "needs_table"],
        },
    )

    # 6. Mathematical equations PDF (using Symbol + ToUnicode CMap for math operators + Helvetica for ASCII)
    math_ops = "\n".join(
        [
            _text_block(["Calculus and Mathematical Physics"], x=60, start_y=730, font="/F2", size=13.5),
            _text_block(["Fundamental Theorem and Series Identities:"], x=60, start_y=695),
            # D=Δ, S=∑, I=∫, R=√, A=≈, L=≤, G=≥, P=∂
            "BT /F4 12.0 Tf 1 0 0 1 72 665 Tm (I) Tj /F1 11.0 Tf ( f\\(x\\) dx = F\\(b\\) - F\\(a\\)) Tj ET",
            "BT /F4 12.0 Tf 1 0 0 1 72 640 Tm (S) Tj /F1 11.0 Tf ( 1/n^2 ) Tj /F4 12.0 Tf (A) Tj /F1 11.0 Tf ( 1.6449) Tj ET",
            "BT /F4 12.0 Tf 1 0 0 1 72 615 Tm (D) Tj /F1 11.0 Tf (E ) Tj /F4 12.0 Tf (G) Tj /F1 11.0 Tf ( 0 and ) Tj /F4 12.0 Tf (P) Tj /F1 11.0 Tf (u / ) Tj /F4 12.0 Tf (P) Tj /F1 11.0 Tf (t ) Tj /F4 12.0 Tf (L) Tj /F1 11.0 Tf ( 0) Tj ET",
            _text_block(["Display form: $$E = mc^2$$ and inline $x = \\sqrt{a^2 + b^2}$."], x=60, start_y=580),
        ]
    )
    math_pdf = build_rich_pdf([PdfPageSpec(math_ops)])
    save_doc(
        "06_math_equations",
        "06_math_equations.pdf",
        math_pdf,
        category="mathematical_equations",
        input_format="digital_pdf",
        ground_truth={
            "expected_unicode_symbols": ["∫", "∑", "≈", "Δ", "≥", "∂", "≤"],
            "expected_latex": ["E = mc^2", "x = \\sqrt{a^2 + b^2}"],
        },
    )

    # 7. Scanned printed page (PNG + image-only PDF)
    scan_img, scan_lines = _render_scanned_printed_page()
    scan_png = _image_to_png_bytes(scan_img)
    scan_jpg = _image_to_jpeg_bytes(scan_img, quality=85)
    scan_pdf = build_rich_pdf(
        [
            PdfPageSpec(
                content_ops="",
                images=(
                    PdfImageSpec(
                        name="ImScan1",
                        jpeg_bytes=scan_jpg,
                        width_px=scan_img.width,
                        height_px=scan_img.height,
                        x=36.0,
                        y=72.0,
                        draw_w=540.0,
                        draw_h=675.0,
                    ),
                ),
            )
        ]
    )
    save_doc(
        "07_scanned_printed_page_png",
        "07_scanned_printed_page.png",
        scan_png,
        category="scanned_printed_page",
        input_format="png",
        ground_truth={"expected_text": "\n".join(scan_lines), "expected_markers": ["Conservation of Mechanical Energy", "K_initial + U_initial"]},
    )
    save_doc(
        "07_scanned_printed_page_pdf",
        "07_scanned_printed_page.pdf",
        scan_pdf,
        category="scanned_printed_page",
        input_format="digital_pdf",
        ground_truth={
            "expected_text": "\n".join(scan_lines),
            "expected_markers": ["Conservation of Mechanical Energy", "K_initial + U_initial"],
            "expected_classifications": ["needs_ocr"],
        },
    )

    # 8. Photographed notes (JPG)
    photo_img, photo_lines = _render_photographed_notes()
    photo_jpg = _image_to_jpeg_bytes(photo_img, quality=78)
    save_doc(
        "08_photographed_notes",
        "08_photographed_notes.jpg",
        photo_jpg,
        category="photographed_notes",
        input_format="jpeg",
        ground_truth={"expected_text": "\n".join(photo_lines), "expected_markers": ["Cellular Respiration", "Glycolysis", "Oxidative phosphorylation"]},
    )

    # 9. Handwritten sample (PNG)
    hw_img, hw_lines = _render_handwritten_sample()
    hw_png = _image_to_png_bytes(hw_img)
    save_doc(
        "09_handwritten_sample",
        "09_handwritten_sample.png",
        hw_png,
        category="handwritten_sample",
        input_format="png",
        ground_truth={"expected_text": "\n".join(hw_lines), "expected_kind": "handwriting_region"},
    )

    # 10. Diagrams, charts and graphs (vector PDF + raster chart PNG)
    chart_img, chart_labels = _render_bar_chart()
    chart_png = _image_to_png_bytes(chart_img)
    chart_jpg = _image_to_jpeg_bytes(chart_img, quality=92)

    diag_p1 = "\n".join(
        [
            _text_block(["Energy Flow in Light Reactions"], x=60, start_y=735, font="/F2", size=13.0),
            "1.0 w",
            "72 560 150 90 re S",
            "260 560 150 90 re S",
            "448 560 120 90 re S",
            "222 605 m 260 605 l S",
            "410 605 m 448 605 l S",
            _text_block(["Photosystem II"], x=95, start_y=605, font="/F1", size=10.0),
            _text_block(["Electron Chain"], x=290, start_y=605, font="/F1", size=10.0),
            _text_block(["ATP Synthase"], x=465, start_y=605, font="/F1", size=10.0),
            _text_block(["Figure 1: Photosynthesis Light-Dependent Cycle"], x=60, start_y=535, font="/F2", size=10.5),
            _text_block(["Protons pumped across the thylakoid membrane drive ATP synthesis."], x=60, start_y=500),
        ]
    )
    diag_p2 = "\n".join(
        [
            _text_block(["Enzyme Catalysis Experimental Results"], x=60, start_y=735, font="/F2", size=13.0),
            _text_block(["Figure 2: Activation Energy Comparison"], x=60, start_y=360, font="/F2", size=10.5),
            _text_block(["Catalysts lower the transition-state energy barrier without shifting equilibrium."], x=60, start_y=325),
        ]
    )
    diag_pdf = build_rich_pdf(
        [
            PdfPageSpec(diag_p1),
            PdfPageSpec(
                diag_p2,
                images=(
                    PdfImageSpec(
                        name="ImChart",
                        jpeg_bytes=chart_jpg,
                        width_px=chart_img.width,
                        height_px=chart_img.height,
                        x=60.0,
                        y=390.0,
                        draw_w=460.0,
                        draw_h=276.0,
                    ),
                ),
            ),
        ]
    )
    save_doc(
        "10_diagrams_charts_graphs_pdf",
        "10_diagrams_charts_graphs.pdf",
        diag_pdf,
        category="diagrams_charts_and_graphs",
        input_format="digital_pdf",
        ground_truth={
            "expected_figures": [
                {"page": 1, "kind": "vector_diagram", "caption_contains": "Figure 1"},
                {"page": 2, "kind": "raster_image", "caption_contains": "Figure 2"},
            ]
        },
    )
    save_doc(
        "10_chart_png",
        "10_chart.png",
        chart_png,
        category="diagrams_charts_and_graphs",
        input_format="png",
        ground_truth={"expected_labels": chart_labels},
    )

    # 11. Mixed digital / scanned PDF (3 pages: digital+table, scanned image-only, 2-col + diagram)
    mix_p1 = "\n".join(
        [
            _text_block(["Unit 5: Thermodynamics Mixed Packet"], x=60, start_y=735, font="/F2", size=13.5),
            _text_block(["Page 1 contains digital text and a bordered summary table."], x=60, start_y=705),
            "0.75 w",
            "60 660 m 420 660 l S",
            "60 630 m 420 630 l S",
            "60 600 m 420 600 l S",
            "60 660 m 60 600 l S",
            "240 660 m 240 600 l S",
            "420 660 m 420 600 l S",
            "BT /F1 10.0 Tf 1 0 0 1 70 640 Tm (Process) Tj 1 0 0 1 250 640 Tm (Invariant) Tj",
            "1 0 0 1 70 610 Tm (Isothermal) Tj 1 0 0 1 250 610 Tm (Temperature) Tj ET",
        ]
    )
    mix_p2 = PdfPageSpec(
        content_ops="",
        images=(
            PdfImageSpec(
                name="ImMixScan",
                jpeg_bytes=scan_jpg,
                width_px=scan_img.width,
                height_px=scan_img.height,
                x=36.0,
                y=72.0,
                draw_w=540.0,
                draw_h=675.0,
            ),
        ),
    )
    mix_p3 = "\n".join(
        [
            _text_block(["Page 3: Multi-Column Review"], x=60, start_y=735, font="/F2", size=13.0),
            "BT /F1 10.0 Tf",
            "1 0 0 1 60 690 Tm (Left Column: First Law states) Tj 1 0 0 1 320 690 Tm (Right Column: Second Law defines) Tj",
            "1 0 0 1 60 670 Tm (energy change dU = Q - W.) Tj 1 0 0 1 320 670 Tm (entropy increase in isolated systems.) Tj",
            "ET",
            "1.0 w 100 500 180 80 re S",
            _text_block(["Figure 3: Carnot Heat Engine Box"], x=100, start_y=480, font="/F2", size=10.0),
        ]
    )
    mixed_pdf = build_rich_pdf([PdfPageSpec(mix_p1), mix_p2, PdfPageSpec(mix_p3)])
    save_doc(
        "11_mixed_digital_scanned",
        "11_mixed_digital_scanned.pdf",
        mixed_pdf,
        category="mixed_digital_scanned_pdf",
        input_format="digital_pdf",
        ground_truth={
            "expected_classifications": ["needs_table", "needs_ocr", "needs_layout"],
            "expected_digital_markers": ["Unit 5: Thermodynamics Mixed Packet", "Isothermal", "Left Column: First Law states", "Right Column: Second Law defines"],
            "expected_ocr_markers": ["Conservation of Mechanical Energy"],
            "expected_table": {"page": 1, "headers": ["Process", "Invariant"], "rows": [["Isothermal", "Temperature"]]},
        },
    )

    # 12. Multilingual samples (Markdown UTF-8 + PDF WinAnsi + Multilingual PNG)
    multi_md = (
        "# Multilingual Educational Notes\n\n"
        "English: Kinetic energy equals half the mass times velocity squared.\n\n"
        "Español: Introducción a la Cinemática y aceleración centrípeta.\n\n"
        "Français: Énergie cinétique et conservation de la quantité de mouvement.\n\n"
    ).encode()
    save_doc(
        "12_multilingual_md",
        "12_multilingual_samples.md",
        multi_md,
        category="multilingual",
        input_format="typed_notes",
        ground_truth={
            "expected_languages": ["en", "es", "fr", "hi"],
            "expected_markers": [
                "Kinetic energy equals half",
                "Introducción a la Cinemática",
                "Énergie cinétique",
                "ऊर्जा संरक्षण का नियम",
            ],
        },
    )

    multi_pdf = build_rich_pdf(
        [
            PdfPageSpec(
                "\n".join(
                    [
                        _text_block(["Multilingual Physics Handout"], x=60, start_y=730, font="/F2", size=13.5),
                        _text_block(
                            [
                                "English: Conservation of momentum in elastic collisions.",
                                "Español: Introducción a la Cinemática y aceleración.",
                                "Français: Énergie cinétique et conservation totale.",
                            ],
                            x=60,
                            start_y=690,
                            step=18,
                        ),
                    ]
                )
            )
        ]
    )
    save_doc(
        "12_multilingual_pdf",
        "12_multilingual_samples.pdf",
        multi_pdf,
        category="multilingual",
        input_format="digital_pdf",
        ground_truth={
            "expected_markers": ["Conservation of momentum", "Introducción a la Cinemática", "Énergie cinétique"],
        },
    )

    multi_img, multi_img_lines = _render_multilingual_image()
    save_doc(
        "12_multilingual_png",
        "12_multilingual_scanned.png",
        _image_to_png_bytes(multi_img),
        category="multilingual",
        input_format="png",
        ground_truth={"expected_text": "\n".join(multi_img_lines)},
    )

    manifest = {
        "corpus_version": "batch_2_3.v1",
        "license": "CC0-1.0 (originally authored synthetic/representative educational samples)",
        "document_count": len(docs_meta),
        "documents": docs_meta,
    }
    GROUND_TRUTH_PATH.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return manifest


if __name__ == "__main__":
    result = generate_corpus()
    print(f"Generated {result['document_count']} representative benchmark documents in {SAMPLES_DIR}")
