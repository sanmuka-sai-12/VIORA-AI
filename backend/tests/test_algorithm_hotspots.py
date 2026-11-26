"""Deterministic regression fixtures for component-local and table-boundary scans."""

from datetime import UTC, datetime
from uuid import UUID

import pytest
from viora.knowledge.curriculum import analyze_prerequisites_and_order
from viora.knowledge.schemas import (
    ConceptCandidate,
    ConceptRelationshipCandidate,
    ExtractedKnowledgeGraph,
    KnowledgeValidationReport,
)
from viora.sources.spatial_layout import _extract_spatial_tables


def make_disjoint_cycles(count: int) -> ExtractedKnowledgeGraph:
    """Create independent two-node cycles with uniquely weakest reverse edges."""
    concepts = tuple(
        ConceptCandidate(
            concept_id=f"c{i}", name=f"Concept {i}", normalized_name=f"concept {i}",
            canonical_summary="Fixture concept", grounding="source_supported",
        )
        for i in range(count * 2)
    )
    relationships = tuple(
        ConceptRelationshipCandidate(
            relationship_id=f"e{i}", source_concept_id=f"c{i}",
            source_concept_name=f"Concept {i}", relation_type="prerequisite_of",
            target_concept_id=f"c{i ^ 1}", target_concept_name=f"Concept {i ^ 1}",
            grounding="uncertain" if i % 2 else "source_supported",
        )
        for i in range(count * 2)
    )
    return ExtractedKnowledgeGraph(
        source_id=UUID(int=1), version_id=UUID(int=2), derived_extraction_id=UUID(int=3),
        source_sha256="0" * 64, input_format="plain_text", title="Disjoint cycles",
        extraction_mode="single_pass", concepts=concepts, relationships=relationships,
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        validation=KnowledgeValidationReport(
            total_concepts=len(concepts), source_supported_count=len(concepts),
            source_synthesized_count=0, learning_request_count=0, inferred_count=0,
            uncertain_count=0, unsupported_count=0, invalid_evidence_refs_rejected=0,
            duplicate_merges_count=0, ambiguous_senses_retained=0,
        ),
    )


def test_disjoint_prerequisite_cycles_preserve_break_order_and_depth() -> None:
    analysis = analyze_prerequisites_and_order(make_disjoint_cycles(40))
    assert [cycle.broken_edge_id for cycle in analysis.cycles] == [f"e{i}" for i in range(1, 80, 2)]
    assert [step.concept_id for step in analysis.teaching_order] == [f"c{i}" for i in range(80)]
    assert [step.depth_level for step in analysis.teaching_order] == [i % 2 for i in range(80)]
    assert all(step.blocked_by_cycle for step in analysis.teaching_order)


def test_component_split_rechecks_only_remaining_active_edges() -> None:
    graph = make_disjoint_cycles(2)
    edges = list(graph.relationships)
    edges[2] = edges[2].model_copy(update={
        "source_concept_id": "c1", "source_concept_name": "Concept 1",
        "target_concept_id": "c2", "target_concept_name": "Concept 2",
    })
    edges[3] = edges[3].model_copy(update={
        "source_concept_id": "c2", "source_concept_name": "Concept 2",
        "target_concept_id": "c1", "target_concept_name": "Concept 1",
    })
    graph = graph.model_copy(update={"relationships": tuple(edges)})
    analysis = analyze_prerequisites_and_order(graph)
    assert [cycle.broken_edge_id for cycle in analysis.cycles] == ["e3", "e1"]
    assert [cycle.concept_ids for cycle in analysis.cycles] == [("c0", "c1", "c2"), ("c0", "c1")]
    assert [step.depth_level for step in analysis.teaching_order] == [0, 1, 2, 0]


class _Table:
    bbox = (0.0, 0.0, 200.0, 200.0)

    def __init__(self, rows: list[list[str]]) -> None:
        self._rows = rows

    def extract(self) -> list[list[str]]:
        return self._rows


class _Page:
    page_number = 1

    def __init__(self, table: _Table) -> None:
        self.table = table

    def find_tables(self, *, table_settings: dict[str, str | int]) -> list[_Table]:
        return [self.table]


@pytest.mark.parametrize("prefix_count", [0, 1, 2000])
def test_borderless_table_trims_caption_prefix_and_sparse_suffix(prefix_count: int) -> None:
    rows = [["Table 1: Caption", "", ""] for _ in range(prefix_count)]
    rows += [["Alpha", "Beta", "Gamma"], ["1", "2", "3"], ["4", "5", "6"]]
    rows += [["Trailing prose", "", ""], ["two", "cells", ""]]
    tables, excluded, warnings = _extract_spatial_tables(_Page(_Table(rows)), 300, 300)
    assert len(tables) == 1
    assert tables[0].headers == ("Alpha", "Beta", "Gamma")
    assert tables[0].rows == (("1", "2", "3"), ("4", "5", "6"))
    assert excluded == []
    assert len(warnings) == 1


def test_borderless_table_with_only_captions_is_rejected() -> None:
    rows = [["Table 1", "Caption", ""] for _ in range(10)]
    assert _extract_spatial_tables(_Page(_Table(rows)), 300, 300) == ([], [], [])
