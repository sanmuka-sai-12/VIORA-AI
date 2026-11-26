"""Spatial reading-order reconstruction, table extraction, and figure detection via pdfplumber."""

from __future__ import annotations

import re
import statistics
from dataclasses import dataclass
from typing import Any

from .document_contracts import BoundingBox

SPATIAL_EXTRACTOR_ID = "viora.sources.pdfplumber_spatial"
SPATIAL_EXTRACTOR_VERSION = "2.0"

_CAPTION_RE = re.compile(
    r"^(?:figure|fig\.?|table|chart|diagram)\s+\d+(?:\.\d+)?[:.\-]?\s*",
    re.IGNORECASE,
)
_CODE_FONT_RE = re.compile(r"(?:courier|consola|mono|menlo|inconsolata|fira)", re.IGNORECASE)
_BOLD_FONT_RE = re.compile(r"(?:bold|black|heavy|demi|semibold)", re.IGNORECASE)
_PAGE_NUMBER_RE = re.compile(r"^(?:page\s+\d+(?:\s+of\s+\d+)?|\d{1,4})$", re.IGNORECASE)


def normalize_header_footer_template(line: str) -> str:
    """Normalize trailing page-number suffixes so running footers like 'Book - Page 1'/'Page 2' match."""
    collapsed = re.sub(r"\s+", " ", line.strip())
    collapsed = re.sub(
        r"(?:^|(?<=\s))(page\s+)\d+(?:\s+of\s+\d+)?\s*$",
        r"\1#",
        collapsed,
        flags=re.IGNORECASE,
    )
    return re.sub(r"\s+\d{1,4}$", " #", collapsed)


@dataclass(frozen=True)
class SpatialTextBlock:
    kind: str  # heading, paragraph, list_item, code, caption, header_or_footer
    text: str
    raw_text: str
    bbox: BoundingBox
    heading_level: int | None = None
    has_monospace: bool = False


@dataclass(frozen=True)
class SpatialTable:
    headers: tuple[str, ...]
    rows: tuple[tuple[str, ...], ...]
    bbox: BoundingBox
    strategy: str  # lines, text
    extraction_state: str  # parsed, partial, detected_unparsed
    caption: str | None = None
    warning_message: str | None = None


@dataclass(frozen=True)
class SpatialFigure:
    kind: str  # raster_image, vector_diagram, chart
    bbox: BoundingBox
    caption: str | None = None


@dataclass(frozen=True)
class SpatialPageResult:
    page_number: int
    width: float
    height: float
    blocks: tuple[SpatialTextBlock, ...]
    tables: tuple[SpatialTable, ...]
    figures: tuple[SpatialFigure, ...]
    suspected_multicolumn: bool
    column_count: int
    reading_order_reliable: bool
    warnings: tuple[tuple[str, str, str], ...]  # (code, severity, message)


def _clamp_bbox(
    x0: float,
    y0: float,
    x1: float,
    y1: float,
    pw: float,
    ph: float,
) -> BoundingBox:
    cx0 = max(0.0, min(float(x0), pw))
    cy0 = max(0.0, min(float(y0), ph))
    cx1 = max(cx0, min(float(x1), pw))
    cy1 = max(cy0, min(float(y1), ph))
    return BoundingBox(
        x0=round(cx0, 2),
        y0=round(cy0, 2),
        x1=round(cx1, 2),
        y1=round(cy1, 2),
        coordinate_space="pdf_points",
        page_width=round(pw, 2),
        page_height=round(ph, 2),
    )


def _point_in_any_box(x: float, y: float, boxes: list[BoundingBox], pad: float = 2.0) -> bool:
    return any(
        (b.x0 - pad) <= x <= (b.x1 + pad) and (b.y0 - pad) <= y <= (b.y1 + pad)
        for b in boxes
    )


def _boxes_overlap(a: BoundingBox, b: BoundingBox, pad: float = 4.0) -> bool:
    return not (
        a.x1 + pad < b.x0
        or b.x1 + pad < a.x0
        or a.y1 + pad < b.y0
        or b.y1 + pad < a.y0
    )


