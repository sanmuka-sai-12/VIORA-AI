import type {
  KnowledgePublicationState,
  KnowledgeQARequest,
  KnowledgeQAResult,
  LessonBlueprintRecord,
  OfferingSourcePolicyInfo,
  SourceSummary,
} from "../../api/client";

export type BadgeTone = "success" | "warning" | "danger" | "neutral" | "brand";

export function groundingTone(grounding: string): BadgeTone {
  if (grounding === "source_supported") return "success";
  if (grounding === "source_synthesized") return "brand";
  if (grounding === "learning_request" || grounding === "inferred") return "warning";
  return "danger";
}

export function jobStateTone(state: string): BadgeTone {
  if (state === "succeeded") return "success";
  if (state === "failed" || state === "cancelled") return "danger";
  if (state === "retry_wait") return "warning";
  return "brand";
}

export function reviewTone(status: string): BadgeTone {
  if (status === "approved") return "success";
  if (status === "rejected") return "danger";
  return "warning";
}

export function qaStatusTone(status: KnowledgeQAResult["status"]): BadgeTone {
  if (status === "answered") return "success";
  if (status === "interpreted") return "brand";
  if (status === "clarification_needed") return "warning";
  return "danger";
}

const INTERPRETATION_LABELS: Record<string, string> = {
  verbatim_source: "Verbatim Source",
  synthesized_source: "Synthesized Source",
  pedagogical_adaptation: "Pedagogical Adaptation",
  worked_derivation: "Worked Derivation",
  syllabus_request: "Syllabus Learning Request (Non-Evidentiary)",
  unverified_draft: "Unverified Draft (Needs Review)",
};

export function formatInterpretationLabel(label: string): string {
  return INTERPRETATION_LABELS[label] ?? label;
}

export function canTeacherManageSource(
  isSubjectTeacher: boolean,
  source: Pick<SourceSummary, "origin_kind" | "lifecycle_state">,
): boolean {
  return (
    isSubjectTeacher &&
    source.origin_kind === "teacher_resource" &&
    source.lifecycle_state === "active"
  );
}

export function canTriggerLessonBlueprint(
  isSubjectTeacher: boolean,
  source: Pick<SourceSummary, "origin_kind" | "lifecycle_state">,
  pubState: KnowledgePublicationState | null,
): boolean {
  return Boolean(
    canTeacherManageSource(isSubjectTeacher, source) && pubState?.teacher_approved,
  );
}

export function canApproveLessonBlueprint(
  isSubjectTeacher: boolean,
  source: Pick<SourceSummary, "origin_kind" | "lifecycle_state">,
  pubState: KnowledgePublicationState | null,
  blueprint: LessonBlueprintRecord | null,
): boolean {
  return Boolean(
    canTriggerLessonBlueprint(isSubjectTeacher, source, pubState) && blueprint,
  );
}

export function hasExposedAnswerKeyFields(item: Record<string, unknown>): boolean {
  return (
    "correct_answer" in item ||
    "correct_option_index" in item ||
    "accepted_aliases" in item ||
    "answer_key_verified" in item
  );
}

export function buildKnowledgeQABody(input: {
  question: string;
  offeringId: string;
  mode: KnowledgeQARequest["mode"];
  allowInterpretation: boolean;
  allowGeneralKnowledge: boolean;
  publishedOnly: boolean;
}): KnowledgeQARequest {
  return {
    question: input.question.trim(),
    offering_id: input.offeringId || null,
    mode: input.mode,
    top_k: 5,
    candidate_k: 20,
    max_context_tokens: 1800,
    max_graph_hops: 2,
    include_student_submissions: !input.publishedOnly,
    published_only: input.publishedOnly,
    allow_general_knowledge: input.allowGeneralKnowledge,
    allow_interpretation: input.allowInterpretation,
  };
}

export function canOwnerStudentProcess(
  actorUserId: string | undefined,
  source: Pick<SourceSummary, "origin_kind" | "owner_user_id" | "lifecycle_state">,
  policy: OfferingSourcePolicyInfo | null,
): boolean {
  return Boolean(
    actorUserId &&
      source.origin_kind === "student_submission" &&
      source.owner_user_id === actorUserId &&
      source.lifecycle_state === "active" &&
      policy?.student_submissions_enabled,
  );
}

export function buildSourceUploadFormData(fields: {
  title: string;
  file: File;
  inputFormat?: string;
  originKind?: string;
  visibility?: string;
  idempotencyKey?: string;
}): FormData {
  const fd = new FormData();
  fd.append("title", fields.title.trim());
  fd.append("file", fields.file);
  if (fields.inputFormat) fd.append("input_format", fields.inputFormat);
  if (fields.originKind) fd.append("origin_kind", fields.originKind);
  if (fields.visibility) fd.append("visibility", fields.visibility);
  if (fields.idempotencyKey) fd.append("idempotency_key", fields.idempotencyKey);
  return fd;
}
