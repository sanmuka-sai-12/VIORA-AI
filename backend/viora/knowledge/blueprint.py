"""Grounded lesson blueprint generation and strict scene/assessment/provenance validation."""

from __future__ import annotations

import json
import math
import re
import time
from dataclasses import dataclass
from datetime import UTC, datetime

from ..ai.public import (
    AIGateway,
    AIRequestConfig,
    AIResourceLimitError,
    AIResourceLimits,
    clamp_resource_limits,
)
from ..contracts import TenantContext
from ..sources.public import DerivedExtractionRecord, EvidenceReference
from .prompts import build_lesson_blueprint_messages
from .schemas import (
    BLUEPRINT_SCHEMA_VERSION,
    BlueprintAssessmentItem,
    BlueprintExplanationStep,
    BlueprintLearningObjective,
    BlueprintNarrationSegment,
    BlueprintValidationReport,
    BlueprintWorkedExample,
    ConceptEvidenceBinding,
    ExtractedKnowledgeGraph,
    GroundingClassification,
    InterpretationLabel,
    KnowledgeReviewRecord,
    LessonBlueprint,
    LessonBlueprintRecord,
    NarrationCueSpec,
    PublishedLessonBlueprint,
    RawLessonBlueprintBatch,
    SceneActionSpec,
    SceneCoordinateSpace,
    SceneObjectSpec,
    StructuredLessonOutline,
    StudentSafeAssessmentItem,
    TypedVisualSceneSpec,
)

ALLOWED_SCENE_TEMPLATES: frozenset[str] = frozenset(
    {"vector_force", "process_flow", "parts_of_whole", "plot_relationship", "labeled_diagram"}
)
ALLOWED_SCENE_OBJECTS: frozenset[str] = frozenset(
    {
        "rectangle",
        "circle",
        "vector_arrow",
        "node",
        "edge",
        "region",
        "axis",
        "curve",
        "point",
        "callout",
        "text_label",
        "array_cells",
    }
)
ALLOWED_SCENE_ACTIONS: frozenset[str] = frozenset(
    {
        "appear",
        "disappear",
        "highlight",
        "move",
        "increase_vector",
        "decrease_vector",
        "state_change",
        "reveal_step",
        "trace_path",
        "compare",
    }
)
ALLOWED_STYLE_TOKENS: frozenset[str] = frozenset(
    {"primary", "secondary", "accent", "muted", "warning"}
)

_SAFE_ID_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,63}$")
_SAFE_ANCHOR_RE = re.compile(r"^[a-z][a-z0-9_]{0,47}$")
_SAFE_ASSET_REF_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9/_.-]{0,127}$")
_UNSAFE_CODE_RE = re.compile(
    r"(?:<\s*(?:script|iframe)\b|javascript:|vbscript:|\bdata:\s*[a-z0-9.+-]+/|\bon(?:load|error|click|mouseover|focus)\s*=|"
    r"\beval\s*\(|\bFunction\s*\(|=>|\b__import__\b|\bexec\s*\(|<\s*svg\b[^>]*(?:onload|onerror|onclick)|https?://)",
    re.IGNORECASE,
)
_WHITESPACE_RE = re.compile(r"\s+")


def _compact_blueprint_schema_size_chars() -> int:
    def _strip(node: object, in_props: bool = False) -> object:
        if isinstance(node, dict):
            return {
                k: _strip(v, k == "properties")
                for k, v in node.items()
                if in_props or k != "title"
            }
        if isinstance(node, list):
            return [_strip(x) for x in node]
        return node

    return len(
        json.dumps(
            _strip(RawLessonBlueprintBatch.model_json_schema()),
            separators=(",", ":"),
        )
    )


_BLUEPRINT_SCHEMA_OVERHEAD_CHARS = _compact_blueprint_schema_size_chars()


def _contains_unsafe_code(*values: str | None) -> bool:
    return any(bool(v and _UNSAFE_CODE_RE.search(v)) for v in values)


def _verbatim_in_bindings(text: str, bindings: tuple[ConceptEvidenceBinding, ...]) -> bool:
    clean = _WHITESPACE_RE.sub(" ", text).strip()
    if not clean or not bindings:
        return False
    prefix = r"(?<![A-Za-z0-9_-])" if (clean[0].isalnum() or clean[0] in "_-") else ""
    suffix = r"(?![A-Za-z0-9_-])" if (clean[-1].isalnum() or clean[-1] in "_-") else ""
    pattern = f"{prefix}{re.escape(clean)}{suffix}"
    return any(
        re.search(pattern, _WHITESPACE_RE.sub(" ", b.excerpt).strip()) is not None
        for b in bindings
        if b.verified
    )