def _clean_cell(val: str | None) -> str:
    if val is None:
        return ""
    return re.sub(r"\s+", " ", val.replace("\n", " ").strip())


def _is_header_row(row: tuple[str, ...], body_rows: list[tuple[str, ...]]) -> bool:
    if not row or not body_rows:
        return False
    numeric_in_header = sum(
        1 for c in row if re.fullmatch(r"[-+]?\d+(?:\.\d+)?%?", c) is not None
    )
    numeric_in_body = sum(
        1
        for r in body_rows
        for c in r
        if re.fullmatch(r"[-+]?\d+(?:\.\d+)?%?", c) is not None
    )
    return numeric_in_header == 0 and (
        numeric_in_body > 0 or len(body_rows) >= 1
    )


def _extract_spatial_tables(
    page: Any,
    pw: float,
    ph: float,
    *,
    allow_borderless: bool = True,
) -> tuple[list[SpatialTable], list[tuple[str, str, str]]]:
    tables: list[SpatialTable] = []
    warnings: list[tuple[str, str, str]] = []

    has_lines = len(getattr(page, "lines", ()) or ()) >= 4 or len(
        getattr(page, "rects", ()) or ()
    ) >= 2

    def _convert_tables(found: list[Any], strategy: str) -> list[SpatialTable]:
        out: list[SpatialTable] = []
        for tbl in found:
            raw_rows = tbl.extract() or []
            cleaned = [
                tuple(_clean_cell(c) for c in row)
                for row in raw_rows
                if any(_clean_cell(c) for c in row)
            ]
            if len(cleaned) < 2:
                continue
            col_count = max(len(r) for r in cleaned)
            if col_count < 2:
                continue
            normalized_rows = [
                r + ("",) * (col_count - len(r)) for r in cleaned
            ]
            # Drop columns that are completely empty across all rows
            keep_cols = [
                ci
                for ci in range(col_count)
                if any(r[ci] != "" for r in normalized_rows)
            ]
            if len(keep_cols) < 2:
                continue
            final_rows = [tuple(r[ci] for ci in keep_cols) for r in normalized_rows]
            non_empty = sum(1 for r in final_rows for c in r if c)
            total_cells = len(final_rows) * len(keep_cols)
            fill_ratio = non_empty / max(1, total_cells)

            if strategy == "text":
                # Trim leading/trailing caption or single-span prose rows accidentally pulled into borderless table
                while final_rows and (
                    _CAPTION_RE.match(" ".join(c for c in final_rows[0] if c))
                    or sum(1 for c in final_rows[0] if c) <= 1
                ):
                    final_rows.pop(0)
                while final_rows and (
                    sum(1 for c in final_rows[-1] if c) <= 1
                    or (
                        sum(1 for c in final_rows[-1] if c) == 2
                        and len(keep_cols) >= 3
                    )
                ):
                    final_rows.pop()
                if len(final_rows) < 3:
                    continue
                # Recompute populated columns after trimming
                keep_cols = [
                    ci
                    for ci in range(len(final_rows[0]))
                    if any(r[ci] != "" for r in final_rows)
                ]
                if len(keep_cols) < 2:
                    continue
                final_rows = [tuple(r[ci] for ci in keep_cols) for r in final_rows]
                non_empty = sum(1 for r in final_rows for c in r if c)
                total_cells = len(final_rows) * len(keep_cols)
                fill_ratio = non_empty / max(1, total_cells)
                # Guard against misclassifying 2-column prose as a borderless table
                avg_words = sum(len(c.split()) for r in final_rows for c in r if c) / max(
                    1, non_empty
                )
                if len(final_rows) == 2 and avg_words > 4.0:
                    continue
                if len(keep_cols) == 2 and avg_words > 4.0:
                    continue
                if fill_ratio < 0.55:
                    continue

            # Tighten bbox to actual non-trimmed rows when borderless rows were trimmed
            if strategy == "text" and hasattr(tbl, "rows") and tbl.rows:
                first_txt = final_rows[0][0] if final_rows[0] else ""
                last_txt = final_rows[-1][0] if final_rows[-1] else ""
                cell_words = [
                    w
                    for w in (page.extract_words(x_tolerance=3, y_tolerance=3) or [])
                    if tbl.bbox[0] - 2 <= float(w["x0"])
                    and float(w["x1"]) <= tbl.bbox[2] + 2
                    and tbl.bbox[1] - 2 <= float(w["top"])
                    and float(w["bottom"]) <= tbl.bbox[3] + 2
                    and not _CAPTION_RE.match(str(w["text"]))
                ]
                caption_tops = [
                    float(w["top"])
                    for w in cell_words
                    if _CAPTION_RE.match(str(w["text"]))
                ]
                min_data_top = (max(caption_tops) + 6.0) if caption_tops else -1.0
                matching = [
                    w
                    for w in cell_words
                    if float(w["top"]) >= min_data_top
                    and any(
                        str(w["text"]) == cell or str(w["text"]) in cell.split()
                        for row in final_rows
                        for cell in row
                        if cell
                    )
                ]
                if matching:
                    bbox = _clamp_bbox(
                        min(float(w["x0"]) for w in matching),
                        min(float(w["top"]) for w in matching),
                        max(float(w["x1"]) for w in matching),
                        max(float(w["bottom"]) for w in matching),
                        pw,
                        ph,
                    )
                else:
                    bbox = _clamp_bbox(*tbl.bbox, pw, ph)
                _ = (first_txt, last_txt)
            else:
                bbox = _clamp_bbox(*tbl.bbox, pw, ph)
            if (bbox.x1 - bbox.x0) < 20.0 or (bbox.y1 - bbox.y0) < 16.0:
                continue

            if _is_header_row(final_rows[0], final_rows[1:]):
                headers = final_rows[0]
                data_rows = tuple(final_rows[1:])
            else:
                headers = ()
                data_rows = tuple(final_rows)

            warn_msg: str | None = None
            state = "parsed"
            if fill_ratio < 0.75:
                state = "partial"
                warn_msg = (
                    f"Extracted table on page {page.page_number} has sparse cells "
                    f"({non_empty}/{total_cells} populated); structure may be incomplete."
                )
                warnings.append(("table_extraction_required", "warning", warn_msg))
            elif strategy == "text":
                warn_msg = (
                    f"Borderless table on page {page.page_number} reconstructed via "
                    "whitespace column alignment; verify column boundaries if ambiguous."
                )
                warnings.append(("table_extraction_required", "info", warn_msg))

            out.append(
                SpatialTable(
                    headers=headers,
                    rows=data_rows,
                    bbox=bbox,
                    strategy=strategy,
                    extraction_state=state,
                    warning_message=warn_msg,
                )
            )
        return out

    if has_lines:
        lined = page.find_tables(
            table_settings={
                "vertical_strategy": "lines",
                "horizontal_strategy": "lines",
                "snap_tolerance": 4,
                "join_tolerance": 4,
            }
        )
        tables.extend(_convert_tables(lined or [], "lines"))

    if not tables and allow_borderless:
        # Try borderless text-aligned table detection with wide word-merge tolerance (>= 10pt)
        borderless = page.find_tables(
            table_settings={
                "vertical_strategy": "text",
                "horizontal_strategy": "text",
                "min_words_vertical": 3,
                "min_words_horizontal": 2,
                "text_x_tolerance": 12,
                "text_y_tolerance": 4,
                "intersection_x_tolerance": 12,
            }
        )
        tables.extend(_convert_tables(borderless or [], "text"))

    return tables, warnings


