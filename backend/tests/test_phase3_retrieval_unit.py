"""Unit tests for Phase 3 embeddings, fingerprinting, bounded graph enrichment, and context packing."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import ValidationError
from viora.config import Settings
from viora.knowledge.embeddings import (
    DeterministicFakeEmbedder,
    LocalOnnxEmbedder,
    _WordPieceTokenizer,
    build_embedder_from_settings,
)
from viora.knowledge.indexing import (
    compute_chunk_content_sha256,
    compute_embedding_fingerprint,
)
from viora.knowledge.retrieval import (
    HybridKnowledgeRetriever,
    _CandidateRow,
    _enrich_with_concept_graph,
)
from viora.knowledge.schemas import (
    ConceptCandidate,
    ConceptDefinition,
    ConceptEvidenceBinding,
    ConceptRelationshipCandidate,
    DocumentChunk,
    ExtractedKnowledgeGraph,
    KnowledgeRetrieveRequest,
    KnowledgeValidationReport,
    PrerequisiteCandidate,
)


def _sample_chunk(chunk_id: str, text: str, *, sec_id: str = "sec-1", idx: int = 0) -> DocumentChunk:
    return DocumentChunk(
        chunk_id=chunk_id,
        chunk_index=idx,
        section_id=sec_id,
        section_path=("Kinematics",),
        heading_title="Kinematics",
        page_start=1,
        page_end=1,
        estimated_tokens=max(1, len(text) // 4),
        char_count=len(text),
        rendered_text=text,
        evidence_ids=("ev-1",),
    )


def test_fake_embedder_deterministic_and_normalized() -> None:
    emb = DeterministicFakeEmbedder()
    v1 = emb.embed_texts(["Newton second law force equals mass times acceleration"])[0]
    v2 = emb.embed_texts(["Newton second law force equals mass times acceleration"])[0]
    v3 = emb.embed_texts(["Binary search logarithmic time complexity"])[0]
    assert len(v1) == 384
    assert v1 == v2
    assert abs(sum(x * x for x in v1) - 1.0) < 1e-6
    # Same-domain lexical overlap yields higher cosine than unrelated text
    v_rel = emb.embed_texts(["Force and acceleration in Newton mechanics"])[0]
    sim_rel = sum(a * b for a, b in zip(v1, v_rel, strict=True))
    sim_unrel = sum(a * b for a, b in zip(v1, v3, strict=True))
    assert sim_rel > sim_unrel


def test_embedding_fingerprints_isolate_content_and_model_versions() -> None:
    c1 = _sample_chunk("chk-1", "Velocity is the derivative of displacement.")
    c2 = _sample_chunk("chk-1", "Acceleration is the derivative of velocity.")
    h1 = compute_chunk_content_sha256(c1)
    h2 = compute_chunk_content_sha256(c2)
    assert h1 != h2

    fp_a = compute_embedding_fingerprint(
        content_sha256=h1,
        embedding_model="fake-hash-embedder",
        embedding_model_version="fake:1.0",
        embedding_dim=384,
    )
    fp_b = compute_embedding_fingerprint(
        content_sha256=h1,
        embedding_model="BAAI/bge-small-en-v1.5",
        embedding_model_version="int8-onnx:1.0",
        embedding_dim=384,
    )
    assert fp_a != fp_b


def test_wordpiece_tokenizer_subwords_without_external_deps(tmp_path) -> None:
    vocab_file = tmp_path / "vocab.txt"
    vocab_file.write_text(
        "[PAD]\n[UNK]\n[CLS]\n[SEP]\nkin\n##ematics\nmotion\n",
        encoding="utf-8",
    )
    tok = _WordPieceTokenizer(vocab_file, max_length=16)
    ids, masks = tok.encode_batch(["Kinematics motion!"])
    assert ids[0][0] == tok.cls_id
    assert tok.vocab["kin"] in ids[0]
    assert tok.vocab["##ematics"] in ids[0]
    assert tok.vocab["motion"] in ids[0]
    assert len(ids[0]) == len(masks[0])


def test_graph_enrichment_is_cycle_safe_and_hop_bounded() -> None:
    src_id, ver_id, ext_id, ce_id = uuid4(), uuid4(), uuid4(), uuid4()
    ten_id = uuid4()

    def _ev(eid: str) -> ConceptEvidenceBinding:
        return ConceptEvidenceBinding(
            evidence_id=eid,
            source_id=src_id,
            version_id=ver_id,
            source_sha256="a" * 64,
            extraction_id=ext_id,
            page_number=1,
            section_id="sec-1",
            block_id="blk-1",
            verified=True,
            excerpt="Sample excerpt.",
        )

    kg = ExtractedKnowledgeGraph(
        source_id=src_id,
        version_id=ver_id,
        derived_extraction_id=ext_id,
        source_sha256="a" * 64,
        input_format="typed_notes",
        title="Kinematics",
        extraction_mode="single_pass",
        concepts=(
            ConceptCandidate(
                concept_id="c-1",
                name="Displacement",
                normalized_name="displacement",
                canonical_summary="Vector change in position.",
                definitions=(ConceptDefinition(definition="dx = x2 - x1", grounding="source_supported", evidence_ids=("ev-1",)),),
                grounding="source_supported",
                evidence_bindings=(_ev("ev-1"),),
            ),
            ConceptCandidate(
                concept_id="c-2",
                name="Velocity",
                normalized_name="velocity",
                canonical_summary="Rate of change of displacement.",
                prerequisites=(PrerequisiteCandidate(prerequisite_name="Displacement", grounding="source_supported"),),
                grounding="source_supported",
                evidence_bindings=(_ev("ev-2"),),
                chunk_ids=("chk-1",),
            ),
            ConceptCandidate(
                concept_id="c-3",
                name="Acceleration",
                normalized_name="acceleration",
                canonical_summary="Rate of change of velocity.",
                grounding="source_supported",
                evidence_bindings=(_ev("ev-3"),),
            ),
            ConceptCandidate(
                concept_id="c-4",
                name="Jerk",
                normalized_name="jerk",
                canonical_summary="Rate of change of acceleration.",
                grounding="inferred",
                evidence_bindings=(),
            ),
        ),
        relationships=(
            ConceptRelationshipCandidate(
                relationship_id="r-1",
                source_concept_id="c-1",
                source_concept_name="Displacement",
                relation_type="prerequisite_of",
                target_concept_id="c-2",
                target_concept_name="Velocity",
                grounding="source_supported",
                evidence_bindings=(_ev("ev-1"),),
            ),
            ConceptRelationshipCandidate(
                relationship_id="r-2",
                source_concept_id="c-2",
                source_concept_name="Velocity",
                relation_type="prerequisite_of",
                target_concept_id="c-3",
                target_concept_name="Acceleration",
                grounding="source_supported",
                evidence_bindings=(_ev("ev-2"),),
            ),
            # Cyclic back-edge Acceleration -> Displacement + extra hop c-3 -> c-4
            ConceptRelationshipCandidate(
                relationship_id="r-cycle",
                source_concept_id="c-3",
                source_concept_name="Acceleration",
                relation_type="contrasts_with",
                target_concept_id="c-1",
                target_concept_name="Displacement",
                grounding="inferred",
            ),
            ConceptRelationshipCandidate(
                relationship_id="r-3",
                source_concept_id="c-3",
                source_concept_name="Acceleration",
                relation_type="prerequisite_of",
                target_concept_id="c-4",
                target_concept_name="Jerk",
                grounding="inferred",
            ),
        ),
        validation=KnowledgeValidationReport(
            total_concepts=4,
            source_supported_count=3,
            source_synthesized_count=0,
            learning_request_count=0,
            inferred_count=1,
            uncertain_count=0,
            unsupported_count=0,
            invalid_evidence_refs_rejected=0,
            duplicate_merges_count=0,
            ambiguous_senses_retained=0,
        ),
        created_at=datetime.now(UTC),
    )

    class _FakeSession:
        def execute(self, _stmt):
            class _Res:
                def all(self):
                    return [(ce_id, kg.model_dump(mode="json"))]

            return _Res()

    selected = [
        _CandidateRow(
            chunk_record_id=uuid4(),
            source_id=src_id,
            version_id=ver_id,
            derived_extraction_id=ext_id,
            concept_extraction_id=ce_id,
            index_version=1,
            chunk_id="chk-1",
            chunk_index=0,
            section_id="sec-1",
            section_path=("Kinematics",),
            heading_title="Velocity",
            page_start=1,
            page_end=1,
            evidence_ids=("ev-2",),
            rendered_text="Velocity is the rate of change of displacement.",
            content_sha256="b" * 64,
            estimated_tokens=12,
            origin_kind="teacher_resource",
            visibility="offering_published",
        )
    ]

    # max_hops=1 reaches Velocity (0), Displacement (1), Acceleration (1), stops before Jerk (2)
    concepts_1, max_hop_1, cycles_1 = _enrich_with_concept_graph(
        _FakeSession(),  # type: ignore[arg-type]
        tenant_id=ten_id,
        query="What is velocity?",
        selected=selected,
        max_hops=1,
    )
    names_1 = {c.name for c in concepts_1}
    assert {"Velocity", "Displacement", "Acceleration"} == names_1
    assert "Jerk" not in names_1
    assert max_hop_1 == 1
    assert cycles_1 == 0

    # max_hops=2 reaches Jerk (2) and encounters back-edges/cycles cleanly via visited set
    concepts_2, max_hop_2, cycles_2 = _enrich_with_concept_graph(
        _FakeSession(),  # type: ignore[arg-type]
        tenant_id=ten_id,
        query="What is velocity?",
        selected=selected,
        max_hops=2,
    )
    assert "Jerk" in {c.name for c in concepts_2}
    assert max_hop_2 == 2
    assert cycles_2 >= 1

    # Token budget bounds context construction strictly
    final_chunks, ctx_text, ctx_tokens = HybridKnowledgeRetriever._build_bounded_context(
        selected=[selected[0], _CandidateRow(**{**selected[0].__dict__, "chunk_record_id": uuid4(), "chunk_id": "chk-2", "rendered_text": "word " * 200, "estimated_tokens": 250})],
        parent_context_map={selected[0].chunk_record_id: "Section [Kinematics]"},
        graph_concepts=concepts_1,
        max_context_tokens=128,
    )
    assert len(final_chunks) == 1
    assert ctx_tokens <= 128
    assert "Velocity" in ctx_text


def test_retrieval_defaults_hybrid_local_onnx_and_test_only_fake(tmp_path: Path) -> None:
    req = KnowledgeRetrieveRequest(query="binary search invariant")
    assert req.mode == "hybrid"

    db_url = "postgresql+psycopg://viora:pw@127.0.0.1:5432/viora_test"
    default_cfg = Settings(database_url=db_url)  # type: ignore[arg-type]
    assert default_cfg.embedding_provider == "local_onnx"
    assert default_cfg.local_embedding_model_dir == "./storage/models/bge-small-en-v1.5-int8"

    for env_name in ("development", "production"):
        with pytest.raises(ValidationError):
            Settings(
                database_url=db_url,  # type: ignore[arg-type]
                environment=env_name,
                embedding_provider="fake",
            )

    missing_cfg = Settings(
        database_url=db_url,  # type: ignore[arg-type]
        environment="development",
        embedding_provider="local_onnx",
        local_embedding_model_dir=str(tmp_path / "missing-onnx"),
    )
    assert build_embedder_from_settings(missing_cfg) is None

    none_cfg = Settings(
        database_url=db_url,  # type: ignore[arg-type]
        environment="development",
        embedding_provider="none",
    )
    assert build_embedder_from_settings(none_cfg) is None

    test_fake_cfg = Settings(
        database_url=db_url,  # type: ignore[arg-type]
        environment="test",
        embedding_provider="fake",
    )
    assert isinstance(build_embedder_from_settings(test_fake_cfg), DeterministicFakeEmbedder)

    local_model_dir = Path(__file__).resolve().parents[2] / "storage" / "models" / "bge-small-en-v1.5-int8"
    if (local_model_dir / "model_quantized.onnx").is_file() and (local_model_dir / "vocab.txt").is_file():
        assert isinstance(build_embedder_from_settings(default_cfg), LocalOnnxEmbedder)