def build_authorized_evidence_map(
    *,
    derived: DerivedExtractionRecord,
    reviewed: ExtractedKnowledgeGraph,
    rejected_concept_ids: tuple[str, ...] = (),
) -> dict[str, EvidenceReference]:
    """Build exact authorized evidence map for approved curriculum only (excluding rejected concepts)."""
    if reviewed.input_format == "topic_description":
        return {}
    rejected_set = set(rejected_concept_ids)
    approved_ev_ids = {
        b.evidence_id
        for c in reviewed.concepts
        if c.concept_id not in rejected_set
        for b in c.evidence_bindings
    }
    if not approved_ev_ids:
        return {}
    authorized: dict[str, EvidenceReference] = {}
    for ev in derived.document.evidence_references:
        if (
            ev.source_id != reviewed.source_id
            or ev.version_id != reviewed.version_id
            or ev.source_sha256 != reviewed.source_sha256
        ):
            continue
        if ev.evidence_id not in approved_ev_ids:
            continue
        authorized[ev.evidence_id] = ev
    return authorized


@dataclass(frozen=True)
class LessonBlueprintExecutionResult:
    blueprint: LessonBlueprint
    provider: str
    model: str
    input_tokens: int
    output_tokens: int
    total_tokens: int
    inference_requests: int
    generation_ms: float
    validation_ms: float


