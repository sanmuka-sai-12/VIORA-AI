import type { LearningPromptMode } from "../../api/client";
import type { TenantController } from "../../tenant/tenantState";

export interface LearningChatEntry {
  id: string;
  role: "user" | "assistant";
  content: string;
  mode?: LearningPromptMode;
  followUps?: string[];
  truncated?: boolean;
}

export interface LearningSessionSnapshot {
  userId: string;
  tenantId: string;
  mode: LearningPromptMode;
  prompt: string;
  messages: LearningChatEntry[];
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
  } satisfies LearningSessionSnapshot);
}
