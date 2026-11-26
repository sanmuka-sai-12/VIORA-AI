import type { OfferingSourcePolicyInfo, SourceSummary } from "../../api/client";

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
