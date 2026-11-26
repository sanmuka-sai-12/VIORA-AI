"""Regression cases for conservative extraction and publication contracts."""

from uuid import uuid4

import pytest
from viora.ai.openai_adapter import _compact_json_schema
from viora.ai.public import FakeAIGateway
from viora.contracts import TenantContext
from viora.knowledge.extractor import ConceptExtractor
from viora.knowledge.schemas import (
    ExtractedKnowledgeGraph,
    RawConceptCandidate,
    RawConceptExtractionBatch,
    RawRelationshipCandidate,
)
from viora.sources.public import (
    AdaptiveDocumentExtractor,
    DerivedExtractionRecord,
    clamp_source_limits,
)


@pytest.fixture
def derived() -> DerivedExtractionRecord:
    document = AdaptiveDocumentExtractor().extract(
        tenant_id=uuid4(), source_id=uuid4(), version_id=uuid4(), source_version=1,
        source_sha256="a" * 64, input_format="typed_notes", title="Queues",
        raw_bytes=b"A queue holds 10 items.\n\nDynamic arrays resize storage. Circular queues reuse storage.",
        limits=clamp_source_limits(),
    )
    return DerivedExtractionRecord(
        extraction_id=uuid4(), tenant_id=document.tenant_id, source_id=document.source_id,
        version_id=document.version_id, source_sha256=document.source_sha256,
        extraction_version=1, document=document, created_at=document.created_at,
    )


def extract(derived: DerivedExtractionRecord, batch: RawConceptExtractionBatch) -> ExtractedKnowledgeGraph:
    gateway = FakeAIGateway(structured_payloads={RawConceptExtractionBatch: batch})
    return ConceptExtractor(gateway).extract_concepts(
        context=TenantContext(actor_id=uuid4(), tenant_id=derived.tenant_id, roles=frozenset()),
        derived=derived,
    ).knowledge


def test_missing_relation_type_is_not_fabricated(derived: DerivedExtractionRecord) -> None:
    relation = RawRelationshipCandidate(source_concept="Dynamic arrays", target_concept="Circular queues")
    assert relation.relation_type is None
    graph = extract(derived, RawConceptExtractionBatch(
        concepts=[RawConceptCandidate(name=n, canonical_summary=n) for n in ("Dynamic arrays", "Circular queues")],
        relationships=[relation],
    ))
    assert not graph.relationships
    assert graph.validation.required_fields_complete is False
    assert graph.validation.semantic_support == "not_verified"


@pytest.mark.parametrize("claim", ["A queue holds 100 items.", "10 items hold a queue.", "A queue holds -10 items."])
def test_word_overlap_cannot_support_changed_claim(derived: DerivedExtractionRecord, claim: str) -> None:
    graph = extract(derived, RawConceptExtractionBatch(concepts=[RawConceptCandidate(
        name="Queue", canonical_summary=claim, grounding="source_explicit",
        evidence_ids=[derived.document.evidence_references[0].evidence_id],
    )]))
    assert graph.concepts[0].grounding == "uncertain"
    assert graph.concepts[0].needs_review


def test_duplicate_cannot_upgrade_unsupported_summary(derived: DerivedExtractionRecord) -> None:
    graph = extract(derived, RawConceptExtractionBatch(concepts=[
        RawConceptCandidate(name="Queue", canonical_summary="A queue holds 100 items."),
        RawConceptCandidate(name="Queue", canonical_summary="A queue holds 10 items.",
                            grounding="source_explicit", evidence_ids=[derived.document.evidence_references[0].evidence_id]),
    ]))
    assert graph.concepts[0].canonical_summary == "A queue holds 100 items."
    assert graph.concepts[0].grounding == "uncertain"
    assert graph.validation.required_fields_complete is False


def test_wire_schema_requires_provenance_after_compaction() -> None:
    schema = _compact_json_schema(RawConceptExtractionBatch.model_json_schema())
    for name in ("RawConceptCandidate", "RawRelationshipCandidate"):
        assert {"grounding", "evidence_ids"} <= set(schema["$defs"][name]["required"])
    assert "relation_type" in schema["$defs"]["RawRelationshipCandidate"]["required"]


def test_legacy_records_remain_readable_with_unknown_completeness(derived: DerivedExtractionRecord) -> None:
    graph = extract(derived, RawConceptExtractionBatch())
    raw = graph.model_dump(mode="json")
    raw["validation"].pop("required_fields_complete", None)
    restored = ExtractedKnowledgeGraph.model_validate(raw)
    assert restored.validation.required_fields_complete is None
