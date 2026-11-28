import type {
  LearningPromptMode,
  PersonalLearningAttachment,
  PersonalLearningAttachmentRef,
  PersonalLearningSourceRef,
} from "../../api/client";
import type { TenantController } from "../../tenant/tenantState";

export interface LearningAttachmentEntry {
  attachment: PersonalLearningAttachment;
  included: boolean;
}

export interface LearningChatEntry {
  id: string;
  role: "user" | "assistant";
  content: string;
  mode?: LearningPromptMode;
  attachedTitles?: string[];
  followUps?: string[];
  sources?: PersonalLearningSourceRef[];
  truncated?: boolean;
}

export interface LearningSessionSnapshot {
  userId: string;
  tenantId: string;
  mode: LearningPromptMode;
  prompt: string;
  messages: LearningChatEntry[];
  attachments: LearningAttachmentEntry[];
}

export function toActiveAttachmentRefs(
  entries: readonly LearningAttachmentEntry[],
): PersonalLearningAttachmentRef[] {
  return entries
    .filter((item) => item.included && item.attachment.status !== "unreadable")
    .slice(0, 5)
    .map((item) => ({
      attachment_id: item.attachment.attachment_id,
      doc_sha256: item.attachment.doc_sha256,
    }));
}

export function formatSourcePageLabel(source: PersonalLearningSourceRef): string {
  return source.page_start === source.page_end
    ? `p. ${source.page_start}`
    : `pp. ${source.page_start}-${source.page_end}`;
}

export function learningSessionCacheKey(
  userId: string,
  tenantId: string,
): string {
  return `personal_learning:${userId}:${tenantId}`;
}

export function loadLearningSession(
  tenantController: Pick<TenantController, "getCached">,
  userId: string | undefined,
  tenantId: string | null,
): LearningSessionSnapshot | null {
  if (!userId || !tenantId) {
    return null;
  }
  const key = learningSessionCacheKey(userId, tenantId);
  const cached = tenantController.getCached<LearningSessionSnapshot>(key);
  if (!cached || cached.userId !== userId || cached.tenantId !== tenantId) {
    return null;
  }
  return cached;
}

export function saveLearningSession(
  tenantController: Pick<TenantController, "setCached">,
  snapshot: LearningSessionSnapshot,
): void {
  const key = learningSessionCacheKey(snapshot.userId, snapshot.tenantId);
  tenantController.setCached(key, snapshot);
}

export function clearLearningSession(
  tenantController: Pick<TenantController, "setCached">,
  userId: string,
  tenantId: string,
): void {
  const key = learningSessionCacheKey(userId, tenantId);
  tenantController.setCached(key, {
    userId,
    tenantId,
    mode: "question",
    prompt: "",
    messages: [],
    attachments: [],
  } satisfies LearningSessionSnapshot);
}
