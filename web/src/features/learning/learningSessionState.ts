import type {
  LearningPromptMode,
  PersonalLearningAttachment,
  PersonalLearningAttachmentRef,
  PersonalLearningSessionDetail,
  PersonalLearningSourceRef,
  PersonalLearningStudyResource,
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
  sessionId: string | null;
  sessionTitle: string;
  mode: LearningPromptMode;
  prompt: string;
  messages: LearningChatEntry[];
  attachments: LearningAttachmentEntry[];
  resources: PersonalLearningStudyResource[];
}

export const NEW_CHAT_SENTINEL = "__new__";

export function activeSessionStorageKey(userId: string, tenantId: string): string {
  return `viora.pl.activeSession:${userId}:${tenantId}`;
}

export function readActiveSessionHint(
  userId: string | undefined,
  tenantId: string | null,
): string | null {
  if (!userId || !tenantId || typeof window === "undefined") {
    return null;
  }
  try {
    return window.sessionStorage.getItem(activeSessionStorageKey(userId, tenantId));
  } catch {
    return null;
  }
}

export function writeActiveSessionHint(
  userId: string | undefined,
  tenantId: string | null,
  value: string | null,
): void {
  if (!userId || !tenantId || typeof window === "undefined") {
    return;
  }
  try {
    const key = activeSessionStorageKey(userId, tenantId);
    if (value === null) {
      window.sessionStorage.removeItem(key);
    } else {
      window.sessionStorage.setItem(key, value);
    }
  } catch {
    // Ignore storage unavailability in restricted browser modes.
  }
}

export function clearAllLearningSessionHints(): void {
  if (typeof window === "undefined") {
    return;
  }
  try {
    const keys: string[] = [];
    for (let i = 0; i < window.sessionStorage.length; i += 1) {
      const k = window.sessionStorage.key(i);
      if (k && k.startsWith("viora.pl.activeSession:")) {
        keys.push(k);
      }
    }
    for (const k of keys) {
      window.sessionStorage.removeItem(k);
    }
  } catch {
    // Ignore storage unavailability.
  }
}

export function mapSessionDetailToChatEntries(
  detail: PersonalLearningSessionDetail,
): LearningChatEntry[] {
  return detail.messages.map((m) => ({
    id: m.message_id,
    role: m.role,
    content: m.content,
    mode: m.mode,
    attachedTitles: m.attached_titles?.length ? m.attached_titles : undefined,
    followUps: m.follow_up_prompts?.length ? m.follow_up_prompts : undefined,
    sources: m.sources?.length ? m.sources : undefined,
    truncated: m.truncated,
  }));
}

export function mapSessionAttachments(
  attachments: readonly PersonalLearningAttachment[],
): LearningAttachmentEntry[] {
  return attachments.map((attachment) => ({
    attachment,
    included: attachment.status !== "unreadable",
  }));
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

export function toActiveAttachmentIds(
  entries: readonly LearningAttachmentEntry[],
): string[] {
  return entries
    .filter((item) => item.included && item.attachment.status !== "unreadable")
    .slice(0, 5)
    .map((item) => item.attachment.attachment_id);
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
    sessionId: null,
    sessionTitle: "New Study Session",
    mode: "question",
    prompt: "",
    messages: [],
    attachments: [],
    resources: [],
  } satisfies LearningSessionSnapshot);
}
