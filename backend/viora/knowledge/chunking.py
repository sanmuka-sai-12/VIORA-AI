"""Deterministic structure-aware adaptive document chunking (zero LLM calls)."""

from __future__ import annotations

import math
import time
from dataclasses import dataclass

from ..sources.public import (
    EvidenceReference,
    ExtractedDocument,
    FigureReference,
    FormulaReference,
    SourceSection,
    TableBlock,
    TextBlock,
)
from .schemas import AdaptiveChunkPlan, ChunkingConfig, ChunkingMode, DocumentChunk


def estimate_tokens(text: str, *, chars_per_token: float = 4.0) -> int:
    """Conservative bounded token estimator (no heavy tokenizer download needed)."""
    if not text:
        return 0
    return max(1, math.ceil(len(text) / max(1.0, chars_per_token)))


@dataclass(frozen=True)
class _AtomicUnit:
    unit_kind: str
    rendered_text: str
    page_number: int
    reading_order: int
    section_id: str
    block_ids: tuple[str, ...] = ()
    table_ids: tuple[str, ...] = ()
    figure_ids: tuple[str, ...] = ()
    formula_ids: tuple[str, ...] = ()
    evidence_ids: tuple[str, ...] = ()
    is_code: bool = False
    is_table: bool = False
    is_formula: bool = False
    is_figure: bool = False
    split_allowed: bool = True


def _format_table_unit(table: TableBlock) -> str:
    lines: list[str] = []
    if table.caption:
        lines.append(f"[TABLE CAPTION: {table.caption}]")
    else:
        lines.append("[TABLE]")
    if table.headers:
        lines.append(" | ".join(table.headers))
        lines.append(" | ".join("---" for _ in table.headers))
    for row in table.rows:
        lines.append(" | ".join(row))
    if len(lines) == 1 and table.raw_text_fallback:
        lines.append(table.raw_text_fallback)
    return "\n".join(lines)


def _format_figure_unit(fig: FigureReference) -> str:
    if fig.caption:
        return f"[FIGURE ({fig.kind}, p.{fig.page_number}): {fig.caption}]"
    return f"[FIGURE ({fig.kind}, p.{fig.page_number}) — visual diagram without caption]"


def _build_section_paths(
    sections: tuple[SourceSection, ...],
) -> dict[str, tuple[str, ...]]:
    by_id = {sec.section_id: sec for sec in sections}
    paths: dict[str, tuple[str, ...]] = {}
    for sec in sections:
        chain: list[str] = []
        cur: SourceSection | None = sec
        seen: set[str] = set()
        while cur is not None and cur.section_id not in seen:
            seen.add(cur.section_id)
            if cur.title:
                chain.append(cur.title)
            cur = (
                by_id.get(cur.parent_section_id)
                if cur.parent_section_id is not None
                else None
            )
        paths[sec.section_id] = tuple(reversed(chain))
    return paths


def _index_evidence(
    evidence_refs: tuple[EvidenceReference, ...],
) -> tuple[dict[str, list[str]], dict[tuple[str | None, int], list[str]]]:
    by_block: dict[str, list[str]] = {}
    by_section_page: dict[tuple[str | None, int], list[str]] = {}
    for ev in evidence_refs:
        if ev.block_id is not None:
            by_block.setdefault(ev.block_id, []).append(ev.evidence_id)
        else:
            by_section_page.setdefault((ev.section_id, ev.page_number), []).append(
                ev.evidence_id
            )
    return by_block, by_section_page


