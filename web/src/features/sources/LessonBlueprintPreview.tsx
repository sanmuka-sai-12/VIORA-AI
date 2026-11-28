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
          {props.mode === "teacher"
            ? `Lesson Blueprint (v${blueprintVersion}): ${bp.title}`
            : bp.title}
        </strong>
        <div className="viora-row">
          {props.mode === "teacher" && props.approvalState ? (
            <>
              <StatusBadge tone={reviewTone(props.approvalState.approval_status)}>
                Approval: {props.approvalState.approval_status}
              </StatusBadge>
              <StatusBadge
                tone={props.approvalState.published_to_students ? "success" : "neutral"}
              >
                {props.approvalState.published_to_students
                  ? "Published to Class"
                  : "Draft"}
              </StatusBadge>
            </>
          ) : props.mode === "teacher" ? (
            <StatusBadge tone="warning">Pending Review</StatusBadge>
          ) : (
            <StatusBadge tone="success">Teacher Approved Lesson</StatusBadge>
          )}
          {bp.learning_request_only && (
            <StatusBadge tone="warning">
              Study Guide (Topic Outline)
            </StatusBadge>
          )}
        </div>
      </div>

      <p style={{ fontSize: "0.9rem" }}>{bp.lesson_summary}</p>

      {bp.learning_objectives.length > 0 && (
        <div className="viora-stack" style={{ gap: "0.4rem" }}>
          <strong>Learning Objectives ({bp.learning_objectives.length})</strong>
          <DataTable
            rows={bp.learning_objectives}
            rowKey={(o) => o.objective_id}
            columns={[
              { key: "bloom", header: "Focus", render: (o) => o.bloom_level },
              { key: "stmt", header: "Objective", render: (o) => o.statement },
            ]}
          />
        </div>
      )}

      {bp.explanations.length > 0 && (
        <div className="viora-stack" style={{ gap: "0.5rem" }}>
          <strong>Lesson Walkthrough ({bp.explanations.length})</strong>
          {bp.explanations.map((step) => (
            <div
              key={`${step.step_index}-${step.concept_id}`}
              style={{
                padding: "0.75rem",
                border: "1px solid var(--color-border)",
                borderRadius: "var(--radius-sm)",
              }}
            >
              <strong>
                Step {step.step_index}: {step.concept_name}
              </strong>
              <p style={{ fontSize: "0.875rem", marginTop: "0.35rem" }}>
                {step.explanation}
              </p>
              {step.key_points.length > 0 && (
                <p style={{ fontSize: "0.8rem", color: "var(--color-text-muted)", marginTop: "0.25rem" }}>
                  Key points: {step.key_points.join(" • ")}
                </p>
              )}
              {step.formulas.length > 0 && (
                <p style={{ fontSize: "0.8rem", marginTop: "0.25rem" }}>
                  Formulas: <code>{step.formulas.join(" ; ")}</code>
                </p>
              )}
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
              <strong>{ex.title}</strong>
              <p style={{ fontSize: "0.85rem", marginTop: "0.3rem" }}>
                {ex.problem_statement}
              </p>
              <ol style={{ marginTop: "0.3rem", paddingLeft: "1.25rem", fontSize: "0.84rem" }}>
                {ex.steps.map((s, idx) => (
                  <li key={`${ex.example_id}-s-${idx}`}>{s}</li>
                ))}
              </ol>
              <p style={{ fontSize: "0.84rem", marginTop: "0.25rem" }}>
                <strong>Result:</strong> {ex.final_answer}
              </p>
            </div>
          ))}
        </div>
      )}

      {bp.scenes.length > 0 && (
        <div className="viora-stack" style={{ gap: "0.4rem" }}>
          <strong>Visual Lesson Segments ({bp.scenes.length})</strong>
          <DataTable
            rows={bp.scenes}
            rowKey={(s) => s.scene_id}
            columns={[
              {
                key: "scene",
                header: "Segment",
                render: (s) => <strong>#{s.order_index}</strong>,
              },
              { key: "intent", header: "Visual Focus", render: (s) => s.visual_intent },
              {
                key: "duration",
                header: "Duration",
                render: (s) => `${s.duration_seconds}s`,
              },
            ]}
          />
        </div>
      )}

      {bp.narrations.length > 0 && (
        <div className="viora-stack" style={{ gap: "0.4rem" }}>
          <strong>Lesson Narration ({bp.narrations.length})</strong>
          <DataTable
            rows={bp.narrations}
            rowKey={(n) => n.segment_id}
            columns={[
              { key: "script", header: "Narration", render: (n) => n.script_text },
              { key: "caption", header: "Caption", render: (n) => n.caption_text },
            ]}
          />
        </div>
      )}

      {bp.assessments.length > 0 && (
        <div className="viora-stack" style={{ gap: "0.4rem" }}>
          <strong>
            {props.mode === "student"
              ? `Practice Questions (${bp.assessments.length})`
              : `Formative Assessments (${bp.assessments.length})`}
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
                  header: "Verified Answer Key",
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
              ]}
            />
          )}
        </div>
      )}

      <details className="viora-details">
        <summary>
          {props.mode === "teacher"
            ? "Advanced Verification & Technical Details"
            : "Source Verification Details"}
        </summary>
        <div className="viora-stack" style={{ gap: "0.65rem" }}>
          <div className="viora-row">
            <StatusBadge tone={bp.validation.all_evidence_verified ? "success" : "warning"}>
              All Evidence Verified: {String(bp.validation.all_evidence_verified)}
            </StatusBadge>
            <StatusBadge tone={bp.validation.prerequisite_order_preserved ? "success" : "warning"}>
              Prerequisite Order Preserved
            </StatusBadge>
          </div>

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
            <div style={{ fontSize: "0.8rem", color: "var(--color-text-muted)" }}>
              <strong>Objective Grounding:</strong>{" "}
              {bp.learning_objectives
                .map(
                  (o) =>
                    `${o.objective_id} (${formatInterpretationLabel(o.interpretation_label)}: ${formatEvidenceList(o.evidence_bindings)})`,
                )
                .join(" • ")}
            </div>
          )}

          {bp.explanations.length > 0 && (
            <div className="viora-row" style={{ gap: "0.4rem" }}>
              {bp.explanations.map((step) => (
                <StatusBadge key={`diag-${step.step_index}`} tone={groundingTone(step.grounding)}>
                  Step {step.step_index}: {formatInterpretationLabel(step.interpretation_label)} (
                  {formatEvidenceList(step.evidence_bindings)})
                </StatusBadge>
              ))}
            </div>
          )}
        </div>
      </details>
    </div>
  );
}
