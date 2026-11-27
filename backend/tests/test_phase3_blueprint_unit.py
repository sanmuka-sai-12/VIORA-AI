"""Focused unit tests for Batch 3.5A structured lesson blueprint generation and validation."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from viora.ai.public import (
    AIRequestConfig,
    AIResourceLimitError,
    AIResourceLimits,
    FakeAIGateway,
)
from viora.contracts import TenantContext
from viora.knowledge.public import (
    ConceptCandidate,
    ConceptDefinition,
    ConceptEvidenceBinding,
    ConceptExample,
    ConceptRelationshipCandidate,
    ExtractedKnowledgeGraph,
    KnowledgeReviewRecord,
    KnowledgeValidationReport,
    LearningObjectiveCandidate,
    LessonBlueprintGenerator,
    LessonBlueprintRecord,
    RawBlueprintAssessment,
    RawBlueprintExplanation,
    RawBlueprintNarration,
    RawBlueprintObjective,
    RawBlueprintScene,
    RawBlueprintWorkedExample,
    RawLessonBlueprintBatch,
    RawNarrationCue,
    RawSceneAction,
    RawSceneObject,
    build_authorized_evidence_map,
    build_lesson_blueprint_messages,
    generate_lesson_outline,
    project_published_blueprint,
    reviewed_graph,
)
from viora.sources.public import (
    AdaptiveDocumentExtractor,
    DerivedExtractionRecord,
    clamp_source_limits,
)


def _ctx() -> TenantContext:
    return TenantContext(actor_id=uuid4(), tenant_id=uuid4(), roles=frozenset({"subject_teacher"}))


def _approved_fixture(
    *,
    input_format: str = "typed_notes",
) -> tuple[
    DerivedExtractionRecord,
    ExtractedKnowledgeGraph,
    KnowledgeReviewRecord,
]:
    tid, sid, vid = uuid4(), uuid4(), uuid4()
    raw_text = (
        b"Arrays store elements in contiguous memory.\n\n"
        b"Binary search halves a sorted array in O(log n) time."
    )
    doc = AdaptiveDocumentExtractor().extract(
        tenant_id=tid,
        source_id=sid,
        version_id=vid,
        source_version=1,
        source_sha256="a" * 64,
        input_format=input_format,  # type: ignore[arg-type]
        title="Searching & Arrays",
        raw_bytes=raw_text,
        limits=clamp_source_limits(),
    )
    derived = DerivedExtractionRecord(
        extraction_id=uuid4(),
        tenant_id=tid,
        source_id=sid,
        version_id=vid,
        source_sha256=doc.source_sha256,
        extraction_version=1,
        document=doc,
        created_at=doc.created_at,
    )
    ev0 = doc.evidence_references[0]
    ev1 = doc.evidence_references[-1]

    def _bind(ev) -> ConceptEvidenceBinding:
        return ConceptEvidenceBinding(
            evidence_id=ev.evidence_id,
            source_id=sid,
            version_id=vid,
            source_sha256=doc.source_sha256,
            extraction_id=derived.extraction_id,
            page_number=ev.page_number,
            section_id=ev.section_id,
            block_id=ev.block_id,
            verified=True,
            excerpt=ev.excerpt,
        )

    c1 = ConceptCandidate(
        concept_id="con-1",
        name="Contiguous Array",
        normalized_name="contiguous array",
        canonical_summary="Arrays store elements in contiguous memory.",
        definitions=(ConceptDefinition(definition="Arrays store elements in contiguous memory.", grounding="learning_request" if input_format == "topic_description" else "source_supported", evidence_ids=() if input_format == "topic_description" else (ev0.evidence_id,)),),
        learning_objectives=(LearningObjectiveCandidate(objective_id="obj-1", statement="Describe contiguous array storage.", bloom_level="understand", grounding="learning_request" if input_format == "topic_description" else "source_supported", evidence_ids=() if input_format == "topic_description" else (ev0.evidence_id,)),),
        grounding="learning_request" if input_format == "topic_description" else "source_supported",
        evidence_bindings=() if input_format == "topic_description" else (_bind(ev0),),
        needs_review=input_format == "topic_description",
    )
    c2 = ConceptCandidate(
        concept_id="con-2",
        name="Binary Search",
        normalized_name="binary search",
        canonical_summary="Binary search halves a sorted array in O(log n) time.",
        learning_objectives=(LearningObjectiveCandidate(objective_id="obj-2", statement="Apply binary search on sorted arrays.", bloom_level="apply", grounding="learning_request" if input_format == "topic_description" else "source_supported", evidence_ids=() if input_format == "topic_description" else (ev1.evidence_id,)),),
        grounding="learning_request" if input_format == "topic_description" else "source_supported",
        evidence_bindings=() if input_format == "topic_description" else (_bind(ev1),),
        needs_review=input_format == "topic_description",
    )
    c_rejected = ConceptCandidate(
        concept_id="con-rej",
        name="Rejected Concept",
        normalized_name="rejected concept",
        canonical_summary="Rejected speculation.",
        grounding="uncertain",
        evidence_bindings=(),
        needs_review=True,
    )
    rel = ConceptRelationshipCandidate(
        relationship_id="rel-1",
        source_concept_id="con-1",
        source_concept_name="Contiguous Array",
        relation_type="prerequisite_of",
        target_concept_id="con-2",
        target_concept_name="Binary Search",
        grounding="learning_request" if input_format == "topic_description" else "source_supported",
        evidence_bindings=() if input_format == "topic_description" else (_bind(ev0),),
    )
    kg = ExtractedKnowledgeGraph(
        source_id=sid,
        version_id=vid,
        derived_extraction_id=derived.extraction_id,
        source_sha256=doc.source_sha256,
        input_format=input_format,  # type: ignore[arg-type]
        title="Searching & Arrays",
        extraction_mode="single_pass",
        concepts=(c1, c2, c_rejected),
        relationships=(rel,),
        validation=KnowledgeValidationReport(
            total_concepts=3,
            source_supported_count=0 if input_format == "topic_description" else 2,
            source_synthesized_count=0,
            learning_request_count=2 if input_format == "topic_description" else 0,
            inferred_count=0,
            uncertain_count=1,
            unsupported_count=0,
            invalid_evidence_refs_rejected=0,
            duplicate_merges_count=0,
            ambiguous_senses_retained=0,
        ),
        created_at=datetime.now(UTC),
    )
    review = KnowledgeReviewRecord(
        review_id=uuid4(),
        tenant_id=tid,
        source_id=sid,
        version_id=vid,
        concept_extraction_id=uuid4(),
        review_version=1,
        decision="approved",
        approved_concept_ids=("con-1", "con-2"),
        rejected_concept_ids=("con-rej",),
        reviewed_by_user_id=uuid4(),
        created_at=datetime.now(UTC),
    )
    return derived, reviewed_graph(kg, review.rejected_concept_ids), review


def test_blueprint_preserves_prerequisite_order_provenance_labels_and_student_key_redaction() -> None:
    derived, reviewed, review = _approved_fixture()
    outline = generate_lesson_outline(reviewed)
    ev0 = derived.document.evidence_references[0].evidence_id
    ev1 = derived.document.evidence_references[-1].evidence_id

    # Model returns explanations in REVERSED order (con-2 before con-1) and includes a fabricated evidence ID
    batch = RawLessonBlueprintBatch(
        lesson_summary="Learn contiguous arrays before binary search.",
        objectives=[
            RawBlueprintObjective(concept_id="con-1", statement="Describe contiguous array storage.", bloom_level="understand", evidence_ids=[ev0]),
            RawBlueprintObjective(concept_id="con-2", statement="Trace binary search halving.", bloom_level="apply", evidence_ids=[ev1]),
        ],
        explanations=[
            RawBlueprintExplanation(concept_id="con-2", explanation="Binary search inspects the midpoint and halves the range.", key_points=["O(log n)"], formulas=["mid = lo + (hi - lo) / 2"], evidence_ids=[ev1, "ev-fabricated"]),
            RawBlueprintExplanation(concept_id="con-1", explanation="Arrays store elements in contiguous memory.", key_points=["O(1) indexing"], evidence_ids=[ev0]),
        ],
        worked_examples=[
            RawBlueprintWorkedExample(
                concept_id="con-2",
                title="Find 14 in sorted array",
                problem_statement="Locate target 14 in [2, 6, 14, 22].",
                steps=["Compute mid=1 (val=6 < 14), move lo=2", "Compute mid=2 (val=14 == 14), return 2"],
                final_answer="Index 2",
                evidence_ids=[ev1],
            )
        ],
        scenes=[
            RawBlueprintScene(
                scene_id="scene-bs-1",
                concept_ids=["con-2"],
                objective_indices=[1],
                template="process_flow",
                visual_intent="Highlight midpoint halving on sorted array cells.",
                duration_seconds=14.0,
                timing_anchors=["start", "halve", "end"],
                objects=[
                    RawSceneObject(id="arr", kind="array_cells", label="[2, 6, 14, 22]", x=120, y=220, width=640, height=90),
                    RawSceneObject(id="mid_ptr", kind="callout", label="mid", x=360, y=140, width=80, height=50, style_token="accent"),
                ],
                actions=[
                    RawSceneAction(kind="appear", target_object_id="arr", anchor="start", duration_seconds=1.0),
                    RawSceneAction(kind="highlight", target_object_id="mid_ptr", anchor="halve", duration_seconds=2.0),
                ],
                evidence_ids=[ev1],
            )
        ],
        narrations=[
            RawBlueprintNarration(
                scene_id="scene-bs-1",
                concept_ids=["con-2"],
                script_text="Start with the full array, then compare the midpoint to halve the range.",
                caption_text="Compare midpoint and halve the range.",
                estimated_duration_seconds=14.0,
                cues=[RawNarrationCue(cue_id="cue-1", anchor="halve", spoken_phrase="halve the range", offset_hint_seconds=4.5)],
                evidence_ids=[ev1],
            )
        ],
        assessments=[
            RawBlueprintAssessment(
                concept_id="con-2",
                question_type="mcq",
                difficulty="foundational",
                stem="What is the worst-case time complexity of binary search?",
                options=["O(1)", "O(log n)", "O(n)"],
                correct_option_index=1,
                correct_answer="O(log n)",
                explanation="Each comparison halves the remaining search interval.",
                evidence_ids=[ev1],
            ),
            RawBlueprintAssessment(
                concept_id="con-1",
                question_type="short_answer",
                difficulty="foundational",
                stem="How are C array elements arranged in RAM?",
                correct_answer="contiguous memory",
                accepted_aliases=["contiguous"],
                explanation="Array elements occupy adjacent bytes in one contiguous block.",
                evidence_ids=[ev0],
            ),
        ],
    )
    gw = FakeAIGateway(structured_payloads={RawLessonBlueprintBatch: batch})
    res = LessonBlueprintGenerator(gw).generate(
        context=_ctx(),
        derived=derived,
        reviewed=reviewed,
        outline=outline,
        approved_review=review,
    )
    bp = res.blueprint
    assert res.inference_requests == 1
    assert bp.tts_synthesized is False
    assert bp.validation.executable_scene_code_blocked is True
    assert bp.validation.prerequisite_order_preserved is True
    assert bp.validation.rejected_evidence_refs_count == 1
    # Prerequisite order repaired: con-1 MUST precede con-2
    assert [e.concept_id for e in bp.explanations] == ["con-1", "con-2"]
    assert bp.explanations[0].interpretation_label == "verbatim_source"
    assert bp.explanations[0].grounding == "source_supported"
    assert bp.explanations[1].interpretation_label == "pedagogical_adaptation"
    assert bp.explanations[1].grounding == "source_synthesized"
    assert bp.worked_examples[0].interpretation_label == "worked_derivation"
    assert bp.scenes[0].narration_cue_ids == ("cue-1",)
    assert len(bp.assessments) == 2

    # Student-safe projection strips answer keys and aliases
    from viora.contracts import ArtifactVersionRef

    rec = LessonBlueprintRecord(
        blueprint_id=uuid4(),
        tenant_id=derived.tenant_id,
        source_id=derived.source_id,
        version_id=derived.version_id,
        derived_extraction_id=derived.extraction_id,
        concept_extraction_id=review.concept_extraction_id,
        approved_review_id=review.review_id,
        blueprint_version=1,
        provider="fake",
        model="fake-model",
        prompt_version="lesson_blueprint_prompt:1.0",
        schema_version="1.0",
        outline_version=outline.outline_version,
        input_fingerprint="b" * 64,
        source_sha256=derived.source_sha256,
        artifact_ref=ArtifactVersionRef(
            tenant_id=derived.tenant_id,
            kind="lesson_blueprint",
            resource_id=uuid4(),
            version_id=uuid4(),
            sha256="c" * 64,
        ),
        byte_size=1024,
        input_tokens=100,
        output_tokens=200,
        total_tokens=300,
        inference_requests=1,
        generation_ms=1.0,
        validation_ms=1.0,
        blueprint=bp,
        created_at=datetime.now(UTC),
    )
    pub_bp = project_published_blueprint(rec, published_at=datetime.now(UTC))
    dumped = pub_bp.model_dump()
    for q in dumped["assessments"]:
        assert "correct_answer" not in q
        assert "correct_option_index" not in q
        assert "accepted_aliases" not in q


def test_blueprint_blocks_executable_scene_code_bad_keys_and_enforces_limits() -> None:
    derived, reviewed, review = _approved_fixture()
    outline = generate_lesson_outline(reviewed)
    ev0 = derived.document.evidence_references[0].evidence_id

    batch = RawLessonBlueprintBatch(
        scenes=[
            # Unsafe JS & external URL scene -> rejected
            RawBlueprintScene(
                scene_id="bad-js-scene",
                concept_ids=["con-1"],
                template="process_flow",
                visual_intent="Run <script>alert(1)</script> animation",
                timing_anchors=["start"],
                objects=[RawSceneObject(id="o1", kind="node", label="Node")],
                actions=[RawSceneAction(kind="appear", target_object_id="o1", anchor="start")],
                asset_refs=["https://evil.example/payload.svg"],
                evidence_ids=[ev0],
            ),
            # Unknown template -> rejected
            RawBlueprintScene(
                scene_id="bad-tmpl",
                concept_ids=["con-1"],
                template="freeform_html",
                visual_intent="Custom widget",
                timing_anchors=["start"],
                objects=[RawSceneObject(id="o1", kind="node", label="Node")],
                actions=[RawSceneAction(kind="appear", target_object_id="o1", anchor="start")],
                evidence_ids=[ev0],
            ),
            # Valid scene with one unknown action + dangling target -> bad commands dropped, valid command kept
            RawBlueprintScene(
                scene_id="valid-scene",
                concept_ids=["con-1"],
                template="labeled_diagram",
                visual_intent="Show contiguous memory blocks.",
                timing_anchors=["start", "end"],
                objects=[RawSceneObject(id="mem", kind="array_cells", label="int a[4]")],
                actions=[
                    RawSceneAction(kind="exec_custom_js", target_object_id="mem", anchor="start"),
                    RawSceneAction(kind="appear", target_object_id="missing_obj", anchor="start"),
                    RawSceneAction(kind="appear", target_object_id="mem", anchor="start"),
                ],
                evidence_ids=[ev0],
            ),
        ],
        assessments=[
            # Contradictory MCQ key -> rejected
            RawBlueprintAssessment(
                concept_id="con-1",
                question_type="mcq",
                stem="Contradictory MCQ",
                options=["A", "B"],
                correct_option_index=0,
                correct_answer="B",
                explanation="Option A vs B mismatch.",
                evidence_ids=[ev0],
            ),
            # Duplicate MCQ options -> rejected
            RawBlueprintAssessment(
                concept_id="con-1",
                question_type="mcq",
                stem="Duplicate options MCQ",
                options=["Same", "same"],
                correct_option_index=0,
                correct_answer="Same",
                explanation="Invalid duplicate choices.",
                evidence_ids=[ev0],
            ),
        ],
    )
    gw = FakeAIGateway(structured_payloads={RawLessonBlueprintBatch: batch})
    bp = LessonBlueprintGenerator(gw).generate(
        context=_ctx(),
        derived=derived,
        reviewed=reviewed,
        outline=outline,
        approved_review=review,
    ).blueprint

    assert [s.scene_id for s in bp.scenes] == ["valid-scene"]
    assert len(bp.scenes[0].actions) == 1
    assert bp.validation.rejected_scenes_count == 2
    assert bp.validation.rejected_scene_commands_count == 2
    assert bp.assessments == ()
    assert bp.validation.rejected_assessment_items_count == 2

    # Topic-description outline preserves syllabus_request interpretation label
    t_derived, t_reviewed, t_review = _approved_fixture(input_format="topic_description")
    t_outline = generate_lesson_outline(t_reviewed)
    t_bp = LessonBlueprintGenerator(
        FakeAIGateway(structured_payloads={RawLessonBlueprintBatch: RawLessonBlueprintBatch()})
    ).generate(
        context=_ctx(),
        derived=t_derived,
        reviewed=t_reviewed,
        outline=t_outline,
        approved_review=t_review,
    ).blueprint
    assert t_bp.learning_request_only is True
    assert all(e.interpretation_label == "syllabus_request" for e in t_bp.explanations)

    # Resource limit enforcement
    with pytest.raises(ValueError, match="at most 1"):
        LessonBlueprintGenerator(FakeAIGateway(), max_inference_requests=2)
    with pytest.raises(AIResourceLimitError):
        LessonBlueprintGenerator(
            FakeAIGateway(resource_limits=AIResourceLimits(max_input_chars=250))
        ).generate(
            context=_ctx(),
            derived=derived,
            reviewed=reviewed,
            outline=outline,
            approved_review=review,
        )
    with pytest.raises(AIResourceLimitError):
        LessonBlueprintGenerator(
            FakeAIGateway(resource_limits=AIResourceLimits(max_output_tokens=200)),
            request_config=AIRequestConfig(max_tokens=1000),
        ).generate(
            context=_ctx(),
            derived=derived,
            reviewed=reviewed,
            outline=outline,
            approved_review=review,
        )


def test_blueprint_excludes_rejected_evidence_and_marks_unverified_claims() -> None:
    raw_doc = AdaptiveDocumentExtractor().extract(
        tenant_id=uuid4(),
        source_id=uuid4(),
        version_id=uuid4(),
        source_version=1,
        source_sha256="d" * 64,
        input_format="typed_notes",
        title="Stacks and Queues",
        raw_bytes=(
            b"Stacks operate in last-in first-out order.\n\n"
            b"Rejected rumor claims stacks sort automatically in O(1)."
        ),
        limits=clamp_source_limits(),
    )
    derived = DerivedExtractionRecord(
        extraction_id=uuid4(),
        tenant_id=raw_doc.tenant_id,
        source_id=raw_doc.source_id,
        version_id=raw_doc.version_id,
        source_sha256=raw_doc.source_sha256,
        extraction_version=1,
        document=raw_doc,
        created_at=raw_doc.created_at,
    )
    ev_approved = raw_doc.evidence_references[0]
    ev_rejected = raw_doc.evidence_references[-1]
    assert ev_approved.evidence_id != ev_rejected.evidence_id

    c_app = ConceptCandidate(
        concept_id="con-stack",
        name="Stack",
        normalized_name="stack",
        canonical_summary="Stacks operate in last-in first-out order.",
        grounding="source_supported",
        definitions=(ConceptDefinition(definition="LIFO container", grounding="source_supported", evidence_ids=(ev_approved.evidence_id,)),),
        examples=(ConceptExample(text="Push then pop returns top.", grounding="source_supported", evidence_ids=(ev_approved.evidence_id,)),),
        learning_objectives=(LearningObjectiveCandidate(objective_id="obj-stack", statement="Explain LIFO push/pop.", bloom_level="understand", grounding="source_supported", evidence_ids=(ev_approved.evidence_id,)),),
        evidence_bindings=(
            ConceptEvidenceBinding(
                evidence_id=ev_approved.evidence_id,
                source_id=derived.source_id,
                version_id=derived.version_id,
                source_sha256=derived.source_sha256,
                extraction_id=derived.extraction_id,
                page_number=ev_approved.page_number,
                section_id=ev_approved.section_id,
                block_id=ev_approved.block_id,
                verified=True,
                excerpt=ev_approved.excerpt,
            ),
        ),
        needs_review=False,
    )
    c_rej = ConceptCandidate(
        concept_id="con-rumor",
        name="Rejected Rumor",
        normalized_name="rejected rumor",
        canonical_summary="Stacks sort automatically in O(1).",
        grounding="uncertain",
        evidence_bindings=(
            ConceptEvidenceBinding(
                evidence_id=ev_rejected.evidence_id,
                source_id=derived.source_id,
                version_id=derived.version_id,
                source_sha256=derived.source_sha256,
                extraction_id=derived.extraction_id,
                page_number=ev_rejected.page_number,
                section_id=ev_rejected.section_id,
                block_id=ev_rejected.block_id,
                verified=True,
                excerpt=ev_rejected.excerpt,
            ),
        ),
        needs_review=True,
    )
    kg = ExtractedKnowledgeGraph(
        source_id=derived.source_id,
        version_id=derived.version_id,
        derived_extraction_id=derived.extraction_id,
        source_sha256=derived.source_sha256,
        input_format="typed_notes",
        title="Stacks and Queues",
        extraction_mode="single_pass",
        concepts=(c_app, c_rej),
        relationships=(),
        validation=KnowledgeValidationReport(
            total_concepts=2,
            source_supported_count=1,
            source_synthesized_count=0,
            learning_request_count=0,
            inferred_count=0,
            uncertain_count=1,
            unsupported_count=0,
            invalid_evidence_refs_rejected=0,
            duplicate_merges_count=0,
            ambiguous_senses_retained=0,
        ),
        created_at=datetime.now(UTC),
    )
    review = KnowledgeReviewRecord(
        review_id=uuid4(),
        tenant_id=derived.tenant_id,
        source_id=derived.source_id,
        version_id=derived.version_id,
        concept_extraction_id=uuid4(),
        review_version=1,
        decision="approved",
        approved_concept_ids=("con-stack",),
        rejected_concept_ids=("con-rumor",),
        reviewed_by_user_id=uuid4(),
        created_at=datetime.now(UTC),
    )
    reviewed = reviewed_graph(kg, review.rejected_concept_ids)
    auth_map = build_authorized_evidence_map(
        derived=derived,
        reviewed=reviewed,
        rejected_concept_ids=review.rejected_concept_ids,
    )
    assert set(auth_map) == {ev_approved.evidence_id}
    assert ev_rejected.evidence_id not in auth_map

    # Even if approved concepts had no verified bindings, rejected evidence must NEVER leak
    empty_bindings_graph = kg.model_copy(
        update={
            "concepts": (
                c_app.model_copy(update={"evidence_bindings": ()}),
                c_rej,
            )
        }
    )
    assert (
        build_authorized_evidence_map(
            derived=derived,
            reviewed=empty_bindings_graph,
            rejected_concept_ids=("con-rumor",),
        )
        == {}
    )

    outline = generate_lesson_outline(reviewed)
    msgs = build_lesson_blueprint_messages(
        outline=outline,
        reviewed=reviewed,
        authorized_evidence=auth_map,
        max_input_chars=12000,
        schema_overhead_chars=500,
    )
    prompt_text = "\n".join(m.content for m in msgs)
    assert ev_rejected.evidence_id not in prompt_text
    assert "Rejected Rumor" not in prompt_text

    # Model cites ONLY rejected evidence on explanation + assessment -> must NOT fall back & mask
    batch = RawLessonBlueprintBatch(
        explanations=[
            RawBlueprintExplanation(
                concept_id="con-stack",
                explanation="Stacks sort automatically in O(1).",
                evidence_ids=[ev_rejected.evidence_id],
            )
        ],
        assessments=[
            RawBlueprintAssessment(
                concept_id="con-stack",
                question_type="short_answer",
                stem="Which access order does a stack enforce?",
                correct_answer="LIFO",
                accepted_aliases=["last-in first-out"],
                explanation="Stacks remove the most recently pushed item first.",
                evidence_ids=[ev_rejected.evidence_id],
            )
        ],
    )
    bp = LessonBlueprintGenerator(
        FakeAIGateway(structured_payloads={RawLessonBlueprintBatch: batch})
    ).generate(
        context=_ctx(),
        derived=derived,
        reviewed=reviewed,
        outline=outline,
        approved_review=review,
    ).blueprint

    assert bp.validation.rejected_evidence_refs_count == 2
    assert bp.validation.all_evidence_verified is False
    assert bp.explanations[0].evidence_bindings == ()
    assert bp.explanations[0].grounding == "inferred"
    assert bp.explanations[0].interpretation_label == "unverified_draft"
    assert bp.explanations[0].needs_review is True
    assert bp.assessments[0].evidence_bindings == ()
    assert bp.assessments[0].interpretation_label == "unverified_draft"


def test_blueprint_adversarial_scene_xss_and_answer_key_isolation() -> None:
    derived, reviewed, review = _approved_fixture()
    outline = generate_lesson_outline(reviewed)
    ev0 = derived.document.evidence_references[0].evidence_id

    batch = RawLessonBlueprintBatch(
        scenes=[
            RawBlueprintScene(
                scene_id="xss-iframe",
                concept_ids=["con-1"],
                template="process_flow",
                visual_intent='<iframe src="https://evil.test"></iframe>',
                timing_anchors=["start"],
                objects=[RawSceneObject(id="o1", kind="node", label="Node")],
                actions=[RawSceneAction(kind="appear", target_object_id="o1", anchor="start")],
                evidence_ids=[ev0],
            ),
            RawBlueprintScene(
                scene_id="xss-handler",
                concept_ids=["con-1"],
                template="labeled_diagram",
                visual_intent="Safe intent",
                timing_anchors=["start"],
                objects=[RawSceneObject(id="o1", kind="node", label='<img src=x onerror="alert(1)">')],
                actions=[RawSceneAction(kind="appear", target_object_id="o1", anchor="start")],
                evidence_ids=[ev0],
            ),
            RawBlueprintScene(
                scene_id="bad-coords",
                concept_ids=["con-1"],
                template="labeled_diagram",
                visual_intent="Non-finite coordinate payload",
                timing_anchors=["start"],
                objects=[RawSceneObject(id="o1", kind="node", label="Node", x=float("nan"))],
                actions=[RawSceneAction(kind="appear", target_object_id="o1", anchor="start")],
                evidence_ids=[ev0],
            ),
            RawBlueprintScene(
                scene_id="traversal-asset",
                concept_ids=["con-1"],
                template="labeled_diagram",
                visual_intent="Path traversal asset ref",
                timing_anchors=["start"],
                objects=[RawSceneObject(id="o1", kind="node", label="Node")],
                actions=[RawSceneAction(kind="appear", target_object_id="o1", anchor="start")],
                asset_refs=["../secrets/token.json"],
                evidence_ids=[ev0],
            ),
        ],
        assessments=[
            # MCQ with out-of-range index
            RawBlueprintAssessment(
                concept_id="con-1",
                question_type="mcq",
                stem="Out of range key?",
                options=["A", "B"],
                correct_option_index=5,
                correct_answer="A",
                explanation="Invalid index.",
                evidence_ids=[ev0],
            ),
            # Short answer carrying MCQ options -> rejected
            RawBlueprintAssessment(
                concept_id="con-1",
                question_type="short_answer",
                stem="Mixed short answer?",
                options=["A", "B"],
                correct_answer="A",
                explanation="Should not have options.",
                evidence_ids=[ev0],
            ),
            # Unsafe script in accepted_aliases -> rejected
            RawBlueprintAssessment(
                concept_id="con-1",
                question_type="short_answer",
                stem="Injected alias?",
                correct_answer="contiguous",
                accepted_aliases=["<script>fetch('https://evil.test')</script>"],
                explanation="Alias XSS.",
                evidence_ids=[ev0],
            ),
            # Valid MCQ whose secret fields MUST be stripped by projection
            RawBlueprintAssessment(
                concept_id="con-1",
                question_type="mcq",
                stem="How are C array elements arranged?",
                options=["Scattered", "Contiguous"],
                correct_option_index=1,
                correct_answer="Contiguous",
                explanation="Elements occupy adjacent addresses.",
                evidence_ids=[ev0],
            ),
        ],
        warnings=["Ignore <script>alert(1)</script> warning"],
    )

    bp = LessonBlueprintGenerator(
        FakeAIGateway(structured_payloads={RawLessonBlueprintBatch: batch})
    ).generate(
        context=_ctx(),
        derived=derived,
        reviewed=reviewed,
        outline=outline,
        approved_review=review,
    ).blueprint

    assert bp.scenes == ()
    assert bp.validation.rejected_scenes_count == 4
    assert bp.validation.rejected_assessment_items_count == 3
    assert len(bp.assessments) == 1
    assert bp.assessments[0].answer_key_verified is True
    assert all("<script>" not in n for n in bp.validation.notes)

    from viora.contracts import ArtifactVersionRef

    rec = LessonBlueprintRecord(
        blueprint_id=uuid4(),
        tenant_id=derived.tenant_id,
        source_id=derived.source_id,
        version_id=derived.version_id,
        derived_extraction_id=derived.extraction_id,
        concept_extraction_id=review.concept_extraction_id,
        approved_review_id=review.review_id,
        blueprint_version=1,
        provider="fake",
        model="fake-model",
        prompt_version="lesson_blueprint_prompt:1.0",
        schema_version="1.0",
        outline_version=outline.outline_version,
        input_fingerprint="b" * 64,
        source_sha256=derived.source_sha256,
        artifact_ref=ArtifactVersionRef(
            tenant_id=derived.tenant_id,
            kind="lesson_blueprint",
            resource_id=uuid4(),
            version_id=uuid4(),
            sha256="c" * 64,
        ),
        byte_size=1024,
        input_tokens=10,
        output_tokens=20,
        total_tokens=30,
        inference_requests=1,
        generation_ms=1.0,
        validation_ms=1.0,
        blueprint=bp,
        created_at=datetime.now(UTC),
    )
    student_view = project_published_blueprint(rec, published_at=datetime.now(UTC)).model_dump()
    assert student_view["teacher_approved"] is True
    assert student_view["published_to_students"] is True
    for secret_key in (
        "correct_answer",
        "correct_option_index",
        "accepted_aliases",
        "explanation",
        "answer_key_verified",
    ):
        assert secret_key not in student_view["assessments"][0]