def _section_units(
    sec: SourceSection,
    *,
    blocks_by_id: dict[str, TextBlock],
    tables_by_id: dict[str, TableBlock],
    figures_by_id: dict[str, FigureReference],
    formulas_by_section: dict[str, list[FormulaReference]],
    ev_by_block: dict[str, list[str]],
    ev_by_sec_page: dict[tuple[str | None, int], list[str]],
) -> list[_AtomicUnit]:
    units: list[_AtomicUnit] = []
    sec_formulas = formulas_by_section.get(sec.section_id, [])

    # Collect blocks belonging to this section
    for bid in sec.block_ids:
        blk = blocks_by_id.get(bid)
        if blk is None:
            continue
        ev_ids = tuple(ev_by_block.get(bid, ()))
        ev_tag = f" [ev:{','.join(ev_ids)}]" if ev_ids else ""
        if blk.kind == "code":
            rendered = f"[CODE p.{blk.page_number}{ev_tag}]\n{blk.normalized_text}"
            is_code = True
        elif blk.kind == "heading":
            rendered = f"{'#' * min(4, blk.heading_level or 2)} {blk.normalized_text}{ev_tag}"
            is_code = False
        else:
            rendered = f"{blk.normalized_text}{ev_tag}"
            is_code = False

        matching_frms = tuple(
            f.formula_id
            for f in sec_formulas
            if (f.raw_representation and f.raw_representation in blk.normalized_text)
            or (f.latex and f.latex in blk.normalized_text)
        )
        units.append(
            _AtomicUnit(
                unit_kind=blk.kind,
                rendered_text=rendered,
                page_number=blk.page_number,
                reading_order=blk.reading_order,
                section_id=sec.section_id,
                block_ids=(bid,),
                formula_ids=matching_frms,
                evidence_ids=ev_ids,
                is_code=is_code,
                is_formula=bool(matching_frms),
                split_allowed=(blk.kind not in {"code", "heading"}),
            )
        )

    for tid in sec.table_ids:
        tbl = tables_by_id.get(tid)
        if tbl is None:
            continue
        ev_ids = tuple(ev_by_sec_page.get((sec.section_id, tbl.page_number), ()))
        ev_tag = f" [ev:{','.join(ev_ids)}]" if ev_ids else ""
        units.append(
            _AtomicUnit(
                unit_kind="table",
                rendered_text=f"{_format_table_unit(tbl)}{ev_tag}",
                page_number=tbl.page_number,
                reading_order=tbl.reading_order,
                section_id=sec.section_id,
                table_ids=(tid,),
                evidence_ids=ev_ids,
                is_table=True,
                split_allowed=False,
            )
        )

    for fid in sec.figure_ids:
        fig = figures_by_id.get(fid)
        if fig is None:
            continue
        # Only associate figure metadata captions/references rather than inventing visuals.
        units.append(
            _AtomicUnit(
                unit_kind="figure_caption",
                rendered_text=_format_figure_unit(fig),
                page_number=fig.page_number,
                reading_order=fig.reading_order,
                section_id=sec.section_id,
                figure_ids=(fid,),
                is_figure=True,
                split_allowed=False,
            )
        )

    units.sort(key=lambda u: (u.page_number, u.reading_order))
    return units


