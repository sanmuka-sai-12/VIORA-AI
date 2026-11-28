import { useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from "react";
import type {
  LearningPromptMode,
  PersonalLearningTurn,
} from "../../api/client";
import { useAuth } from "../../auth/AuthContext";
import { RichAcademicContent } from "../../components/common/RichAcademicContent";
import {
  AlertBanner,
  PageHeader,
  StatusBadge,
} from "../../components/common/UiPrimitives";
import { useTenant } from "../../tenant/TenantContext";
import { useActionFeedback } from "../common/useActionFeedback";
import {
  clearLearningSession,
  loadLearningSession,
  saveLearningSession,
  type LearningChatEntry,
} from "./learningSessionState";

const STARTER_PROMPTS: ReadonlyArray<{
  label: string;
  prompt: string;
  mode: LearningPromptMode;
}> = [
  {
    label: "Calculus: Chain Rule",
    prompt:
      "Explain the chain rule in calculus with a step-by-step derivative example.",
    mode: "explain",
  },
  {
    label: "Algorithms: Binary Search",
    prompt:
      "Explain why binary search runs in O(log n) time and show clean Python code.",
    mode: "explain",
  },
  {
    label: "Algebra: Quadratic Formula",
    prompt: "How is the quadratic formula derived from completing the square?",
    mode: "question",
  },
  {
    label: "Physics: Newton's Second Law",
    prompt:
      "Explain Newton's second law F = ma with units and a worked force example.",
    mode: "explain",
  },
];

export function StudentLearningPage() {
  const { apiClient, snapshot: auth } = useAuth();
  const { snapshot: tenant, controller: tenantController } = useTenant();
  const userId = auth.user?.user_id;
  const tenantId = tenant.selectedTenantId;
  const feedback = useActionFeedback();

  const initial = loadLearningSession(tenantController, userId, tenantId);
  const [mode, setMode] = useState<LearningPromptMode>(
    () => initial?.mode ?? "question",
  );
  const [prompt, setPrompt] = useState(() => initial?.prompt ?? "");
  const [messages, setMessages] = useState<LearningChatEntry[]>(
    () => initial?.messages ?? [],
  );
  const [busy, setBusy] = useState(false);

  const nextIdRef = useRef((initial?.messages.length ?? 0) + 1);
  const threadEndRef = useRef<HTMLDivElement | null>(null);
  const loadedScopeRef = useRef<string | null>(
    userId && tenantId ? `${userId}:${tenantId}` : null,
  );

  // Reload scoped session if user or active institution changes while mounted.
  useEffect(() => {
    const scopeKey = userId && tenantId ? `${userId}:${tenantId}` : null;
    if (scopeKey === loadedScopeRef.current) {
      return;
    }
    loadedScopeRef.current = scopeKey;
    const restored = loadLearningSession(tenantController, userId, tenantId);
    setMode(restored?.mode ?? "question");
    setPrompt(restored?.prompt ?? "");
    setMessages(restored?.messages ?? []);
    nextIdRef.current = (restored?.messages.length ?? 0) + 1;
  }, [tenantController, tenantId, userId]);

  // Persist active conversation inside TenantController cache scoped to (userId, tenantId).
  useEffect(() => {
    if (!userId || !tenantId) {
      return;
    }
    saveLearningSession(tenantController, {
      userId,
      tenantId,
      mode,
      prompt,
      messages,
    });
  }, [messages, mode, prompt, tenantController, tenantId, userId]);

  useEffect(() => {
    threadEndRef.current?.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }, [messages.length, busy]);

  if (!tenantId || !userId) return null;

  const buildHistory = (entries: LearningChatEntry[]): PersonalLearningTurn[] =>
    entries.slice(-10).map((entry) => ({
      role: entry.role,
      content: entry.content,
    }));

  const submitPrompt = async (
    rawPrompt: string,
    selectedMode: LearningPromptMode,
  ) => {
    const trimmed = rawPrompt.trim();
    if (!trimmed || busy) return;

    const historyPayload = buildHistory(messages);
    const userEntry: LearningChatEntry = {
      id: `msg-${nextIdRef.current++}`,
      role: "user",
      content: trimmed,
      mode: selectedMode,
    };

    setMessages((prev) => [...prev, userEntry]);
    setPrompt("");

    const ok = await feedback.runApiAction({
      setBusy,
      call: () =>
        apiClient.POST("/api/v1/tenants/{tenant_id}/learning/ask", {
          params: { path: { tenant_id: tenantId } },
          body: {
            prompt: trimmed,
            mode: selectedMode,
            history: historyPayload,
          },
        }),
      fallbackError: "Unable to get a response right now. Please try again.",
      onSuccess: (data) => {
        setMessages((prev) => [
          ...prev,
          {
            id: `msg-${nextIdRef.current++}`,
            role: "assistant",
            content: data.answer,
            mode: data.mode,
            followUps: data.follow_up_prompts,
            truncated: data.truncated,
          },
        ]);
      },
    });

    if (!ok) {
      setMessages((prev) => prev.filter((item) => item.id !== userEntry.id));
      setPrompt(trimmed);
    }
  };

  const handleFormSubmit = (e: FormEvent) => {
    e.preventDefault();
    void submitPrompt(prompt, mode);
  };

  const handleComposerKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      void submitPrompt(prompt, mode);
    }
  };

  const handleNewConversation = () => {
    feedback.clearFeedback();
    setMessages([]);
    setPrompt("");
    clearLearningSession(tenantController, userId, tenantId);
  };

  const latestAssistantId = [...messages]
    .reverse()
    .find((m) => m.role === "assistant")?.id;

  return (
    <div className="viora-learning-shell">
      <PageHeader
        title="Personal Learning"
        subtitle="Ask any academic question or explore a topic at your own pace."
        actions={
          messages.length > 0 ? (
            <button
              type="button"
              className="viora-btn viora-btn-sm"
              disabled={busy}
              onClick={handleNewConversation}
            >
              New Topic
            </button>
          ) : undefined
        }
      />

      {feedback.alerts}

      {messages.length === 0 && (
        <section className="viora-learning-hero">
          <div className="viora-row" style={{ justifyContent: "space-between" }}>
            <div>
              <h2
                style={{
                  fontSize: "1.15rem",
                  fontWeight: 700,
                  color: "var(--color-brand-primary)",
                }}
              >
                What would you like to understand today?
              </h2>
              <p
                style={{
                  fontSize: "0.88rem",
                  color: "var(--color-text-muted)",
                  marginTop: "0.25rem",
                }}
              >
                Ask a direct question or request a structured topic explanation with equations, worked steps, and code.
              </p>
            </div>
            <StatusBadge tone="brand">Independent Study</StatusBadge>
          </div>

          <div className="viora-starter-grid">
            {STARTER_PROMPTS.map((starter) => (
              <button
                key={starter.label}
                type="button"
                className="viora-starter-btn"
                disabled={busy}
                onClick={() => {
                  setMode(starter.mode);
                  void submitPrompt(starter.prompt, starter.mode);
                }}
              >
                <div
                  style={{
                    fontWeight: 600,
                    color: "var(--color-brand-primary)",
                    marginBottom: "0.2rem",
                  }}
                >
                  {starter.label}
                </div>
                <div style={{ color: "var(--color-text-muted)", fontSize: "0.8rem" }}>
                  {starter.prompt}
                </div>
              </button>
            ))}
          </div>
        </section>
      )}

      {messages.length > 0 && (
        <div className="viora-chat-thread" aria-live="polite">
          {messages.map((msg) => {
            if (msg.role === "user") {
              return (
                <div key={msg.id} className="viora-msg-user">
                  {msg.content}
                </div>
              );
            }

            const isLatest = msg.id === latestAssistantId;
            return (
              <article key={msg.id} className="viora-msg-assistant">
                <div className="viora-msg-assistant-head">
                  <span>VIORA Learning Assistant</span>
                  {msg.truncated ? (
                    <StatusBadge tone="warning">
                      Incomplete Response (Length Limit)
                    </StatusBadge>
                  ) : (
                    <StatusBadge tone="neutral">
                      {msg.mode === "explain" ? "Topic Explanation" : "Direct Answer"}
                    </StatusBadge>
                  )}
                </div>

                {msg.truncated && (
                  <div style={{ marginBottom: "0.75rem" }}>
                    <AlertBanner
                      tone="warning"
                      title="Response cut off before finishing"
                      message="This reply reached the response length limit and is incomplete. Use Continue below to read the remaining steps."
                    />
                  </div>
                )}

                <RichAcademicContent content={msg.content} />

                {isLatest && msg.followUps && msg.followUps.length > 0 && (
                  <div className="viora-followup-row">
                    {msg.followUps.map((followUp) => (
                      <button
                        key={followUp}
                        type="button"
                        className="viora-followup-chip"
                        disabled={busy}
                        onClick={() => void submitPrompt(followUp, "question")}
                      >
                        {followUp}
                      </button>
                    ))}
                  </div>
                )}
              </article>
            );
          })}

          {busy && (
            <div className="viora-msg-assistant" role="status">
              <div className="viora-typing-dots">
                <span className="viora-typing-dot" aria-hidden="true" />
                <span className="viora-typing-dot" aria-hidden="true" />
                <span className="viora-typing-dot" aria-hidden="true" />
                <span>
                  {mode === "explain"
                    ? "Preparing a clear topic explanation..."
                    : "Working through your question..."}
                </span>
              </div>
            </div>
          )}

          <div ref={threadEndRef} />
        </div>
      )}

      <form onSubmit={handleFormSubmit} className="viora-composer">
        <div className="viora-row" style={{ justifyContent: "space-between" }}>
          <div className="viora-row" role="group" aria-label="Learning mode">
            <button
              type="button"
              className={`viora-btn viora-btn-sm ${mode === "question" ? "viora-btn-primary" : ""}`}
              disabled={busy}
              onClick={() => setMode("question")}
            >
              Ask a Question
            </button>
            <button
              type="button"
              className={`viora-btn viora-btn-sm ${mode === "explain" ? "viora-btn-primary" : ""}`}
              disabled={busy}
              onClick={() => setMode("explain")}
            >
              Explain a Topic
            </button>
          </div>
          <span className="viora-field-hint">
            Enter to send • Shift+Enter for new line
          </span>
        </div>

        <textarea
          id="personal-learning-input"
          aria-label="Academic question or topic"
          className="viora-input"
          rows={3}
          value={prompt}
          onChange={(e) => setPrompt(e.target.value)}
          onKeyDown={handleComposerKeyDown}
          placeholder={
            mode === "explain"
              ? "Enter a topic you want explained clearly (e.g. eigenvalues, recursion, photosynthesis)..."
              : messages.length > 0
                ? "Ask a follow-up question or change the topic..."
                : "Ask any academic question across math, science, programming, or theory..."
          }
          disabled={busy}
          required
        />

        <div className="viora-row" style={{ justifyContent: "flex-end" }}>
          <button
            type="submit"
            className="viora-btn viora-btn-primary"
            disabled={busy || !prompt.trim()}
          >
            {busy
              ? "Generating..."
              : mode === "explain"
                ? "Explain Topic"
                : messages.length > 0
                  ? "Send Follow-up"
                  : "Ask Question"}
          </button>
        </div>
      </form>
    </div>
  );
}