class LessonBlueprintGenerator:
    """Generates and deterministically validates typed, evidence-grounded lesson blueprints."""

    def __init__(
        self,
        gateway: AIGateway,
        *,
        request_config: AIRequestConfig | None = None,
        max_inference_requests: int = 1,
    ) -> None:
        if max_inference_requests != 1:
            raise ValueError("LessonBlueprintGenerator permits at most 1 inference request")
        self.gateway = gateway
        self.max_inference_requests = 1
        self.request_config = request_config or AIRequestConfig(
            max_tokens=2200,
            temperature=0.0,
            timeout_seconds=30.0,
            total_deadline_seconds=50.0,
            max_retries=1,
        )

    def generate(
        self,
        *,
        context: TenantContext,
        derived: DerivedExtractionRecord,
        reviewed: ExtractedKnowledgeGraph,
        outline: StructuredLessonOutline,
        approved_review: KnowledgeReviewRecord,
    ) -> LessonBlueprintExecutionResult:
        if approved_review.decision != "approved":
            raise ValueError("Approved curriculum review is required for blueprint generation")
        if not outline.prerequisite_analysis.teaching_order:
            raise ValueError("Approved lesson outline must contain at least one concept")

        gw_limits = getattr(self.gateway, "hard_limits", None)
        hard_limits = gw_limits if isinstance(gw_limits, AIResourceLimits) else AIResourceLimits()
        effective_limits = clamp_resource_limits(
            self.request_config.resource_limits, hard_limits
        )
        if (
            self.request_config.max_tokens is not None
            and self.request_config.max_tokens > effective_limits.max_output_tokens
        ):
            raise AIResourceLimitError(
                f"Requested max_tokens {self.request_config.max_tokens} exceeds limit {effective_limits.max_output_tokens}"
            )

        authorized_evidence = build_authorized_evidence_map(
            derived=derived,
            reviewed=reviewed,
            rejected_concept_ids=approved_review.rejected_concept_ids,
        )
        messages = build_lesson_blueprint_messages(
            outline=outline,
            reviewed=reviewed,
            authorized_evidence=authorized_evidence,
            max_input_chars=effective_limits.max_input_chars,
            schema_overhead_chars=_BLUEPRINT_SCHEMA_OVERHEAD_CHARS,
        )

        t_gen0 = time.monotonic_ns()
        res = self.gateway.complete_structured(
            context,
            messages,
            RawLessonBlueprintBatch,
            config=self.request_config,
        )
        generation_ms = round((time.monotonic_ns() - t_gen0) / 1_000_000.0, 3)

        t_val0 = time.monotonic_ns()
        blueprint = self._validate_blueprint(
            derived=derived,
            reviewed=reviewed,
            outline=outline,
            approved_review=approved_review,
            authorized_evidence=authorized_evidence,
            raw=res.parsed,
        )
        validation_ms = round((time.monotonic_ns() - t_val0) / 1_000_000.0, 3)

        return LessonBlueprintExecutionResult(
            blueprint=blueprint,
            provider=res.metadata.provider,
            model=res.model,
            input_tokens=res.usage.input_tokens,
            output_tokens=res.usage.output_tokens,
            total_tokens=res.usage.total_tokens,
            inference_requests=1,
            generation_ms=generation_ms,
            validation_ms=validation_ms,
        )

    def _validate_blueprint(
        self,
        *,
        derived: DerivedExtractionRecord,
        reviewed: ExtractedKnowledgeGraph,
        outline: StructuredLessonOutline,
        approved_review: KnowledgeReviewRecord,
        authorized_evidence: dict[str, EvidenceReference],
        raw: RawLessonBlueprintBatch,
    ) -> LessonBlueprint:
        is_topic = reviewed.input_format == "topic_description"
        concept_by_id = {c.concept_id: c for c in reviewed.concepts}
        approved_cids = set(concept_by_id)
        order_steps = outline.prerequisite_analysis.teaching_order
        ordered_cids = tuple(s.concept_id for s in order_steps)
        order_rank = {cid: idx for idx, cid in enumerate(ordered_cids)}
        binding_cache: dict[str, ConceptEvidenceBinding] = {}
        rejected_ev_refs = 0
        rejected_scenes = 0
        rejected_scene_cmds = 0
        rejected_assessments = 0
        notes: list[str] = []

        def _add_note(note: str) -> None:
            clean = note.strip()[:220]
            if clean and clean not in notes:
                notes.append(clean)

        def _verify_ev(
            raw_ids: list[str] | tuple[str, ...],
            *,
            fallback_concept_id: str | None = None,
        ) -> tuple[ConceptEvidenceBinding, ...]:
            nonlocal rejected_ev_refs
            if is_topic:
                return ()
            out: list[ConceptEvidenceBinding] = []
            seen: set[str] = set()
            for eid in raw_ids:
                clean = eid.strip()
                if not clean or clean in seen:
                    continue
                ev = authorized_evidence.get(clean)
                if ev is None:
                    rejected_ev_refs += 1
                    continue
                seen.add(clean)
                cached = binding_cache.get(clean)
                if cached is None:
                    cached = ConceptEvidenceBinding(
                        evidence_id=ev.evidence_id,
                        source_id=reviewed.source_id,
                        version_id=reviewed.version_id,
                        source_sha256=reviewed.source_sha256,
                        extraction_id=derived.extraction_id,
                        page_number=ev.page_number,
                        section_id=ev.section_id,
                        block_id=ev.block_id,
                        verified=True,
                        excerpt=ev.excerpt[:1000],
                    )
                    binding_cache[clean] = cached
                out.append(cached)
            had_raw_refs = any(eid.strip() for eid in raw_ids)
            if not out and not had_raw_refs and fallback_concept_id and fallback_concept_id in concept_by_id:
                for b in concept_by_id[fallback_concept_id].evidence_bindings:
                    if b.evidence_id in authorized_evidence and b.evidence_id not in seen:
                        seen.add(b.evidence_id)
                        out.append(b)
            return tuple(out)

        def _classify(
            text_sample: str,
            bindings: tuple[ConceptEvidenceBinding, ...],
            *,
            kind_label: InterpretationLabel,
        ) -> tuple[GroundingClassification, InterpretationLabel, bool]:
            if is_topic:
                return "learning_request", "syllabus_request", True
            if bindings:
                if _verbatim_in_bindings(text_sample, bindings):
                    return "source_supported", "verbatim_source", False
                return "source_synthesized", kind_label, True
            return "inferred", "unverified_draft", True

        # 1. Learning objectives (bounded to approved concepts)
        objectives: list[BlueprintLearningObjective] = []
        obj_ids_by_cid: dict[str, list[str]] = {}
        for raw_obj in raw.objectives[:24]:
            cid = raw_obj.concept_id.strip()
            if cid not in approved_cids or _contains_unsafe_code(raw_obj.statement):
                continue
            stmt = raw_obj.statement.strip()
            if not stmt:
                continue
            bindings = _verify_ev(raw_obj.evidence_ids, fallback_concept_id=cid)
            grounding, interp, review_req = _classify(
                stmt, bindings, kind_label="synthesized_source"
            )
            oid = f"bp-obj-{len(objectives) + 1}"
            objectives.append(
                BlueprintLearningObjective(
                    objective_id=oid,
                    concept_id=cid,
                    statement=stmt,
                    bloom_level=raw_obj.bloom_level,
                    grounding=grounding,
                    interpretation_label=interp,
                    evidence_bindings=bindings,
                    needs_review=review_req,
                )
            )
            obj_ids_by_cid.setdefault(cid, []).append(oid)

        for cid in ordered_cids:
            if cid not in obj_ids_by_cid:
                c = concept_by_id[cid]
                for lo in c.learning_objectives[:2]:
                    bindings = _verify_ev(lo.evidence_ids, fallback_concept_id=cid)
                    grounding, interp, review_req = _classify(
                        lo.statement, bindings, kind_label="synthesized_source"
                    )
                    oid = f"bp-obj-{len(objectives) + 1}"
                    objectives.append(
                        BlueprintLearningObjective(
                            objective_id=oid,
                            concept_id=cid,
                            statement=lo.statement,
                            bloom_level=lo.bloom_level,
                            grounding=grounding,
                            interpretation_label=interp,
                            evidence_bindings=bindings,
                            needs_review=review_req,
                        )
                    )
                    obj_ids_by_cid.setdefault(cid, []).append(oid)

        # 2. Ordered explanations strictly matching prerequisite teaching_order
        raw_exp_by_cid: dict[str, object] = {}
        raw_exp_order: list[str] = []
        for raw_exp in raw.explanations:
            cid = raw_exp.concept_id.strip()
            if cid in approved_cids and cid not in raw_exp_by_cid:
                raw_exp_by_cid[cid] = raw_exp
                raw_exp_order.append(cid)
        if raw_exp_order and raw_exp_order != [c for c in ordered_cids if c in raw_exp_by_cid]:
            _add_note("Reordered explanations to preserve approved prerequisite teaching order.")

        explanations: list[BlueprintExplanationStep] = []
        for step in order_steps:
            cid = step.concept_id
            c = concept_by_id[cid]
            raw_exp = raw_exp_by_cid.get(cid)
            if raw_exp is not None and not _contains_unsafe_code(raw_exp.explanation):  # type: ignore[attr-defined]
                exp_text = raw_exp.explanation.strip()  # type: ignore[attr-defined]
                k_pts = tuple(p.strip()[:240] for p in raw_exp.key_points if p.strip() and not _contains_unsafe_code(p))  # type: ignore[attr-defined]
                formulas = tuple(f.strip()[:240] for f in raw_exp.formulas if f.strip() and not _contains_unsafe_code(f))  # type: ignore[attr-defined]
                bindings = _verify_ev(raw_exp.evidence_ids, fallback_concept_id=cid)  # type: ignore[attr-defined]
            else:
                exp_text = c.canonical_summary
                k_pts = tuple(d.definition[:240] for d in c.definitions[:2])
                formulas = ()
                bindings = _verify_ev((), fallback_concept_id=cid)
            grounding, interp, review_req = _classify(
                exp_text, bindings, kind_label="pedagogical_adaptation"
            )
            explanations.append(
                BlueprintExplanationStep(
                    step_index=step.order_index,
                    depth_level=step.depth_level,
                    concept_id=cid,
                    concept_name=c.name,
                    prerequisite_concept_ids=step.prerequisite_concept_ids,
                    explanation=exp_text,
                    key_points=k_pts,
                    formulas=formulas,
                    supported_by_source=bool(bindings) and not is_topic,
                    grounding=grounding,
                    interpretation_label=interp,
                    evidence_bindings=bindings,
                    needs_review=review_req,
                )
            )

        # 3. Worked examples
        worked_examples: list[BlueprintWorkedExample] = []
        for raw_ex in raw.worked_examples[:16]:
            cid = raw_ex.concept_id.strip()
            if cid not in approved_cids or _contains_unsafe_code(
                raw_ex.title, raw_ex.problem_statement, raw_ex.final_answer, *raw_ex.steps
            ):
                continue
            steps = tuple(s.strip()[:500] for s in raw_ex.steps if s.strip())
            if not steps:
                continue
            bindings = _verify_ev(raw_ex.evidence_ids, fallback_concept_id=cid)
            grounding, interp, review_req = _classify(
                raw_ex.problem_statement, bindings, kind_label="worked_derivation"
            )
            worked_examples.append(
                BlueprintWorkedExample(
                    example_id=f"bp-ex-{len(worked_examples) + 1}",
                    concept_id=cid,
                    title=raw_ex.title.strip(),
                    problem_statement=raw_ex.problem_statement.strip(),
                    steps=steps,
                    final_answer=raw_ex.final_answer.strip(),
                    grounding=grounding,
                    interpretation_label=interp,
                    evidence_bindings=bindings,
                    needs_review=review_req,
                )
            )

        # 4. Typed visual scenes (strict catalog, no executable code, referential integrity)
        prelim_scenes: list[tuple[int, TypedVisualSceneSpec]] = []
        seen_scene_ids: set[str] = set()
        for raw_sc in raw.scenes[:24]:
            sc_id = raw_sc.scene_id.strip()
            cids = tuple(cid.strip() for cid in raw_sc.concept_ids if cid.strip() in approved_cids)
            if (
                not _SAFE_ID_RE.fullmatch(sc_id)
                or sc_id in seen_scene_ids
                or not cids
                or raw_sc.template not in ALLOWED_SCENE_TEMPLATES
                or _contains_unsafe_code(sc_id, raw_sc.visual_intent, *raw_sc.asset_refs)
            ):
                rejected_scenes += 1
                continue

            # Reject unsafe external asset references
            safe_assets: list[str] = []
            unsafe_asset = False
            for a_ref in raw_sc.asset_refs:
                clean_a = a_ref.strip()
                if not clean_a:
                    continue
                if (
                    "://" in clean_a
                    or ".." in clean_a
                    or clean_a.startswith(("/", "\\"))
                    or not _SAFE_ASSET_REF_RE.fullmatch(clean_a)
                ):
                    unsafe_asset = True
                    break
                safe_assets.append(clean_a)
            if unsafe_asset:
                rejected_scenes += 1
                continue

            anchors = tuple(
                dict.fromkeys(
                    a.strip()
                    for a in raw_sc.timing_anchors
                    if _SAFE_ANCHOR_RE.fullmatch(a.strip())
                )
            )
            if not anchors:
                anchors = ("start", "end")
            anchor_set = set(anchors)

            valid_objects: list[SceneObjectSpec] = []
            obj_ids: set[str] = set()
            for obj in raw_sc.objects[:24]:
                oid = obj.id.strip()
                if (
                    not _SAFE_ID_RE.fullmatch(oid)
                    or oid in obj_ids
                    or obj.kind not in ALLOWED_SCENE_OBJECTS
                    or obj.style_token not in ALLOWED_STYLE_TOKENS
                    or _contains_unsafe_code(oid, obj.label)
                    or not (
                        math.isfinite(obj.x)
                        and math.isfinite(obj.y)
                        and math.isfinite(obj.width)
                        and math.isfinite(obj.height)
                    )
                ):
                    rejected_scene_cmds += 1
                    continue
                obj_ids.add(oid)
                valid_objects.append(
                    SceneObjectSpec(
                        id=oid,
                        kind=obj.kind,  # type: ignore[arg-type]
                        label=obj.label.strip()[:160],
                        x=min(1280.0, max(0.0, float(obj.x))),
                        y=min(720.0, max(0.0, float(obj.y))),
                        width=min(1280.0, max(1.0, float(obj.width))),
                        height=min(720.0, max(1.0, float(obj.height))),
                        style_token=obj.style_token,  # type: ignore[arg-type]
                    )
                )

            valid_actions: list[SceneActionSpec] = []
            for act in raw_sc.actions[:40]:
                tid = act.target_object_id.strip()
                anch = act.anchor.strip()
                if (
                    act.kind not in ALLOWED_SCENE_ACTIONS
                    or tid not in obj_ids
                    or anch not in anchor_set
                    or _contains_unsafe_code(act.kind, tid, anch, act.note)
                    or not (
                        math.isfinite(act.duration_seconds)
                        and math.isfinite(act.delta_x)
                        and math.isfinite(act.delta_y)
                        and math.isfinite(act.scale_factor)
                    )
                ):
                    rejected_scene_cmds += 1
                    continue
                valid_actions.append(
                    SceneActionSpec(
                        kind=act.kind,  # type: ignore[arg-type]
                        target_object_id=tid,
                        anchor=anch,
                        duration_seconds=min(30.0, max(0.1, float(act.duration_seconds))),
                        easing=act.easing,
                        delta_x=min(1280.0, max(-1280.0, float(act.delta_x))),
                        delta_y=min(720.0, max(-720.0, float(act.delta_y))),
                        scale_factor=min(10.0, max(0.1, float(act.scale_factor))),
                        note=act.note.strip()[:240] if act.note and act.note.strip() else None,
                    )
                )

            if not valid_objects or not valid_actions:
                rejected_scenes += 1
                continue

            seen_scene_ids.add(sc_id)
            sc_obj_ids: list[str] = []
            for idx in raw_sc.objective_indices:
                if 0 <= idx < len(objectives):
                    sc_obj_ids.append(objectives[idx].objective_id)
            if not sc_obj_ids:
                for cid in cids:
                    sc_obj_ids.extend(obj_ids_by_cid.get(cid, [])[:2])
            bindings = _verify_ev(raw_sc.evidence_ids, fallback_concept_id=cids[0])
            grounding, interp, review_req = _classify(
                raw_sc.visual_intent, bindings, kind_label="pedagogical_adaptation"
            )
            dur = float(raw_sc.duration_seconds)
            dur = min(120.0, max(1.0, dur if math.isfinite(dur) else 12.0))
            min_rank = min(order_rank[cid] for cid in cids)
            prelim_scenes.append(
                (
                    min_rank,
                    TypedVisualSceneSpec(
                        scene_id=sc_id,
                        order_index=len(prelim_scenes) + 1,
                        concept_ids=cids,
                        objective_ids=tuple(dict.fromkeys(sc_obj_ids)),
                        template=raw_sc.template,  # type: ignore[arg-type]
                        visual_intent=raw_sc.visual_intent.strip(),
                        coordinate_space=SceneCoordinateSpace(),
                        duration_seconds=dur,
                        timing_anchors=anchors,
                        objects=tuple(valid_objects),
                        actions=tuple(valid_actions),
                        narration_cue_ids=(),
                        asset_refs=tuple(safe_assets),
                        grounding=grounding,
                        interpretation_label=interp,
                        evidence_bindings=bindings,
                        needs_review=review_req,
                    ),
                )
            )

        prelim_scenes.sort(key=lambda pair: (pair[0], pair[1].order_index))
        scenes_by_id: dict[str, TypedVisualSceneSpec] = {}
        ordered_scenes: list[TypedVisualSceneSpec] = []
        for new_idx, (_, sc) in enumerate(prelim_scenes, start=1):
            updated_sc = sc.model_copy(update={"order_index": new_idx})
            ordered_scenes.append(updated_sc)
            scenes_by_id[updated_sc.scene_id] = updated_sc

        # 5. Narration alignment (bound to validated scenes & timing_anchors; zero TTS)
        narrations: list[BlueprintNarrationSegment] = []
        cues_by_scene: dict[str, list[str]] = {}
        for raw_nar in raw.narrations[:24]:
            sc_id = raw_nar.scene_id.strip()
            target_scene = scenes_by_id.get(sc_id)
            if target_scene is None or _contains_unsafe_code(
                raw_nar.script_text, raw_nar.caption_text
            ):
                continue
            script = raw_nar.script_text.strip()
            if not script:
                continue
            caption = raw_nar.caption_text.strip() or script
            anchor_set = set(target_scene.timing_anchors)
            valid_cues: list[NarrationCueSpec] = []
            for raw_cue in raw_nar.cues[:16]:
                cid_cue = raw_cue.cue_id.strip()
                anch = raw_cue.anchor.strip()
                if (
                    not _SAFE_ID_RE.fullmatch(cid_cue)
                    or anch not in anchor_set
                    or _contains_unsafe_code(raw_cue.spoken_phrase)
                ):
                    continue
                valid_cues.append(
                    NarrationCueSpec(
                        cue_id=cid_cue,
                        anchor=anch,
                        spoken_phrase=raw_cue.spoken_phrase.strip(),
                        offset_hint_seconds=raw_cue.offset_hint_seconds,
                    )
                )
                cues_by_scene.setdefault(sc_id, []).append(cid_cue)

            cids = tuple(
                cid.strip() for cid in raw_nar.concept_ids if cid.strip() in approved_cids
            ) or target_scene.concept_ids
            bindings = _verify_ev(raw_nar.evidence_ids, fallback_concept_id=cids[0])
            grounding, interp, review_req = _classify(
                script, bindings, kind_label="pedagogical_adaptation"
            )
            dur = float(raw_nar.estimated_duration_seconds)
            dur = min(120.0, max(1.0, dur if math.isfinite(dur) else target_scene.duration_seconds))
            narrations.append(
                BlueprintNarrationSegment(
                    segment_id=f"bp-nar-{len(narrations) + 1}",
                    order_index=target_scene.order_index,
                    scene_id=sc_id,
                    concept_ids=cids,
                    script_text=script,
                    caption_text=caption,
                    estimated_duration_seconds=dur,
                    cues=tuple(valid_cues),
                    grounding=grounding,
                    interpretation_label=interp,
                    evidence_bindings=bindings,
                    needs_review=review_req,
                )
            )
        narrations.sort(key=lambda n: n.order_index)
        narrations = [
            n.model_copy(update={"order_index": idx})
            for idx, n in enumerate(narrations, start=1)
        ]
        final_scenes = tuple(
            sc.model_copy(
                update={
                    "narration_cue_ids": tuple(dict.fromkeys(cues_by_scene.get(sc.scene_id, [])))
                }
            )
            for sc in ordered_scenes
        )

        # 6. Formative assessments with strict answer-key validation
        assessments: list[BlueprintAssessmentItem] = []
        for raw_q in raw.assessments[:20]:
            cid = raw_q.concept_id.strip()
            if cid not in approved_cids or _contains_unsafe_code(
                raw_q.stem,
                raw_q.correct_answer,
                raw_q.explanation,
                *raw_q.options,
                *raw_q.accepted_aliases,
            ):
                rejected_assessments += 1
                continue
            stem = raw_q.stem.strip()
            explanation = raw_q.explanation.strip()
            if not stem or not explanation:
                rejected_assessments += 1
                continue

            if raw_q.question_type == "mcq":
                clean_opts = tuple(o.strip()[:300] for o in raw_q.options if o.strip())
                if (
                    len(clean_opts) < 2
                    or len(clean_opts) > 6
                    or len({o.casefold() for o in clean_opts}) != len(clean_opts)
                    or raw_q.correct_option_index is None
                    or not (0 <= raw_q.correct_option_index < len(clean_opts))
                ):
                    rejected_assessments += 1
                    continue
                indexed_answer = clean_opts[raw_q.correct_option_index]
                declared = raw_q.correct_answer.strip()
                if declared and declared.casefold() != indexed_answer.casefold():
                    rejected_assessments += 1
                    continue
                canonical_answer = indexed_answer
                opt_index: int | None = raw_q.correct_option_index
                aliases: tuple[str, ...] = ()
                grading_method = "deterministic_mcq"
            else:
                declared = raw_q.correct_answer.strip()
                if not declared or raw_q.options or raw_q.correct_option_index is not None:
                    rejected_assessments += 1
                    continue
                clean_opts = ()
                opt_index = None
                canonical_answer = declared
                aliases = tuple(dict.fromkeys(a.strip() for a in raw_q.accepted_aliases if a.strip()))
                grading_method = (
                    "deterministic_exact"
                    if raw_q.question_type == "short_answer"
                    else "formative_rubric"
                )

            bindings = _verify_ev(raw_q.evidence_ids, fallback_concept_id=cid)
            grounding, interp, review_req = _classify(
                stem, bindings, kind_label="pedagogical_adaptation"
            )
            assessments.append(
                BlueprintAssessmentItem(
                    question_id=f"bp-q-{len(assessments) + 1}",
                    concept_id=cid,
                    objective_ids=tuple(obj_ids_by_cid.get(cid, [])[:2]),
                    question_type=raw_q.question_type,
                    difficulty=raw_q.difficulty,
                    stem=stem,
                    options=clean_opts,
                    correct_option_index=opt_index,
                    correct_answer=canonical_answer,
                    accepted_aliases=aliases,
                    explanation=explanation,
                    grading_method=grading_method,  # type: ignore[arg-type]
                    answer_key_verified=True,
                    grounding=grounding,
                    interpretation_label=interp,
                    evidence_bindings=bindings,
                    needs_review=review_req,
                )
            )

        for w in raw.warnings:
            if not _contains_unsafe_code(w):
                _add_note(w)
        if rejected_ev_refs:
            _add_note(f"Rejected {rejected_ev_refs} unauthorized or fabricated evidence reference(s).")
        if rejected_scenes:
            _add_note(f"Rejected {rejected_scenes} invalid or unsafe visual scene specification(s).")
        if rejected_scene_cmds:
            _add_note(f"Rejected {rejected_scene_cmds} invalid or unknown scene object/action command(s).")
        if rejected_assessments:
            _add_note(f"Rejected {rejected_assessments} assessment item(s) with invalid or inconsistent answer keys.")

        all_ev_verified = (
            not is_topic
            and bool(explanations)
            and all(bool(e.evidence_bindings) for e in explanations)
            and rejected_ev_refs == 0
        )
        summary = (
            raw.lesson_summary.strip()
            if raw.lesson_summary.strip() and not _contains_unsafe_code(raw.lesson_summary)
            else f"Structured lesson blueprint for {outline.title} ({len(explanations)} ordered concept steps)."
        )

        return LessonBlueprint(
            blueprint_schema_version=BLUEPRINT_SCHEMA_VERSION,
            source_id=reviewed.source_id,
            version_id=reviewed.version_id,
            derived_extraction_id=reviewed.derived_extraction_id,
            concept_extraction_id=approved_review.concept_extraction_id,
            approved_review_id=approved_review.review_id,
            source_sha256=reviewed.source_sha256,
            input_format=reviewed.input_format,
            title=reviewed.title,
            lesson_summary=summary[:1600],
            learning_request_only=is_topic,
            prerequisite_order=ordered_cids,
            learning_objectives=tuple(objectives),
            explanations=tuple(explanations),
            worked_examples=tuple(worked_examples),
            scenes=final_scenes,
            narration_language="en",
            tts_synthesized=False,
            narrations=tuple(narrations),
            assessments=tuple(assessments),
            validation=BlueprintValidationReport(
                outline_version=outline.outline_version,
                prerequisite_order_preserved=True,
                learning_request_only=is_topic,
                all_evidence_verified=all_ev_verified,
                rejected_evidence_refs_count=rejected_ev_refs,
                rejected_scenes_count=rejected_scenes,
                rejected_scene_commands_count=rejected_scene_cmds,
                rejected_assessment_items_count=rejected_assessments,
                requires_teacher_review=True,
                notes=tuple(notes),
            ),
            created_at=datetime.now(UTC),
        )