def _split_oversized_text_unit(
    unit: _AtomicUnit,
    *,
    max_chars: int,
    overlap_chars: int,
) -> list[_AtomicUnit]:
    text = unit.rendered_text
    if len(text) <= max_chars:
        return [unit]

    # For oversized tables or code blocks, never split mid-line if avoidable and omit duplicate overlap.
    eff_overlap = overlap_chars if unit.split_allowed else 0
    parts: list[_AtomicUnit] = []
    cursor = 0
    while cursor < len(text):
        end = min(len(text), cursor + max_chars)
        if end < len(text):
            if unit.split_allowed:
                break_pos = max(
                    text.rfind("\n", cursor + max_chars // 2, end),
                    text.rfind(". ", cursor + max_chars // 2, end),
                )
            else:
                break_pos = text.rfind("\n", cursor + max_chars // 2, end)
            if break_pos > cursor:
                end = break_pos + (1 if text[break_pos] == "." else 0)
        piece = text[cursor:end].strip()
        if piece:
            parts.append(
                _AtomicUnit(
                    unit_kind=unit.unit_kind,
                    rendered_text=piece,
                    page_number=unit.page_number,
                    reading_order=unit.reading_order,
                    section_id=unit.section_id,
                    block_ids=unit.block_ids,
                    table_ids=unit.table_ids,
                    figure_ids=unit.figure_ids,
                    formula_ids=unit.formula_ids,
                    evidence_ids=unit.evidence_ids,
                    is_code=unit.is_code,
                    is_table=unit.is_table,
                    is_formula=unit.is_formula,
                    is_figure=unit.is_figure,
                    split_allowed=False,
                )
            )
        if end >= len(text):
            break
        cursor = max(end - eff_overlap, cursor + 1)
    return parts


def _collect_chunk_metadata(
    units: list[_AtomicUnit],
) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    blocks: dict[str, None] = {}
    tables: dict[str, None] = {}
    figures: dict[str, None] = {}
    formulas: dict[str, None] = {}
    evs: dict[str, None] = {}
    for u in units:
        for b in u.block_ids:
            blocks[b] = None
        for t in u.table_ids:
            tables[t] = None
        for f in u.figure_ids:
            figures[f] = None
        for m in u.formula_ids:
            formulas[m] = None
        for e in u.evidence_ids:
            evs[e] = None
    return (
        tuple(blocks),
        tuple(tables),
        tuple(figures),
        tuple(formulas),
        tuple(evs),
    )


def _pack_units(
    *,
    sec: SourceSection,
    sec_path: tuple[str, ...],
    units: list[_AtomicUnit],
    config: ChunkingConfig,
    chunk_counter_start: int,
) -> tuple[list[DocumentChunk], int]:
    """Assemble section units into bounded chunks with limited contextual overlap."""
    if not units:
        return [], chunk_counter_start

    max_tokens = config.chunk_max_tokens
    max_unit_chars = max(400, int(max_tokens * config.chars_per_token * 0.85))
    overlap_chars = int(config.overlap_tokens * config.chars_per_token)

    normalized_units: list[_AtomicUnit] = []
    for unit in units:
        normalized_units.extend(
            _split_oversized_text_unit(
                unit, max_chars=max_unit_chars, overlap_chars=overlap_chars
            )
        )

    header_line = (
        f"[SECTION: {' > '.join(sec_path)}]"
        if sec_path
        else (f"[SECTION: {sec.title}]" if sec.title else f"[SECTION {sec.section_id}]")
    )
    header_tokens = estimate_tokens(header_line, chars_per_token=config.chars_per_token)
    body_budget = max(120, max_tokens - header_tokens - config.overlap_tokens)

    buckets: list[list[_AtomicUnit]] = []
    current_bucket: list[_AtomicUnit] = []
    current_tokens = 0
    for unit in normalized_units:
        utok = estimate_tokens(unit.rendered_text, chars_per_token=config.chars_per_token)
        if current_bucket and (current_tokens + utok) > body_budget:
            buckets.append(current_bucket)
            current_bucket = [unit]
            current_tokens = utok
        else:
            current_bucket.append(unit)
            current_tokens += utok
    if current_bucket:
        buckets.append(current_bucket)

    chunks: list[DocumentChunk] = []
    idx = chunk_counter_start
    prev_tail = ""
    for b_pos, bucket in enumerate(buckets):
        overlap_prefix = prev_tail if (b_pos > 0 and prev_tail) else ""
        parts = [header_line]
        if overlap_prefix:
            parts.append(f"[CONTEXT OVERLAP: {overlap_prefix}]")
        parts.extend(u.rendered_text for u in bucket)
        rendered = "\n\n".join(parts)

        block_ids, table_ids, figure_ids, formula_ids, ev_ids = _collect_chunk_metadata(bucket)
        chunks.append(
            DocumentChunk(
                chunk_id=f"chk-{idx + 1}",
                chunk_index=idx,
                section_id=sec.section_id,
                section_path=sec_path,
                heading_title=sec.title,
                page_start=min(u.page_number for u in bucket),
                page_end=max(u.page_number for u in bucket),
                estimated_tokens=estimate_tokens(
                    rendered, chars_per_token=config.chars_per_token
                ),
                char_count=len(rendered),
                overlap_prefix=overlap_prefix,
                rendered_text=rendered,
                block_ids=block_ids,
                table_ids=table_ids,
                figure_ids=figure_ids,
                formula_ids=formula_ids,
                evidence_ids=ev_ids,
                contains_code=any(u.is_code for u in bucket),
                contains_table=any(u.is_table for u in bucket),
                contains_formula=any(u.is_formula for u in bucket),
                contains_figure=any(u.is_figure for u in bucket),
            )
        )
        idx += 1
        if overlap_chars > 0:
            last_text = bucket[-1].rendered_text
            if not bucket[-1].is_table and not bucket[-1].is_code and len(last_text) > overlap_chars:
                start_pos = len(last_text) - overlap_chars
                ws_pos = last_text.find(" ", start_pos)
                tail = (last_text[ws_pos + 1 :] if ws_pos != -1 else last_text[start_pos:]).strip()
                if tail.startswith(("]", "ev:")):
                    tail = ""
                prev_tail = tail
            elif not bucket[-1].is_table and not bucket[-1].is_code:
                prev_tail = last_text.strip()
            else:
                prev_tail = ""
    return chunks, idx


def chunk_extracted_document(
    document: ExtractedDocument,
    *,
    config: ChunkingConfig | None = None,
    force_mode: ChunkingMode | None = None,
) -> AdaptiveChunkPlan:
    """Select single-pass vs hierarchical section-level chunking deterministically."""
    started_ns = time.monotonic_ns()
    cfg = config or ChunkingConfig()
    sections = document.sections
    section_paths = _build_section_paths(sections)
    blocks_by_id = {b.block_id: b for b in document.text_blocks}
    tables_by_id = {t.table_id: t for t in document.tables}
    figures_by_id = {f.figure_id: f for f in document.figures}
    formulas_by_sec: dict[str, list[FormulaReference]] = {}
    for f in document.formulas:
        formulas_by_sec.setdefault(f.section_id or "", []).append(f)
    ev_by_block, ev_by_sec_page = _index_evidence(document.evidence_references)

    total_est = estimate_tokens(
        document.normalized_text, chars_per_token=cfg.chars_per_token
    )
    # Collect section units in exact reading order, assigning orphan units if sections omitted.
    all_section_units: list[tuple[SourceSection, list[_AtomicUnit]]] = []
    covered_blocks: set[str] = set()
    covered_tables: set[str] = set()
    covered_figures: set[str] = set()

    for sec in sorted(sections, key=lambda s: (s.page_start, s.reading_order)):
        units = _section_units(
            sec,
            blocks_by_id=blocks_by_id,
            tables_by_id=tables_by_id,
            figures_by_id=figures_by_id,
            formulas_by_section=formulas_by_sec,
            ev_by_block=ev_by_block,
            ev_by_sec_page=ev_by_sec_page,
        )
        for u in units:
            covered_blocks.update(u.block_ids)
            covered_tables.update(u.table_ids)
            covered_figures.update(u.figure_ids)
        if units:
            all_section_units.append((sec, units))

    # Include any blocks/tables/figures not attached to a SourceSection cleanly
    orphan_units: list[_AtomicUnit] = []
    for blk in document.text_blocks:
        if blk.block_id not in covered_blocks:
            ev_ids = tuple(ev_by_block.get(blk.block_id, ()))
            ev_tag = f" [ev:{','.join(ev_ids)}]" if ev_ids else ""
            orphan_units.append(
                _AtomicUnit(
                    unit_kind=blk.kind,
                    rendered_text=f"{blk.normalized_text}{ev_tag}",
                    page_number=blk.page_number,
                    reading_order=blk.reading_order,
                    section_id=blk.section_id or "sec-1",
                    block_ids=(blk.block_id,),
                    evidence_ids=ev_ids,
                    is_code=(blk.kind == "code"),
                )
            )
    for tbl in document.tables:
        if tbl.table_id not in covered_tables:
            ev_ids = tuple(ev_by_sec_page.get((tbl.section_id, tbl.page_number), ()))
            ev_tag = f" [ev:{','.join(ev_ids)}]" if ev_ids else ""
            orphan_units.append(
                _AtomicUnit(
                    unit_kind="table",
                    rendered_text=f"{_format_table_unit(tbl)}{ev_tag}",
                    page_number=tbl.page_number,
                    reading_order=tbl.reading_order,
                    section_id=tbl.section_id or "sec-1",
                    table_ids=(tbl.table_id,),
                    evidence_ids=ev_ids,
                    is_table=True,
                    split_allowed=False,
                )
            )
    if orphan_units:
        fallback_sec = (
            sections[0]
            if sections
            else SourceSection(
                section_id="sec-1",
                title=document.title,
                level=1,
                reading_order=0,
                page_start=1,
                page_end=max(1, len(document.pages)),
            )
        )
        all_section_units.append((fallback_sec, orphan_units))

    mode: ChunkingMode = (
        force_mode
        if force_mode is not None
        else (
            "single_pass"
            if total_est <= cfg.single_pass_max_tokens
            else "hierarchical"
        )
    )

    chunks: list[DocumentChunk] = []
    if mode == "single_pass" and all_section_units:
        merged_parts: list[str] = []
        single_units: list[_AtomicUnit] = []
        p_start = min(u.page_number for _, us in all_section_units for u in us)
        p_end = max(u.page_number for _, us in all_section_units for u in us)
        has_code = has_table = has_formula = has_figure = False
        for sec, us in all_section_units:
            path = section_paths.get(sec.section_id, ())
            if path:
                merged_parts.append(f"[SECTION: {' > '.join(path)}]")
            elif sec.title:
                merged_parts.append(f"[SECTION: {sec.title}]")
            for u in us:
                merged_parts.append(u.rendered_text)
                single_units.append(u)
                has_code = has_code or u.is_code
                has_table = has_table or u.is_table
                has_formula = has_formula or u.is_formula
                has_figure = has_figure or u.is_figure
        block_ids, table_ids, figure_ids, formula_ids, ev_ids = _collect_chunk_metadata(single_units)
        rendered_single = "\n\n".join(merged_parts)
        chunks.append(
            DocumentChunk(
                chunk_id="chk-1",
                chunk_index=0,
                section_id=all_section_units[0][0].section_id,
                section_path=section_paths.get(all_section_units[0][0].section_id, ()),
                heading_title=document.title,
                page_start=p_start,
                page_end=p_end,
                estimated_tokens=estimate_tokens(
                    rendered_single, chars_per_token=cfg.chars_per_token
                ),
                char_count=len(rendered_single),
                rendered_text=rendered_single,
                block_ids=block_ids,
                table_ids=table_ids,
                figure_ids=figure_ids,
                formula_ids=formula_ids,
                evidence_ids=ev_ids,
                contains_code=has_code,
                contains_table=has_table,
                contains_formula=has_formula,
                contains_figure=has_figure,
            )
        )
    else:
        # Hierarchical: pack small sibling sections if well below min_tokens, split only oversized sections.
        pending_sec: SourceSection | None = None
        pending_units: list[_AtomicUnit] = []
        pending_tokens = 0
        chunk_idx = 0

        def _flush_pending() -> None:
            nonlocal pending_sec, pending_units, pending_tokens, chunk_idx
            if pending_sec is None or not pending_units:
                pending_sec = None
                pending_units = []
                pending_tokens = 0
                return
            sec_chunks, chunk_idx = _pack_units(
                sec=pending_sec,
                sec_path=section_paths.get(pending_sec.section_id, ()),
                units=pending_units,
                config=cfg,
                chunk_counter_start=chunk_idx,
            )
            chunks.extend(sec_chunks)
            pending_sec = None
            pending_units = []
            pending_tokens = 0

        for sec, units in all_section_units:
            sec_tokens = sum(
                estimate_tokens(u.rendered_text, chars_per_token=cfg.chars_per_token)
                for u in units
            )
            if (
                pending_sec is not None
                and sec.level >= pending_sec.level
                and (pending_tokens + sec_tokens) <= cfg.chunk_max_tokens
                and (pending_tokens < cfg.chunk_min_tokens or sec_tokens < cfg.chunk_min_tokens)
            ):
                pending_units.extend(units)
                pending_tokens += sec_tokens
            else:
                _flush_pending()
                pending_sec = sec
                pending_units = list(units)
                pending_tokens = sec_tokens
        _flush_pending()

        if len(chunks) > cfg.max_chunks:
            chunks = chunks[: cfg.max_chunks]

    total_ev_ids = {ev.evidence_id for ev in document.evidence_references}
    covered_ev_ids = {eid for ch in chunks for eid in ch.evidence_ids}
    uncovered = tuple(sorted(total_ev_ids - covered_ev_ids))
    coverage = (
        round(len(covered_ev_ids & total_ev_ids) / len(total_ev_ids), 4)
        if total_ev_ids
        else (1.0 if chunks else 0.0)
    )
    total_chunk_blocks = sum(len(ch.block_ids) for ch in chunks)
    unique_chunk_blocks = len({bid for ch in chunks for bid in ch.block_ids})
    dup_ratio = (
        round(
            max(0, total_chunk_blocks - unique_chunk_blocks) / total_chunk_blocks, 4
        )
        if total_chunk_blocks > 0
        else 0.0
    )
    elapsed_ms = round((time.monotonic_ns() - started_ns) / 1_000_000.0, 3)

    return AdaptiveChunkPlan(
        mode=mode,
        source_id=document.source_id,
        version_id=document.version_id,
        source_sha256=document.source_sha256,
        total_estimated_tokens=total_est,
        budget_tokens=cfg.single_pass_max_tokens
        if mode == "single_pass"
        else cfg.chunk_max_tokens,
        coverage_ratio=coverage,
        duplicate_ratio=dup_ratio,
        chunking_ms=elapsed_ms,
        chunks=tuple(chunks),
        uncovered_evidence_ids=uncovered,
    )