def _detect_figures_and_diagrams(
    page: Any,
    pw: float,
    ph: float,
    table_boxes: list[BoundingBox],
) -> list[SpatialFigure]:
    figures: list[SpatialFigure] = []

    for img in getattr(page, "images", ()) or ():
        bbox = _clamp_bbox(
            img.get("x0", 0.0),
            img.get("top", 0.0),
            img.get("x1", 0.0),
            img.get("bottom", 0.0),
            pw,
            ph,
        )
        if (bbox.x1 - bbox.x0) >= 12.0 and (bbox.y1 - bbox.y0) >= 12.0:
            figures.append(SpatialFigure(kind="raster_image", bbox=bbox))

    # Cluster non-table vector primitives (rects, lines, curves) into diagram regions
    prims: list[BoundingBox] = []
    for item in (
        list(getattr(page, "rects", ()) or ())
        + list(getattr(page, "lines", ()) or ())
        + list(getattr(page, "curves", ()) or ())
    )[:500]:
        box = _clamp_bbox(
            item.get("x0", 0.0),
            item.get("top", 0.0),
            item.get("x1", 0.0),
            item.get("bottom", 0.0),
            pw,
            ph,
        )
        cx = (box.x0 + box.x1) * 0.5
        cy = (box.y0 + box.y1) * 0.5
        if _point_in_any_box(cx, cy, table_boxes, pad=4.0):
            continue
        # Ignore full-page frames or hairlines
        if (box.x1 - box.x0) > pw * 0.92 and (box.y1 - box.y0) > ph * 0.92:
            continue
        prims.append(box)

    if len(prims) >= 3:
        clusters: list[list[BoundingBox]] = []
        for pbox in prims:
            placed = False
            for cl in clusters:
                if any(_boxes_overlap(pbox, existing, pad=18.0) for existing in cl):
                    cl.append(pbox)
                    placed = True
                    break
            if not placed:
                clusters.append([pbox])

        # Merge clusters that touch after additions
        merged = True
        while merged and len(clusters) > 1:
            merged = False
            for i in range(len(clusters)):
                for j in range(i + 1, len(clusters)):
                    if any(
                        _boxes_overlap(a, b, pad=18.0)
                        for a in clusters[i]
                        for b in clusters[j]
                    ):
                        clusters[i].extend(clusters[j])
                        clusters.pop(j)
                        merged = True
                        break
                if merged:
                    break

        for cl in clusters:
            if len(cl) < 3:
                continue
            bx0 = min(b.x0 for b in cl)
            by0 = min(b.y0 for b in cl)
            bx1 = max(b.x1 for b in cl)
            by1 = max(b.y1 for b in cl)
            area = (bx1 - bx0) * (by1 - by0)
            if area >= 2500.0 and (bx1 - bx0) >= 40.0 and (by1 - by0) >= 30.0:
                diag_box = _clamp_bbox(bx0, by0, bx1, by1, pw, ph)
                if not any(_boxes_overlap(diag_box, f.bbox, pad=4.0) for f in figures):
                    figures.append(SpatialFigure(kind="vector_diagram", bbox=diag_box))

    figures.sort(key=lambda f: (f.bbox.y0, f.bbox.x0))
    return figures