def project_published_blueprint(
    record: LessonBlueprintRecord,
    *,
    published_at: datetime,
) -> PublishedLessonBlueprint:
    """Create a student-safe published blueprint projection with answer keys removed."""
    bp = record.blueprint
    safe_assessments = tuple(
        StudentSafeAssessmentItem(
            question_id=q.question_id,
            concept_id=q.concept_id,
            objective_ids=q.objective_ids,
            question_type=q.question_type,
            difficulty=q.difficulty,
            stem=q.stem,
            options=q.options,
            grading_method=q.grading_method,
            grounding=q.grounding,
            interpretation_label=q.interpretation_label,
            evidence_bindings=q.evidence_bindings,
        )
        for q in bp.assessments
    )
    return PublishedLessonBlueprint(
        blueprint_id=record.blueprint_id,
        tenant_id=record.tenant_id,
        source_id=record.source_id,
        version_id=record.version_id,
        concept_extraction_id=record.concept_extraction_id,
        approved_review_id=record.approved_review_id,
        blueprint_version=record.blueprint_version,
        source_sha256=record.source_sha256,
        title=bp.title,
        lesson_summary=bp.lesson_summary,
        learning_request_only=bp.learning_request_only,
        prerequisite_order=bp.prerequisite_order,
        learning_objectives=bp.learning_objectives,
        explanations=bp.explanations,
        worked_examples=bp.worked_examples,
        scenes=bp.scenes,
        narration_language=bp.narration_language,
        tts_synthesized=False,
        narrations=bp.narrations,
        assessments=safe_assessments,
        validation=bp.validation,
        published_at=published_at,
    )
