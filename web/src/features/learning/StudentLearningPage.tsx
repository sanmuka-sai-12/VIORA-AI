import {
  useEffect,
  useRef,
  useState,
  type ChangeEvent,
  type FormEvent,
  type KeyboardEvent,
} from "react";
import type {
  LearningPromptMode,
  PersonalLearningAttachment,
  PersonalLearningAttachmentStatus,
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
  formatSourcePageLabel,
  loadLearningSession,
  saveLearningSession,
  toActiveAttachmentRefs,
  type LearningAttachmentEntry,
  type LearningChatEntry,
} from "./learningSessionState";

const MAX_ATTACHMENTS = 5;

function attachmentTone(
  status: PersonalLearningAttachmentStatus,
): "success" | "warning" | "danger" {
  if (status === "ready") return "success";
  if (status === "partial") return "warning";
  return "danger";
}

function attachmentStatusLabel(status: PersonalLearningAttachmentStatus): string {
  if (status === "ready") return "Ready";
  if (status === "partial") return "Partial";
  return "Could Not Read";
}

function attachmentKindLabel(att: PersonalLearningAttachment): string {
  if (att.kind === "pdf") return "PDF";
  if (att.kind === "image") return "Image (OCR)";
  return "Text";
}

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
  const [attachments, setAttachments] = useState<LearningAttachmentEntry[]>(
    () => initial?.attachments ?? [],
  );
  const [busy, setBusy] = useState(false);
  const [uploadingLabel, setUploadingLabel] = useState<string | null>(null);
  const [showNoteDrawer, setShowNoteDrawer] = useState(false);
  const [noteTitle, setNoteTitle] = useState("");
  const [noteContent, setNoteContent] = useState("");

  const nextIdRef = useRef((initial?.messages.length ?? 0) + 1);
  const threadEndRef = useRef<HTMLDivElement | null>(null);
  const fileInputRef = useRef<HTMLInputElement | null>(null);
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
    setAttachments(restored?.attachments ?? []);
    nextIdRef.current = (restored?.messages.length ?? 0) + 1;
  }, [tenantController, tenantId, userId]);

  // Persist active conversation and attachments scoped to (userId, tenantId).
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
      attachments,
    });
  }, [attachments, messages, mode, prompt, tenantController, tenantId, userId]);

  useEffect(() => {
    threadEndRef.current?.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }, [messages.length, busy]);

  if (!tenantId || !userId) return null;

  const buildHistory = (entries: LearningChatEntry[]): PersonalLearningTurn[] =>
    entries.slice(-10).map((entry) => ({
      role: entry.role,
      content: entry.content,
    }));

  const activeAttachmentRefs = toActiveAttachmentRefs(attachments);
  const activeTitles = attachments
    .filter((item) => item.included && item.attachment.status !== "unreadable")
    .slice(0, MAX_ATTACHMENTS)
    .map((item) => item.attachment.title);

  const submitPrompt = async (
    rawPrompt: string,
    selectedMode: LearningPromptMode,
  ) => {
    const trimmed = rawPrompt.trim();
    if (!trimmed || busy || uploadingLabel) return;

    const historyPayload = buildHistory(messages);
    const userEntry: LearningChatEntry = {
      id: `msg-${nextIdRef.current++}`,
      role: "user",
      content: trimmed,
      mode: selectedMode,
      attachedTitles: activeTitles.length > 0 ? activeTitles : undefined,
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
            attachments: activeAttachmentRefs,
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
            sources: data.sources,
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

  const handleFileChange = async (e: ChangeEvent<HTMLInputElement>) => {
    const selected = e.target.files ? Array.from(e.target.files) : [];
    e.target.value = "";
    if (selected.length === 0) return;

    const remainingSlots = MAX_ATTACHMENTS - attachments.length;
    if (remainingSlots <= 0) {
      return;
    }
    const queue = selected.slice(0, remainingSlots);

    for (const file of queue) {
      setUploadingLabel(`Processing ${file.name} (extracting text / OCR)...`);
      const formData = new FormData();
      formData.append("file", file);
      formData.append("title", file.name);
      await feedback.runApiAction({
        call: () =>
          apiClient.POST(
            "/api/v1/tenants/{tenant_id}/learning/attachments/file",
            {
              params: { path: { tenant_id: tenantId } },
              body: formData as never,
            },
          ),
        fallbackError: `Could not process ${file.name}. Ensure it is a readable PDF, PNG, JPEG, or text file.`,
        onSuccess: (data) => {
          setAttachments((prev) => [
            ...prev,
            {
              attachment: data,
              included: data.status !== "unreadable",
            },
          ]);
        },
      });
    }
    setUploadingLabel(null);
  };

  const handleAddTextNote = async () => {
    const cleanContent = noteContent.trim();
    if (!cleanContent || uploadingLabel || attachments.length >= MAX_ATTACHMENTS) {
      return;
    }
    const cleanTitle = noteTitle.trim() || "Study Notes";
    setUploadingLabel(`Processing "${cleanTitle}"...`);
    const ok = await feedback.runApiAction({
      call: () =>
        apiClient.POST(
          "/api/v1/tenants/{tenant_id}/learning/attachments/text",
          {
            params: { path: { tenant_id: tenantId } },
            body: {
              title: cleanTitle,
              content: cleanContent,
            },
          },
        ),
      fallbackError: "Could not process text note. Ensure at least 3 characters.",
      onSuccess: (data) => {
        setAttachments((prev) => [
          ...prev,
          {
            attachment: data,
            included: data.status !== "unreadable",
          },
        ]);
      },
    });
    setUploadingLabel(null);
    if (ok) {
      setNoteTitle("");
      setNoteContent("");
      setShowNoteDrawer(false);
    }
  };

  const handleToggleAttachment = (attachmentId: string) => {
    setAttachments((prev) =>
      prev.map((item) =>
        item.attachment.attachment_id === attachmentId &&
        item.attachment.status !== "unreadable"
          ? { ...item, included: !item.included }
          : item,
      ),
    );
  };

  const handleRemoveAttachment = (attachmentId: string) => {
    setAttachments((prev) =>
      prev.filter((item) => item.attachment.attachment_id !== attachmentId),
    );
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
    setAttachments([]);
    setPrompt("");
    setShowNoteDrawer(false);
    setNoteTitle("");
    setNoteContent("");
    clearLearningSession(tenantController, userId, tenantId);
  };

  const latestAssistantId = [...messages]
    .reverse()
    .find((m) => m.role === "assistant")?.id;

  return (
    <div className="viora-learning-shell">
      <PageHeader
        title="Personal Learning"
        subtitle="Ask any academic question, explore topics, or study from your private PDFs, images, and notes."
        actions={
          messages.length > 0 || attachments.length > 0 ? (
            <button
              type="button"
              className="viora-btn viora-btn-sm"
              disabled={busy || Boolean(uploadingLabel)}
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
                Ask a direct question, explain a concept, or attach private PDFs, screenshots, or study notes below.
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
                disabled={busy || Boolean(uploadingLabel)}
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
                  <div>{msg.content}</div>
                  {msg.attachedTitles && msg.attachedTitles.length > 0 && (
                    <div
                      style={{
                        marginTop: "0.4rem",
                        fontSize: "0.75rem",
                        opacity: 0.88,
                      }}
                    >
                      Using: {msg.attachedTitles.join(", ")}
                    </div>
                  )}
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

                {msg.sources && msg.sources.length > 0 && (
                  <div className="viora-sources-box">
                    <span
                      style={{
                        fontSize: "0.75rem",
                        fontWeight: 600,
                        color: "var(--color-text-muted)",
                      }}
                    >
                      Referenced Study Materials
                    </span>
                    <div className="viora-sources-list">
                      {msg.sources.map((src, srcIdx) => (
                        <span
                          key={`${src.attachment_id}-${src.page_start}-${srcIdx}`}
                          className="viora-source-pill"
                          title={src.excerpt}
                        >
                          <strong>{src.title}</strong>
                          <span>• {formatSourcePageLabel(src)}</span>
                          {src.section_title && <span>• {src.section_title}</span>}
                        </span>
                      ))}
                    </div>
                  </div>
                )}

                {isLatest && msg.followUps && msg.followUps.length > 0 && (
                  <div className="viora-followup-row">
                    {msg.followUps.map((followUp) => (
                      <button
                        key={followUp}
                        type="button"
                        className="viora-followup-chip"
                        disabled={busy || Boolean(uploadingLabel)}
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
        <div className="viora-row" style={{ justifyContent: "space-between", flexWrap: "wrap" }}>
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
          <div className="viora-row">
            <input
              ref={fileInputRef}
              type="file"
              accept=".pdf,.png,.jpg,.jpeg,.txt,.md"
              multiple
              style={{ display: "none" }}
              onChange={(e) => void handleFileChange(e)}
            />
            <button
              type="button"
              className="viora-btn viora-btn-sm"
              disabled={
                busy || Boolean(uploadingLabel) || attachments.length >= MAX_ATTACHMENTS
              }
              onClick={() => fileInputRef.current?.click()}
            >
              Attach PDF / Image / Text
            </button>
            <button
              type="button"
              className="viora-btn viora-btn-sm"
              disabled={
                busy || Boolean(uploadingLabel) || attachments.length >= MAX_ATTACHMENTS
              }
              onClick={() => setShowNoteDrawer((prev) => !prev)}
            >
              {showNoteDrawer ? "Close Note" : "Paste Study Note"}
            </button>
          </div>
        </div>

        {uploadingLabel && (
          <div className="viora-typing-dots" role="status">
            <span className="viora-typing-dot" aria-hidden="true" />
            <span className="viora-typing-dot" aria-hidden="true" />
            <span className="viora-typing-dot" aria-hidden="true" />
            <span>{uploadingLabel}</span>
          </div>
        )}

        {showNoteDrawer && (
          <div className="viora-text-note-drawer">
            <input
              type="text"
              className="viora-input"
              aria-label="Study note title"
              placeholder="Note title (optional, e.g. Thermodynamics Ch. 3)"
              value={noteTitle}
              onChange={(e) => setNoteTitle(e.target.value)}
              disabled={Boolean(uploadingLabel)}
            />
            <textarea
              className="viora-input"
              aria-label="Study note text"
              rows={3}
              placeholder="Paste excerpt, definitions, or lecture notes here..."
              value={noteContent}
              onChange={(e) => setNoteContent(e.target.value)}
              disabled={Boolean(uploadingLabel)}
            />
            <div className="viora-row" style={{ justifyContent: "flex-end" }}>
              <button
                type="button"
                className="viora-btn viora-btn-sm"
                onClick={() => setShowNoteDrawer(false)}
                disabled={Boolean(uploadingLabel)}
              >
                Cancel
              </button>
              <button
                type="button"
                className="viora-btn viora-btn-sm viora-btn-primary"
                disabled={Boolean(uploadingLabel) || noteContent.trim().length < 3}
                onClick={() => void handleAddTextNote()}
              >
                Process Note
              </button>
            </div>
          </div>
        )}

        {attachments.length > 0 && (
          <div className="viora-attachment-tray" aria-label="Attached study materials">
            {attachments.map(({ attachment, included }) => {
              const unusable = attachment.status === "unreadable";
              return (
                <div
                  key={attachment.attachment_id}
                  className={`viora-attachment-chip${!included || unusable ? " is-excluded" : ""}`}
                >
                  <div
                    style={{
                      display: "flex",
                      flexDirection: "column",
                      gap: "0.15rem",
                      minWidth: 0,
                    }}
                  >
                    <div className="viora-row" style={{ flexWrap: "wrap", gap: "0.4rem" }}>
                      <strong
                        style={{
                          overflow: "hidden",
                          textOverflow: "ellipsis",
                          whiteSpace: "nowrap",
                          maxWidth: "280px",
                        }}
                      >
                        {attachment.title}
                      </strong>
                      <StatusBadge tone="neutral">
                        {attachmentKindLabel(attachment)}
                      </StatusBadge>
                      <StatusBadge tone={attachmentTone(attachment.status)}>
                        {attachmentStatusLabel(attachment.status)}
                      </StatusBadge>
                    </div>
                    <span
                      style={{
                        fontSize: "0.75rem",
                        color: "var(--color-text-muted)",
                      }}
                    >
                      {attachment.status_message}
                    </span>
                  </div>

                  <div className="viora-row">
                    {!unusable && (
                      <button
                        type="button"
                        className="viora-btn viora-btn-sm"
                        disabled={busy}
                        onClick={() =>
                          handleToggleAttachment(attachment.attachment_id)
                        }
                      >
                        {included ? "Active" : "Paused"}
                      </button>
                    )}
                    <button
                      type="button"
                      className="viora-btn viora-btn-sm"
                      disabled={busy}
                      aria-label={`Remove ${attachment.title}`}
                      onClick={() =>
                        handleRemoveAttachment(attachment.attachment_id)
                      }
                    >
                      Remove
                    </button>
                  </div>
                </div>
              );
            })}
          </div>
        )}

        <textarea
          id="personal-learning-input"
          aria-label="Academic question or topic"
          className="viora-input"
          rows={3}
          value={prompt}
          onChange={(e) => setPrompt(e.target.value)}
          onKeyDown={handleComposerKeyDown}
          placeholder={
            activeTitles.length > 0
              ? `Ask a question about ${activeTitles.join(", ")}...`
              : mode === "explain"
                ? "Enter a topic you want explained clearly (e.g. eigenvalues, recursion, photosynthesis)..."
                : messages.length > 0
                  ? "Ask a follow-up question or change the topic..."
                  : "Ask any academic question across math, science, programming, or theory..."
          }
          disabled={busy}
          required
        />

        <div className="viora-row" style={{ justifyContent: "space-between" }}>
          <span className="viora-field-hint">
            Enter to send • Shift+Enter for new line
            {attachments.length > 0
              ? ` • ${activeAttachmentRefs.length}/${attachments.length} material${attachments.length === 1 ? "" : "s"} active`
              : ""}
          </span>
          <button
            type="submit"
            className="viora-btn viora-btn-primary"
            disabled={busy || Boolean(uploadingLabel) || !prompt.trim()}
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
