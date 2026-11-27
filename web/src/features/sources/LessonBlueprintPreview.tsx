import type {
  BlueprintAssessmentItem,
  LessonBlueprintApprovalState,
  LessonBlueprintRecord,
  LessonBlueprintSummary,
  PublishedLessonBlueprint,
  StudentSafeAssessmentItem,
} from "../../api/client";
import {
  AlertBanner,
  DataTable,
  StatusBadge,
} from "../../components/common/UiPrimitives";
import {
  formatInterpretationLabel,
  groundingTone,
  reviewTone,
} from "./sourceHelpers";

type PreviewProps =
  | {
      mode: "teacher";
      record: LessonBlueprintRecord;
      approvalState: LessonBlueprintApprovalState | null;
      versions: readonly LessonBlueprintSummary[];
    }
  | {
      mode: "student";
      published: PublishedLessonBlueprint;
    };

function formatEvidenceList(
  bindings: readonly { evidence_id: string; page_number: number }[],
): string {
  return bindings.length > 0
    ? bindings.map((b) => `${b.evidence_id} (p.${b.page_number})`).join(", ")
    : "None";
}

export function LessonBlueprintPreview(props: PreviewProps) {
  const bp = props.mode === "teacher" ? props.record.blueprint : props.published;
  const blueprintVersion =
    props.mode === "teacher"
      ? props.record.blueprint_version
      : props.published.blueprint_version;

  return (
    <div
      className="viora-stack"
      style={{ borderTop: "1px solid var(--color-border)", paddingTop: "0.75rem" }}
    >
      <div className="viora-row" style={{ justifyContent: "space-between" }}>
        <strong>
          Lesson Blueprint Preview (v{blueprintVersion}): {bp.title}
        </strong>
        <div className="viora-row">
          {props.mode === "teacher" && props.approvalState ? (
            <>
              <StatusBadge tone={reviewTone(props.approvalState.approval_status)}>
                Blueprint Approval: {props.approvalState.approval_status}
              </StatusBadge>
              <StatusBadge
                tone={props.approvalState.published_to_students ? "success" : "neutral"}
              >
                {props.approvalState.published_to_students
                  ? "Published to Students"
                  : "Unpublished"}
              </StatusBadge>
            </>
          ) : props.mode === "teacher" ? (
            <StatusBadge tone="warning">Blueprint Approval: pending_review</StatusBadge>
          ) : (
            <>
              <StatusBadge tone="success">Teacher Approved Lesson</StatusBadge>
              <StatusBadge tone="success">Published to Students</StatusBadge>
            </>
          )}
          <StatusBadge tone={bp.validation.all_evidence_verified ? "success" : "warning"}>
            All Evidence Verified: {String(bp.validation.all_evidence_verified)}
          </StatusBadge>
          <StatusBadge tone={bp.validation.prerequisite_order_preserved ? "success" : "warning"}>
            Prerequisite Order Preserved
          </StatusBadge>
          {bp.learning_request_only && (
            <StatusBadge tone="warning">
              Learning Request Only (Non-Evidentiary)
            </StatusBadge>
          )}
        </div>
      </div>

      <p style={{ fontSize: "0.875rem" }}>{bp.lesson_summary}</p>

      {props.mode === "teacher" && (
        <p style={{ fontSize: "0.8rem", color: "var(--color-text-muted)" }}>
          Provider: <code>{props.record.provider}</code> • Model:{" "}
          <code>{props.record.model}</code> • Tokens: {props.record.total_tokens} •
          Versions stored: {props.versions.length || 1} • Executable scene code blocked:{" "}
          {String(bp.validation.executable_scene_code_blocked)} • TTS synthesized:{" "}
          {String(bp.tts_synthesized)}
        </p>
      )}

      {(bp.validation.rejected_evidence_refs_count > 0 ||
        bp.validation.rejected_scenes_count > 0 ||
        bp.validation.rejected_scene_commands_count > 0 ||
        bp.validation.rejected_assessment_items_count > 0) && (
        <AlertBanner
          tone="warning"
          message={`Safety & grounding filters rejected: ${bp.validation.rejected_evidence_refs_count} evidence ref(s), ${bp.validation.rejected_scenes_count} scene(s), ${bp.validation.rejected_scene_commands_count} scene command(s), ${bp.validation.rejected_assessment_items_count} assessment item(s).`}
        />
      )}

      {bp.learning_objectives.length > 0 && (
        <div className="viora-stack" style={{ gap: "0.4rem" }}>
          <strong>Learning Objectives ({bp.learning_objectives.length})</strong>
          <DataTable
            rows={bp.learning_objectives}
            rowKey={(o) => o.objective_id}
            columns={[
              { key: "bloom", header: "Bloom", render: (o) => o.bloom_level },
              { key: "stmt", header: "Objective", render: (o) => o.statement },
              {
                key: "label",
                header: "Grounding / Label",
                render: (o) => (
                  <div className="viora-row">
                    <StatusBadge tone={groundingTone(o.grounding)}>
                      {o.grounding}
                    </StatusBadge>
                    <StatusBadge tone="neutral">
                      {formatInterpretationLabel(o.interpretation_label)}
                    </StatusBadge>
                  </div>
                ),
              },
              {
                key: "ev",
                header: "Provenance",
                render: (o) => formatEvidenceList(o.evidence_bindings),
              },
            ]}
          />
        </div>
      )}

      {bp.explanations.length > 0 && (
        <div className="viora-stack" style={{ gap: "0.5rem" }}>
          <strong>Ordered Explanations ({bp.explanations.length})</strong>
          {bp.explanations.map((step) => (
            <div
              key={`${step.step_index}-${step.concept_id}`}
              style={{
                padding: "0.75rem",
                border: "1px solid var(--color-border)",
                borderRadius: "var(--radius-sm)",
              }}
            >
              <div className="viora-row" style={{ justifyContent: "space-between" }}>
                <strong>
                  Step {step.step_index}: {step.concept_name}
                </strong>
                <div className="viora-row">
                  <StatusBadge tone={groundingTone(step.grounding)}>
                    {step.grounding}
                  </StatusBadge>
                  <StatusBadge tone="neutral">
                    {formatInterpretationLabel(step.interpretation_label)}
                  </StatusBadge>
                  <StatusBadge tone={step.supported_by_source ? "success" : "warning"}>
                    {step.supported_by_source ? "Source-Supported" : "Not Verbatim Source"}
                  </StatusBadge>
                </div>
              </div>
              <p style={{ fontSize: "0.875rem", marginTop: "0.35rem" }}>
                {step.explanation}
              </p>
              {step.key_points.length > 0 && (
                <p style={{ fontSize: "0.8rem", color: "var(--color-text-muted)" }}>
                  Key points: {step.key_points.join(" • ")}
                </p>
              )}
              {step.formulas.length > 0 && (
                <p style={{ fontSize: "0.8rem" }}>
                  Formulas: <code>{step.formulas.join(" ; ")}</code>
                </p>
              )}
              <p style={{ fontSize: "0.78rem", color: "var(--color-text-muted)" }}>
                Evidence: {formatEvidenceList(step.evidence_bindings)}
              </p>
            </div>
          ))}
        </div>
      )}

      {bp.worked_examples.length > 0 && (
        <div className="viora-stack" style={{ gap: "0.5rem" }}>
          <strong>Worked Examples ({bp.worked_examples.length})</strong>
          {bp.worked_examples.map((ex) => (
            <div
              key={ex.example_id}
              style={{
                padding: "0.75rem",
                border: "1px solid var(--color-border)",
                borderRadius: "var(--radius-sm)",
              }}
            >
              <div className="viora-row" style={{ justifyContent: "space-between" }}>
                <strong>{ex.title}</strong>
                <StatusBadge tone={groundingTone(ex.grounding)}>
                  {formatInterpretationLabel(ex.interpretation_label)}
                </StatusBadge>
              </div>
              <p style={{ fontSize: "0.85rem", marginTop: "0.3rem" }}>
                {ex.problem_statement}
              </p>
              <ol style={{ marginTop: "0.3rem", paddingLeft: "1.25rem", fontSize: "0.84rem" }}>
                {ex.steps.map((s, idx) => (
                  <li key={`${ex.example_id}-s-${idx}`}>{s}</li>
                ))}
              </ol>
              <p style={{ fontSize: "0.84rem", marginTop: "0.25rem" }}>
                <strong>Result:</strong> {ex.final_answer} • Provenance:{" "}
                {formatEvidenceList(ex.evidence_bindings)}
              </p>
            </div>
          ))}
        </div>
      )}

      {bp.scenes.length > 0 && (
        <div className="viora-stack" style={{ gap: "0.4rem" }}>
          <strong>Typed Visual Scene Specifications ({bp.scenes.length})</strong>
          <DataTable
            rows={bp.scenes}
            rowKey={(s) => s.scene_id}
            columns={[
              {
                key: "scene",
                header: "Scene / Template",
                render: (s) => (
                  <div>
                    <strong>#{s.order_index} {s.scene_id}</strong>{" "}
                    <code>{s.template}</code>
                  </div>
                ),
              },
              { key: "intent", header: "Visual Intent", render: (s) => s.visual_intent },
              {
                key: "elements",
                header: "Typed Spec",
                render: (s) =>
                  `${s.objects.length} object(s), ${s.actions.length} action(s), ${s.duration_seconds}s, anchors=[${s.timing_anchors.join(", ")}]`,
              },
              {
                key: "label",
                header: "Label",
                render: (s) => (
                  <StatusBadge tone={groundingTone(s.grounding)}>
                    {formatInterpretationLabel(s.interpretation_label)}
                  </StatusBadge>
                ),
              },
            ]}
          />
        </div>
      )}

      {bp.narrations.length > 0 && (
        <div className="viora-stack" style={{ gap: "0.4rem" }}>
          <strong>Narration Script & Timeline Cues ({bp.narrations.length})</strong>
          <DataTable
            rows={bp.narrations}
            rowKey={(n) => n.segment_id}
            columns={[
              { key: "scene", header: "Scene", render: (n) => <code>{n.scene_id}</code> },
              { key: "script", header: "Script", render: (n) => n.script_text },
              { key: "caption", header: "Caption", render: (n) => n.caption_text },
              {
                key: "cues",
                header: "Cues",
                render: (n) =>
                  n.cues.length > 0
                    ? n.cues.map((c) => `${c.cue_id}@${c.anchor}`).join(", ")
                    : "None",
              },
            ]}
          />
        </div>
      )}

      {bp.assessments.length > 0 && (
        <div className="viora-stack" style={{ gap: "0.4rem" }}>
          <strong>
            Formative Assessments ({bp.assessments.length})
            {props.mode === "student" ? " — Student Practice (Answer Keys Isolated)" : ""}
          </strong>
          {props.mode === "teacher" ? (
            <DataTable
              rows={bp.assessments as BlueprintAssessmentItem[]}
              rowKey={(q) => q.question_id}
              columns={[
                {
                  key: "type",
                  header: "Type",
                  render: (q) => `${q.question_type} (${q.difficulty})`,
                },
                {
                  key: "stem",
                  header: "Question & Options",
                  render: (q) => (
                    <div>
                      <div>{q.stem}</div>
                      {q.options.length > 0 && (
                        <div style={{ fontSize: "0.8rem", color: "var(--color-text-muted)" }}>
                          Options: {q.options.map((o, i) => `[${i}] ${o}`).join(" | ")}
                        </div>
                      )}
                    </div>
                  ),
                },
                {
                  key: "key",
                  header: "Verified Answer Key (Teacher Only)",
                  render: (q) => (
                    <div>
                      <code>{q.correct_answer}</code>
                      {q.accepted_aliases.length > 0 && (
                        <div style={{ fontSize: "0.78rem" }}>
                          Aliases: {q.accepted_aliases.join(", ")}
                        </div>
                      )}
                      <div style={{ fontSize: "0.78rem", color: "var(--color-text-muted)" }}>
                        {q.explanation}
                      </div>
                    </div>
                  ),
                },
                {
                  key: "prov",
                  header: "Provenance",
                  render: (q) => formatEvidenceList(q.evidence_bindings),
                },
              ]}
            />
          ) : (
            <DataTable
              rows={bp.assessments as StudentSafeAssessmentItem[]}
              rowKey={(q) => q.question_id}
              columns={[
                {
                  key: "type",
                  header: "Type",
                  render: (q) => `${q.question_type} (${q.difficulty})`,
                },
                {
                  key: "stem",
                  header: "Practice Question",
                  render: (q) => (
                    <div>
                      <div>{q.stem}</div>
                      {q.options.length > 0 && (
                        <ul style={{ marginTop: "0.25rem", paddingLeft: "1.1rem", fontSize: "0.82rem" }}>
                          {q.options.map((opt) => (
                            <li key={`${q.question_id}-${opt}`}>{opt}</li>
                          ))}
                        </ul>
                      )}
                    </div>
                  ),
                },
                {
                  key: "label",
                  header: "Grounding / Label",
                  render: (q) => (
                    <div className="viora-row">
                      <StatusBadge tone={groundingTone(q.grounding)}>
                        {q.grounding}
                      </StatusBadge>
                      <StatusBadge tone="neutral">
                        {formatInterpretationLabel(q.interpretation_label)}
                      </StatusBadge>
                    </div>
                  ),
                },
                {
                  key: "prov",
                  header: "Provenance",
                  render: (q) => formatEvidenceList(q.evidence_bindings),
                },
              ]}
            />
          )}
        </div>
      )}
    </div>
  );
}
