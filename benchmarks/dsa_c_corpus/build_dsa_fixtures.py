"""Build representative CC0-1.0 DSA (C99) Phase 2 extraction & chunking fixtures."""

from __future__ import annotations

import hashlib
import io
import json
import sys
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFilter, ImageFont

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from benchmarks.educational_corpus.build_corpus import (
    PdfImageSpec,
    PdfPageSpec,
    build_rich_pdf,
)

DSA_DIR = Path(__file__).resolve().parent
FIXTURES_DIR = DSA_DIR / "fixtures"
GROUND_TRUTH_PATH = DSA_DIR / "ground_truth.json"


def _esc(text: str) -> str:
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _load_font(path: str, size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    try:
        return ImageFont.truetype(path, size)
    except OSError:
        return ImageFont.load_default()


def _render_raster_page(lines: list[str], *, bg: tuple[int, int, int], fg: tuple[int, int, int], blur: float, fmt: str) -> bytes:
    img = Image.new("RGB", (1200, 820), bg)
    draw = ImageDraw.Draw(img)
    title_font = _load_font("C:/Windows/Fonts/arialbd.ttf", 32)
    body_font = _load_font("C:/Windows/Fonts/arial.ttf", 26)
    y = 55
    for idx, ln in enumerate(lines):
        draw.text((55, y), ln, fill=fg, font=title_font if idx == 0 else body_font)
        y += 56 if idx == 0 else 48
    if blur > 0:
        img = img.filter(ImageFilter.GaussianBlur(radius=blur))
    buf = io.BytesIO()
    if fmt == "PNG":
        img.save(buf, format="PNG")
    else:
        img.save(buf, format="JPEG", quality=88)
    return buf.getvalue()


def build_all() -> dict[str, Any]:
    FIXTURES_DIR.mkdir(parents=True, exist_ok=True)
    docs: list[dict[str, Any]] = []

    def _register(doc_id: str, filename: str, fmt: str, raw: bytes, gt: dict[str, Any]) -> None:
        (FIXTURES_DIR / filename).write_bytes(raw)
        docs.append(
            {
                "doc_id": doc_id,
                "filename": filename,
                "relative_path": f"fixtures/{filename}",
                "input_format": fmt,
                "size_bytes": len(raw),
                "sha256": hashlib.sha256(raw).hexdigest(),
                "ground_truth": gt,
            }
        )

    # 1. Plain text & Markdown notes
    md_text = (
        b"# VIORA-DSA Study Notes: Dynamic Arrays and Circular Queues\n\n"
        b"A dynamic array doubles capacity (`new_cap = capacity * 2`) when full so amortized push stays O(1).\n\n"
        b"## Ring-Buffer FIFO Equations\n\n"
        b"Given capacity CAP, current head index, and item count:\n\n"
        b"$$tail = (head + count) \\bmod CAP$$\n\n"
        b"```c\n"
        b"size_t tail = (q->head + q->count) % CAP;\n"
        b"q->items[tail] = val;\n"
        b"q->count++;\n"
        b"```\n"
    )
    _register(
        "dsa_01_markdown_notes",
        "01_dsa_notes.md",
        "typed_notes",
        md_text,
        {
            "expected_ordered_markers": ["Dynamic Arrays", "amortized push", "Ring-Buffer", "q->items[tail] = val;"],
            "expected_code_markers": ["size_t tail = (q->head + q->count) % CAP;"],
            "expected_formulas": ["tail = (head + count) \\bmod CAP"],
        },
    )

    # 2. Code-heavy native C PDF
    code_lines = [
        ("/F2", 14, 54, 740, "Singly Linked List Reversal and Binary Search in C99"),
        ("/F3", 10, 54, 705, "static void sll_reverse(SNode **head) {"),
        ("/F3", 10, 54, 688, "    SNode *prev = NULL, *cur = *head;"),
        ("/F3", 10, 54, 671, "    while (cur != NULL) {"),
        ("/F3", 10, 54, 654, "        SNode *nxt = cur->next;"),
        ("/F3", 10, 54, 637, "        cur->next = prev;"),
        ("/F3", 10, 54, 620, "        prev = cur;"),
        ("/F3", 10, 54, 603, "        cur = nxt;"),
        ("/F3", 10, 54, 586, "    }"),
        ("/F3", 10, 54, 569, "    *head = prev;"),
        ("/F3", 10, 54, 552, "}"),
        ("/F1", 11, 54, 520, "Always save cur->next before rewriting links so the remaining tail is never lost."),
    ]
    code_ops = "BT " + " ".join(f"{f} {sz} Tf 1 0 0 1 {x} {y} Tm ({_esc(txt)}) Tj" for f, sz, x, y, txt in code_lines) + " ET"
    _register(
        "dsa_02_code_heavy_pdf",
        "02_dsa_code_heavy.pdf",
        "digital_pdf",
        build_rich_pdf([PdfPageSpec(content_ops=code_ops)]),
        {
            "expected_ordered_markers": [
                "Singly Linked List Reversal",
                "SNode *prev = NULL, *cur = *head;",
                "cur->next = prev;",
                "Always save cur->next",
            ],
            "expected_code_markers": ["static void sll_reverse(SNode **head) {"],
        },
    )

    # 3. Multi-column native C DSA comparison PDF
    left_col = [
        "Left: Bounded Stack LIFO",
        "Initialize top = -1.",
        "Push increments top before write.",
        "Pop reads items[top] then decrements.",
        "Guards top < 0 against underflow.",
    ]
    right_col = [
        "Right: Open-Address Hash Table",
        "Compute initial slot = hash(key) % CAP.",
        "Probe forward across occupied slots.",
        "Deleted slots store HT_TOMBSTONE.",
        "Stop lookups immediately on HT_EMPTY.",
    ]
    mc_ops = ["BT /F2 13 Tf 1 0 0 1 54 745 Tm (Data Structure Comparison Sheet) Tj /F1 10 Tf"]
    y = 700
    for l_txt, r_txt in zip(left_col, right_col, strict=True):
        mc_ops.append(f"1 0 0 1 54 {y} Tm ({_esc(l_txt)}) Tj")
        mc_ops.append(f"1 0 0 1 320 {y} Tm ({_esc(r_txt)}) Tj")
        y -= 20
    mc_ops.append("ET")
    _register(
        "dsa_03_multicolumn_pdf",
        "03_dsa_multicolumn.pdf",
        "digital_pdf",
        build_rich_pdf([PdfPageSpec(content_ops=" ".join(mc_ops))]),
        {
            "expected_ordered_markers": left_col + right_col,
        },
    )

    # 4. Bordered & Borderless Complexity Tables PDF
    bordered_ops = (
        "BT /F2 12 Tf 1 0 0 1 54 750 Tm (Table 1: Worst-Case Time and Auxiliary Space) Tj ET "
        "0.75 w 54 730 m 540 730 l S 54 702 m 540 702 l S 54 674 m 540 674 l S 54 646 m 540 646 l S 54 618 m 540 618 l S "
        "54 730 m 54 618 l S 210 730 m 210 618 l S 370 730 m 370 618 l S 540 730 m 540 618 l S "
        "BT /F2 10 Tf 1 0 0 1 62 712 Tm (Structure) Tj 1 0 0 1 218 712 Tm (Search) Tj 1 0 0 1 378 712 Tm (AuxSpace) Tj "
        "/F1 10 Tf "
        "1 0 0 1 62 684 Tm (SortedArray) Tj 1 0 0 1 218 684 Tm (O(log n)) Tj 1 0 0 1 378 684 Tm (O(1)) Tj "
        "1 0 0 1 62 656 Tm (MergeSort) Tj 1 0 0 1 218 656 Tm (Theta(n log n)) Tj 1 0 0 1 378 656 Tm (O(n)) Tj "
        "1 0 0 1 62 628 Tm (MinHeap) Tj 1 0 0 1 218 628 Tm (O(n)) Tj 1 0 0 1 378 628 Tm (O(1)) Tj ET"
    )
    borderless_ops = (
        "BT /F2 12 Tf 1 0 0 1 54 745 Tm (Table 2: Graph Traversal Summary) Tj "
        "/F2 10 Tf 1 0 0 1 60 708 Tm (Representation) Tj 1 0 0 1 230 708 Tm (BFS_Time) Tj 1 0 0 1 390 708 Tm (Space) Tj "
        "/F1 10 Tf "
        "1 0 0 1 60 684 Tm (AdjList) Tj 1 0 0 1 230 684 Tm (O(V+E)) Tj 1 0 0 1 390 684 Tm (O(V)) Tj "
        "1 0 0 1 60 660 Tm (AdjMatrix) Tj 1 0 0 1 230 660 Tm (O(V^2)) Tj 1 0 0 1 390 660 Tm (O(V^2)) Tj "
        "1 0 0 1 60 636 Tm (EdgeList) Tj 1 0 0 1 230 636 Tm (O(V*E)) Tj 1 0 0 1 390 636 Tm (O(E)) Tj ET"
    )
    _register(
        "dsa_04_complexity_tables",
        "04_dsa_complexity_tables.pdf",
        "digital_pdf",
        build_rich_pdf([PdfPageSpec(content_ops=bordered_ops), PdfPageSpec(content_ops=borderless_ops)]),
        {
            "expected_table_headers": [("Structure", "Search", "AuxSpace"), ("Representation", "BFS_Time", "Space")],
            "expected_table_rows": [["SortedArray", "O(log n)", "O(1)"], ["AdjList", "O(V+E)", "O(V)"]],
        },
    )

    # 5. Recurrences & mathematical notation PDF
    math_ops = (
        "BT /F2 14 Tf 1 0 0 1 54 745 Tm (Recurrence and Index Equations for C Algorithms) Tj "
        "/F1 11 Tf "
        "1 0 0 1 54 705 Tm (Divide and Conquer: T(n) = 2*T(n/2) + n => T(n) = Theta(n log n)) Tj "
        "1 0 0 1 54 680 Tm (Overflow-safe midpoint: mid = lo + (hi - lo) / 2) Tj "
        "1 0 0 1 54 655 Tm (Binary heap parent: parent(i) = (i - 1) / 2 for i > 0) Tj "
        "1 0 0 1 54 630 Tm (Open-address linear probe: pos = (hash(k) + step) mod CAP) Tj ET"
    )
    _register(
        "dsa_05_recurrence_math",
        "05_dsa_recurrence_math.pdf",
        "digital_pdf",
        build_rich_pdf([PdfPageSpec(content_ops=math_ops)]),
        {
            "expected_formulas": ["T(n) = 2*T(n/2) + n", "mid = lo + (hi - lo) / 2"],
        },
    )

    # 6. Scanned page PNG + wrapped PDF
    scan_lines = [
        "Binary Min-Heap Invariant Notes",
        "Every parent node stores key <= both children keys.",
        "Push appends at tail and repeats sift-up while parent > child.",
        "Extract-min moves tail to root and repeats sift-down.",
    ]
    scan_png = _render_raster_page(scan_lines, bg=(247, 247, 245), fg=(28, 28, 30), blur=0.9, fmt="PNG")
    scan_jpg = _render_raster_page(scan_lines, bg=(247, 247, 245), fg=(28, 28, 30), blur=0.9, fmt="JPEG")
    _register(
        "dsa_06_scanned_notes_png",
        "06_dsa_scanned_notes.png",
        "png",
        scan_png,
        {"expected_text": "\n".join(scan_lines)},
    )
    _register(
        "dsa_06b_scanned_notes_pdf",
        "06b_dsa_scanned_notes.pdf",
        "scanned_pdf",
        build_rich_pdf([
            PdfPageSpec(
                content_ops="q 540 0 0 369 36 360 cm /ImScan Do Q",
                images=(PdfImageSpec("ImScan", scan_jpg, 1200, 820, 36, 360, 540, 369),),
            )
        ]),
        {"expected_text": "\n".join(scan_lines)},
    )

    # 7. Photographed low-contrast notes JPEG
    photo_lines = [
        "Adjacency List Breadth-First Search",
        "Mark neighbor visited before pushing onto FIFO ring queue.",
        "Each reachable edge is inspected once in O(V + E) time.",
    ]
    photo_jpg = _render_raster_page(photo_lines, bg=(214, 212, 204), fg=(72, 74, 70), blur=0.7, fmt="JPEG")
    _register(
        "dsa_07_photographed_notes_jpg",
        "07_dsa_photographed_notes.jpg",
        "jpeg",
        photo_jpg,
        {"expected_text": "\n".join(photo_lines)},
    )

    manifest = {
        "corpus_id": "viora-dsa-c-fixtures.v1",
        "license": "CC0-1.0",
        "document_count": len(docs),
        "documents": docs,
    }
    GROUND_TRUTH_PATH.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


if __name__ == "__main__":
    res = build_all()
    print(f"Built {res['document_count']} DSA Phase 2 fixtures into {FIXTURES_DIR}")