@dataclass
class _VisualLine:
    words: list[dict[str, Any]]
    x0: float
    y0: float
    x1: float
    y1: float
    text: str
    avg_size: float
    is_bold: bool
    is_mono: bool
    is_full_width: bool = False


def _build_visual_line(words: list[dict[str, Any]]) -> _VisualLine:
    words_sorted = sorted(words, key=lambda w: float(w["x0"]))
    x0 = min(float(w["x0"]) for w in words_sorted)
    y0 = min(float(w["top"]) for w in words_sorted)
    x1 = max(float(w["x1"]) for w in words_sorted)
    y1 = max(float(w["bottom"]) for w in words_sorted)
    sizes = [float(w.get("size") or 10.0) for w in words_sorted]
    fonts = [str(w.get("fontname") or "") for w in words_sorted]
    mono_hits = sum(1 for fn in fonts if _CODE_FONT_RE.search(fn))
    bold_hits = sum(1 for fn in fonts if _BOLD_FONT_RE.search(fn))
    is_mono = mono_hits >= max(1, len(fonts) // 2)
    is_bold = bold_hits >= max(1, len(fonts) // 2)

    if is_mono:
        parts: list[str] = []
        prev_x1: float | None = None
        char_w = max(4.5, (sum(sizes) / len(sizes)) * 0.58)
        for w in words_sorted:
            if prev_x1 is not None:
                gap = float(w["x0"]) - prev_x1
                spaces = max(1, round(gap / char_w)) if gap > char_w * 0.4 else 1
                parts.append(" " * spaces)
            parts.append(str(w["text"]))
            prev_x1 = float(w["x1"])
        text = "".join(parts)
    else:
        text = " ".join(str(w["text"]) for w in words_sorted)

    return _VisualLine(
        words=words_sorted,
        x0=x0,
        y0=y0,
        x1=x1,
        y1=y1,
        text=text.strip() if not is_mono else text,
        avg_size=sum(sizes) / len(sizes),
        is_bold=is_bold,
        is_mono=is_mono,
    )


def _group_words_into_rows(words: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    if not words:
        return []
    ordered = sorted(words, key=lambda w: (float(w["top"]), float(w["x0"])))
    rows: list[list[dict[str, Any]]] = [[ordered[0]]]
    anchor_top = float(ordered[0]["top"])
    for w in ordered[1:]:
        if abs(float(w["top"]) - anchor_top) <= 3.5:
            rows[-1].append(w)
        else:
            rows.append([w])
            anchor_top = float(w["top"])
    return rows


def _detect_column_gutter(
    rows: list[list[dict[str, Any]]],
    pw: float,
) -> float | None:
    """Find a vertical whitespace gutter separating multi-column body text."""
    if pw < 200.0 or len(rows) < 4:
        return None
    int_w = max(1, int(pw))
    occupancy = [0] * (int_w + 1)
    left_rows = 0
    right_rows = 0
    mid_lo = int(pw * 0.28)
    mid_hi = int(pw * 0.72)

    candidate_rows: list[list[dict[str, Any]]] = []
    for row in rows:
        row_sorted = sorted(row, key=lambda w: float(w["x0"]))
        row_x0 = float(row_sorted[0]["x0"])
        row_x1 = float(row_sorted[-1]["x1"])
        span = row_x1 - row_x0
        # Check if row has a large internal horizontal gap (>= 8.5pt) or crosses center continuously
        max_gap = 0.0
        for i in range(len(row_sorted) - 1):
            max_gap = max(max_gap, float(row_sorted[i + 1]["x0"]) - float(row_sorted[i]["x1"]))
        crosses_center = row_x0 < pw * 0.42 and row_x1 > pw * 0.56 and max_gap < 8.5
        if (span > pw * 0.62 and max_gap < 8.5) or crosses_center:
            # True full-width prose/heading/centered-footer line; exclude from body gutter histogram
            continue
        candidate_rows.append(row_sorted)
        for w in row_sorted:
            s = max(0, int(float(w["x0"])))
            e = min(int_w, int(float(w["x1"])))
            for x in range(s, e + 1):
                occupancy[x] += 1

    if len(candidate_rows) < 2:
        return None

    max_cross = max(1, int(len(candidate_rows) * 0.15))
    best_run_start = -1
    best_run_len = 0
    cur_start = -1
    for x in range(mid_lo, mid_hi):
        if occupancy[x] <= max_cross:
            if cur_start == -1:
                cur_start = x
        else:
            if cur_start != -1 and (x - cur_start) > best_run_len:
                best_run_start = cur_start
                best_run_len = x - cur_start
            cur_start = -1
    if cur_start != -1 and (mid_hi - cur_start) > best_run_len:
        best_run_start = cur_start
        best_run_len = mid_hi - cur_start

    if best_run_len < 8:
        return None

    split_x = best_run_start + best_run_len * 0.5
    co_occurring = 0
    for row in candidate_rows:
        has_left = any(float(w["x1"]) <= split_x for w in row)
        has_right = any(float(w["x0"]) >= split_x for w in row)
        if has_left:
            left_rows += 1
        if has_right:
            right_rows += 1
        if has_left and has_right:
            co_occurring += 1

    if left_rows >= 2 and right_rows >= 2 and co_occurring >= 2:
        return split_x
    return None


def _split_row_across_gutter(
    row: list[dict[str, Any]],
    split_x: float | None,
    pw: float,
) -> list[tuple[str, _VisualLine]]:
    """Split a Y-row into ('full'|'left'|'right', _VisualLine) using intra-line gap inspection."""
    ordered = sorted(row, key=lambda w: float(w["x0"]))
    if split_x is None:
        return [("full", _build_visual_line(ordered))]

    left_words = [w for w in ordered if (float(w["x0"]) + float(w["x1"])) * 0.5 < split_x]
    right_words = [w for w in ordered if (float(w["x0"]) + float(w["x1"])) * 0.5 >= split_x]

    if left_words and right_words:
        gap = float(right_words[0]["x0"]) - float(left_words[-1]["x1"])
        # If gap across split_x is normal word spacing (<= 8.5pt), this is a full-width span!
        if gap <= 8.5:
            vl = _build_visual_line(ordered)
            vl.is_full_width = True
            return [("full", vl)]
        return [
            ("left", _build_visual_line(left_words)),
            ("right", _build_visual_line(right_words)),
        ]

    # Single-side row: treat as full-width only if it physically spans > 58% of page width
    vl = _build_visual_line(ordered)
    if (vl.x1 - vl.x0) > pw * 0.58:
        vl.is_full_width = True
        return [("full", vl)]
    side = "left" if (vl.x0 + vl.x1) * 0.5 < split_x else "right"
    return [(side, vl)]


def _reconstruct_stream_blocks(
    lines: list[_VisualLine],
    *,
    median_size: float,
    pw: float,
    ph: float,
) -> list[SpatialTextBlock]:
    if not lines:
        return []
    blocks: list[SpatialTextBlock] = []

    i = 0
    while i < len(lines):
        ln = lines[i]
        # 1. Monospace code block reconstruction preserving indentation relative to block min x0
        if ln.is_mono:
            code_lines = [ln]
            i += 1
            while i < len(lines) and lines[i].is_mono and (lines[i].y0 - code_lines[-1].y1) <= 14.0:
                code_lines.append(lines[i])
                i += 1
            base_x0 = min(c.x0 for c in code_lines)
            char_w = max(4.5, (sum(c.avg_size for c in code_lines) / len(code_lines)) * 0.58)
            rendered_lines: list[str] = []
            for cl in code_lines:
                indent_spaces = max(0, round((cl.x0 - base_x0) / char_w))
                rendered_lines.append((" " * indent_spaces) + cl.text.rstrip())
            code_txt = "\n".join(rendered_lines)
            bbox = _clamp_bbox(
                min(c.x0 for c in code_lines),
                min(c.y0 for c in code_lines),
                max(c.x1 for c in code_lines),
                max(c.y1 for c in code_lines),
                pw,
                ph,
            )
            blocks.append(
                SpatialTextBlock(
                    kind="code",
                    text=code_txt,
                    raw_text=code_txt,
                    bbox=bbox,
                    has_monospace=True,
                )
            )
            continue

        # 2. Caption line
        if _CAPTION_RE.match(ln.text):
            cap_lines = [ln]
            i += 1
            while (
                i < len(lines)
                and not lines[i].is_mono
                and not _CAPTION_RE.match(lines[i].text)
                and (lines[i].y0 - cap_lines[-1].y1) <= max(6.0, ln.avg_size * 0.7)
                and abs(lines[i].x0 - ln.x0) <= 15.0
            ):
                cap_lines.append(lines[i])
                i += 1
            txt = " ".join(c.text for c in cap_lines)
            bbox = _clamp_bbox(
                min(c.x0 for c in cap_lines),
                min(c.y0 for c in cap_lines),
                max(c.x1 for c in cap_lines),
                max(c.y1 for c in cap_lines),
                pw,
                ph,
            )
            blocks.append(SpatialTextBlock(kind="caption", text=txt, raw_text=txt, bbox=bbox))
            continue

        # 3. Heading line (font size >= 1.15x median OR bold short standalone line)
        is_large = ln.avg_size >= (median_size * 1.15)
        is_short_bold = ln.is_bold and len(ln.text) <= 90 and not ln.text.endswith(".")
        if is_large or is_short_bold:
            level = 1 if ln.avg_size >= median_size * 1.45 else 2
            bbox = _clamp_bbox(ln.x0, ln.y0, ln.x1, ln.y1, pw, ph)
            blocks.append(
                SpatialTextBlock(
                    kind="heading",
                    text=ln.text,
                    raw_text=ln.text,
                    bbox=bbox,
                    heading_level=level,
                )
            )
            i += 1
            continue

        # 4. Paragraph / list item clustering
        para = [ln]
        i += 1
        line_h = max(10.0, ln.y1 - ln.y0)
        while i < len(lines):
            nxt = lines[i]
            if nxt.is_mono or _CAPTION_RE.match(nxt.text):
                break
            if nxt.avg_size >= (median_size * 1.15) or (
                nxt.is_bold and len(nxt.text) <= 90 and not nxt.text.endswith(".")
            ):
                break
            gap_y = nxt.y0 - para[-1].y1
            if gap_y > line_h * 0.95:
                break
            if re.match(r"^(?:[-*•]|\d+[.)])\s+", nxt.text):
                break
            para.append(nxt)
            i += 1

        joined = "\n".join(p.text for p in para)
        bbox = _clamp_bbox(
            min(p.x0 for p in para),
            min(p.y0 for p in para),
            max(p.x1 for p in para),
            max(p.y1 for p in para),
            pw,
            ph,
        )
        kind = "list_item" if re.match(r"^(?:[-*•]|\d+[.)])\s+", para[0].text) else "paragraph"
        blocks.append(SpatialTextBlock(kind=kind, text=joined, raw_text=joined, bbox=bbox))

    return blocks


def _associate_captions(
    blocks: list[SpatialTextBlock],
    tables: list[SpatialTable],
    figures: list[SpatialFigure],
) -> tuple[list[SpatialTable], list[SpatialFigure]]:
    captions = [b for b in blocks if b.kind == "caption"]
    if not captions:
        return tables, figures

    updated_tables = list(tables)
    updated_figures = list(figures)

    for cap in captions:
        txt_l = cap.text.lower()
        if txt_l.startswith("table"):
            best_idx = -1
            best_dist = 1e9
            for idx, tbl in enumerate(updated_tables):
                dist = min(abs(cap.bbox.y1 - tbl.bbox.y0), abs(tbl.bbox.y1 - cap.bbox.y0))
                if dist < best_dist and dist <= 90.0:
                    best_dist = dist
                    best_idx = idx
            if best_idx != -1:
                t = updated_tables[best_idx]
                updated_tables[best_idx] = SpatialTable(
                    headers=t.headers,
                    rows=t.rows,
                    bbox=t.bbox,
                    strategy=t.strategy,
                    extraction_state=t.extraction_state,
                    caption=cap.text[:512],
                    warning_message=t.warning_message,
                )
        elif txt_l.startswith(("figure", "fig", "chart", "diagram")):
            best_idx = -1
            best_dist = 1e9
            for idx, fig in enumerate(updated_figures):
                dist = min(abs(cap.bbox.y1 - fig.bbox.y0), abs(fig.bbox.y1 - cap.bbox.y0))
                if dist < best_dist and dist <= 90.0:
                    best_dist = dist
                    best_idx = idx
            if best_idx != -1:
                f = updated_figures[best_idx]
                updated_figures[best_idx] = SpatialFigure(
                    kind=f.kind,
                    bbox=f.bbox,
                    caption=cap.text[:512],
                )
    return updated_tables, updated_figures


def analyze_page_spatial(
    page: Any,
    *,
    repeated_header_templates: frozenset[str] = frozenset(),
    repeated_footer_templates: frozenset[str] = frozenset(),
    extract_tables: bool = True,
    allow_borderless_tables: bool = True,
) -> SpatialPageResult:
    """Reconstruct reading order, tables, headers/footers, and figures on one pdfplumber page."""
    pw = float(getattr(page, "width", 612.0) or 612.0)
    ph = float(getattr(page, "height", 792.0) or 792.0)
    warnings: list[tuple[str, str, str]] = []

    tables: list[SpatialTable] = []
    if extract_tables:
        tables, tbl_warnings = _extract_spatial_tables(
            page, pw, ph, allow_borderless=allow_borderless_tables
        )
        warnings.extend(tbl_warnings)
    table_boxes = [t.bbox for t in tables]

    figures = _detect_figures_and_diagrams(page, pw, ph, table_boxes)

    raw_words = (
        page.extract_words(
            x_tolerance=3,
            y_tolerance=3,
            keep_blank_chars=False,
            use_text_flow=False,
            extra_attrs=["fontname", "size"],
        )
        or []
    )
    if len(raw_words) > 25_000:
        raw_words = raw_words[:25_000]
        warnings.append(
            (
                "truncation_warning",
                "warning",
                f"Page {page.page_number} exceeded 25,000 word tokens and was truncated.",
            )
        )

    non_table_words = [
        w
        for w in raw_words
        if not _point_in_any_box(
            (float(w["x0"]) + float(w["x1"])) * 0.5,
            (float(w["top"]) + float(w["bottom"])) * 0.5,
            table_boxes,
            pad=2.0,
        )
    ]

    all_rows = _group_words_into_rows(non_table_words)
    header_footer_blocks: list[SpatialTextBlock] = []
    body_rows: list[list[dict[str, Any]]] = []

    for row in all_rows:
        vl = _build_visual_line(row)
        tmpl = normalize_header_footer_template(vl.text)
        in_top_band = vl.y0 < ph * 0.12
        in_bot_band = vl.y1 > ph * 0.88
        is_rep_hdr = in_top_band and (tmpl in repeated_header_templates)
        is_rep_ftr = in_bot_band and (
            tmpl in repeated_footer_templates or bool(_PAGE_NUMBER_RE.match(vl.text))
        )
        if is_rep_hdr or is_rep_ftr:
            header_footer_blocks.append(
                SpatialTextBlock(
                    kind="header_or_footer",
                    text=vl.text,
                    raw_text=vl.text,
                    bbox=_clamp_bbox(vl.x0, vl.y0, vl.x1, vl.y1, pw, ph),
                )
            )
        else:
            body_rows.append(row)

    body_sizes = [
        float(w.get("size") or 10.0)
        for row in body_rows
        for w in row
    ]
    median_size = statistics.median(body_sizes) if body_sizes else 10.0

    split_x = _detect_column_gutter(body_rows, pw)
    suspected_multicolumn = split_x is not None
    column_count = 2 if split_x is not None else 1

    # Partition body rows into ordered bands separated by full-width elements
    ordered_blocks: list[SpatialTextBlock] = []
    full_buf: list[_VisualLine] = []
    left_buf: list[_VisualLine] = []
    right_buf: list[_VisualLine] = []

    def _flush_full_buffer() -> None:
        if full_buf:
            ordered_blocks.extend(
                _reconstruct_stream_blocks(full_buf, median_size=median_size, pw=pw, ph=ph)
            )
            full_buf.clear()

    def _flush_column_buffers() -> None:
        if left_buf:
            ordered_blocks.extend(
                _reconstruct_stream_blocks(left_buf, median_size=median_size, pw=pw, ph=ph)
            )
            left_buf.clear()
        if right_buf:
            ordered_blocks.extend(
                _reconstruct_stream_blocks(right_buf, median_size=median_size, pw=pw, ph=ph)
            )
            right_buf.clear()

    for row in body_rows:
        segments = _split_row_across_gutter(row, split_x, pw)
        if len(segments) == 1 and segments[0][0] == "full":
            _flush_column_buffers()
            full_buf.append(segments[0][1])
        else:
            _flush_full_buffer()
            for side, seg in segments:
                if side == "left":
                    left_buf.append(seg)
                else:
                    right_buf.append(seg)
    _flush_full_buffer()
    _flush_column_buffers()

    final_tables, final_figures = _associate_captions(ordered_blocks, tables, figures)
    # Retain header/footer blocks with explicit kind after main body reading order
    all_blocks = ordered_blocks + header_footer_blocks

    if not body_rows and not final_tables and final_figures:
        reading_reliable = False
    else:
        reading_reliable = True

    return SpatialPageResult(
        page_number=int(page.page_number),
        width=pw,
        height=ph,
        blocks=tuple(all_blocks),
        tables=tuple(final_tables),
        figures=tuple(final_figures),
        suspected_multicolumn=suspected_multicolumn,
        column_count=column_count,
        reading_order_reliable=reading_reliable,
        warnings=tuple(warnings),
    )
