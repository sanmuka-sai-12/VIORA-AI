import {
  useCallback,
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
  PersonalLearningSessionSummary,
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
  mapSessionAttachments,
  mapSessionDetailToChatEntries,
  NEW_CHAT_SENTINEL,
  readActiveSessionHint,
  saveLearningSession,
  toActiveAttachmentIds,
  toActiveAttachmentRefs,
  writeActiveSessionHint,
  type LearningAttachmentEntry,
  type LearningChatEntry,
} from "./learningSessionState";

const MAX_ATTACHMENTS = 5;

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

function formatShortDate(iso: string): string {
  const parsed = new Date(iso);
  if (Number.isNaN(parsed.getTime())) return "";
  return parsed.toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
  });
}

export function StudentLearningPage() {
  const { apiClient, snapshot: auth } = useAuth();
  const { snapshot: tenant, controller: tenantController } = useTenant();
  const userId = auth.user?.user_id;
  const tenantId = tenant.selectedTenantId;
  const feedback = useActionFeedback();

  const initial = loadLearningSession(tenantController, userId, tenantId);
  const [sessionId, setSessionId] = useState<string | null>(
    () => initial?.sessionId ?? null,
  );
  const [sessionTitle, setSessionTitle] = useState<string>(
    () => initial?.sessionTitle ?? "New Study Session",
  );
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

  const [sessions, setSessions] = useState<PersonalLearningSessionSummary[]>([]);
  const [library, setLibrary] = useState<PersonalLearningAttachment[]>([]);
  const [railTab, setRailTab] = useState<"history" | "library">("history");
  const [loadingHistory, setLoadingHistory] = useState(false);

  const [renamingId, setRenamingId] = useState<string | null>(null);
  const [renameDraft, setRenameDraft] = useState("");

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

  const openSession = useCallback(
    async (targetSessionId: string) => {
      if (!tenantId || !userId) return;
      const res = await apiClient.GET(
        "/api/v1/tenants/{tenant_id}/learning/sessions/{session_id}",
        {
          params: {
            path: { tenant_id: tenantId, session_id: targetSessionId },
          },
        },
      );
      if (res.data) {
        setSessionId(res.data.session_id);
        setSessionTitle(res.data.title);
        setMode(res.data.mode);
        setMessages(mapSessionDetailToChatEntries(res.data));
        setAttachments(mapSessionAttachments(res.data.attachments));
        setPrompt("");
        setRenamingId(null);
        writeActiveSessionHint(userId, tenantId, res.data.session_id);
      }
    },
    [apiClient, tenantId, userId],
  );

  const refreshSidebarLists = useCallback(
    async (hydrateIfCold: boolean) => {
      if (!tenantId || !userId) return;
      setLoadingHistory(true);
      try {
        const [sessRes, libRes] = await Promise.all([
          apiClient.GET("/api/v1/tenants/{tenant_id}/learning/sessions", {
            params: { path: { tenant_id: tenantId }, query: { limit: 40 } },
          }),
          apiClient.GET("/api/v1/tenants/{tenant_id}/learning/materials", {
            params: { path: { tenant_id: tenantId }, query: { limit: 50 } },
          }),
        ]);
        const nextSessions = sessRes.data?.items ?? [];
        const nextLibrary = libRes.data?.items ?? [];
        setSessions(nextSessions);
        setLibrary(nextLibrary);

        if (hydrateIfCold && nextSessions.length > 0) {
          const hint = readActiveSessionHint(userId, tenantId);
          if (hint !== NEW_CHAT_SENTINEL) {
            const match = hint
              ? nextSessions.find((s) => s.session_id === hint)
              : nextSessions[0];
            if (match) {
              await openSession(match.session_id);
            }
          }
        }
      } finally {
        setLoadingHistory(false);
      }
    },
    [apiClient, openSession, tenantId, userId],
  );

  // Initial load + scope change reload (navigating back retains cache; refresh/login hydrates from DB).
  useEffect(() => {
    if (!tenantId || !userId) return;
    const scopeKey = `${userId}:${tenantId}`;
    const scopeChanged = scopeKey !== loadedScopeRef.current;
    loadedScopeRef.current = scopeKey;

    const cached = loadLearningSession(tenantController, userId, tenantId);
    if (scopeChanged) {
      setSessionId(cached?.sessionId ?? null);
      setSessionTitle(cached?.sessionTitle ?? "New Study Session");
      setMode(cached?.mode ?? "question");
      setPrompt(cached?.prompt ?? "");
      setMessages(cached?.messages ?? []);
      setAttachments(cached?.attachments ?? []);
      nextIdRef.current = (cached?.messages.length ?? 0) + 1;
    }

    const coldWorkspace =
      !cached || (cached.sessionId === null && cached.messages.length === 0);
    void refreshSidebarLists(coldWorkspace);
  }, [refreshSidebarLists, tenantController, tenantId, userId]);

  // Keep instant route-switch cache updated scoped cleanly to (userId, tenantId).
  useEffect(() => {
    if (!userId || !tenantId) return;
    saveLearningSession(tenantController, {
      userId,
      tenantId,
      sessionId,
      sessionTitle,
      mode,
      prompt,
      messages,
      attachments,
    });
  }, [
    attachments,
    messages,
    mode,
    prompt,
    sessionId,
    sessionTitle,
    tenantController,
    tenantId,
    userId,
  ]);

  useEffect(() => {
    threadEndRef.current?.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }, [messages.length, busy]);

  if (!tenantId || !userId) return null;

  const syncSessionAttachments = async (nextList: LearningAttachmentEntry[]) => {
    if (!sessionId) return;
    const nextIds = toActiveAttachmentIds(nextList);
    await apiClient.PATCH(
      "/api/v1/tenants/{tenant_id}/learning/sessions/{session_id}",
      {
        params: { path: { tenant_id: tenantId, session_id: sessionId } },
        body: { attachment_ids: nextIds },
      },
    );
    void refreshSidebarLists(false);
  };

  const buildHistory = (entries: LearningChatEntry[]): PersonalLearningTurn[] =>
    entries.slice(-10).map((entry) => ({
      role: entry.role,
      content: entry.content,
    }));

  const activeAttachmentRefs = toActiveAttachmentRefs(attachments);
  const activeAttachmentIds = toActiveAttachmentIds(attachments);
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
            session_id: sessionId,
            history: historyPayload,
            attachments: activeAttachmentRefs,
            attachment_ids: activeAttachmentIds,
          },
        }),
      fallbackError: "Unable to get a response right now. Please try again.",
      onSuccess: (data) => {
        if (data.session_id) {
          setSessionId(data.session_id);
          if (data.session_title) {
            setSessionTitle(data.session_title);
          }
          writeActiveSessionHint(userId, tenantId, data.session_id);
        }
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
        void refreshSidebarLists(false);
      },
    });

    if (!ok) {
      setMessages((prev) => prev.filter((item) => item.id !== userEntry.id));
      setPrompt(trimmed);
    }
  };

  const attachMaterialToChat = (att: PersonalLearningAttachment) => {
    setAttachments((prev) => {
      if (prev.some((p) => p.attachment.attachment_id === att.attachment_id)) {
        return prev;
      }
      if (prev.length >= MAX_ATTACHMENTS) {
        return prev;
      }
      const next = [
        ...prev,
        { attachment: att, included: att.status !== "unreadable" },
      ];
      void syncSessionAttachments(next);
      return next;
    });
  };

  const handleFileChange = async (e: ChangeEvent<HTMLInputElement>) => {
    const selected = e.target.files ? Array.from(e.target.files) : [];
    e.target.value = "";
    if (selected.length === 0) return;

    const remainingSlots = MAX_ATTACHMENTS - attachments.length;
    if (remainingSlots <= 0) return;
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
          attachMaterialToChat(data);
          void refreshSidebarLists(false);
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
        attachMaterialToChat(data);
        void refreshSidebarLists(false);
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
    setAttachments((prev) => {
      const next = prev.map((item) =>
        item.attachment.attachment_id === attachmentId &&
        item.attachment.status !== "unreadable"
          ? { ...item, included: !item.included }
          : item,
      );
      void syncSessionAttachments(next);
      return next;
    });
  };

  const handleRemoveAttachment = (attachmentId: string) => {
    setAttachments((prev) => {
      const next = prev.filter(
        (item) => item.attachment.attachment_id !== attachmentId,
      );
      void syncSessionAttachments(next);
      return next;
    });
  };

  const handleDeleteMaterial = async (attachmentId: string) => {
    await feedback.runApiAction({
      call: () =>
        apiClient.DELETE(
          "/api/v1/tenants/{tenant_id}/learning/materials/{attachment_id}",
          {
            params: { path: { tenant_id: tenantId, attachment_id: attachmentId } },
          },
        ),
      fallbackError: "Could not remove material from library.",
      onSuccess: () => {
        setAttachments((prev) =>
          prev.filter((item) => item.attachment.attachment_id !== attachmentId),
        );
        setLibrary((prev) =>
          prev.filter((item) => item.attachment_id !== attachmentId),
        );
        void refreshSidebarLists(false);
      },
    });
  };

  const handleNewConversation = () => {
    feedback.clearFeedback();
    setSessionId(null);
    setSessionTitle("New Study Session");
    setMessages([]);
    setAttachments([]);
    setPrompt("");
    setShowNoteDrawer(false);
    setNoteTitle("");
    setNoteContent("");
    setRenamingId(null);
    writeActiveSessionHint(userId, tenantId, NEW_CHAT_SENTINEL);
    clearLearningSession(tenantController, userId, tenantId);
  };

  const handleSaveRename = async (targetSessionId: string) => {
    const clean = renameDraft.trim();
    if (!clean) return;
    await feedback.runApiAction({
      call: () =>
        apiClient.PATCH(
          "/api/v1/tenants/{tenant_id}/learning/sessions/{session_id}",
          {
            params: { path: { tenant_id: tenantId, session_id: targetSessionId } },
            body: { title: clean },
          },
        ),
      fallbackError: "Could not rename study session.",
      onSuccess: (updated) => {
        if (sessionId === targetSessionId) {
          setSessionTitle(updated.title);
        }
        setRenamingId(null);
        setRenameDraft("");
        void refreshSidebarLists(false);
      },
    });
  };

  const handleDeleteSession = async (targetSessionId: string) => {
    await feedback.runApiAction({
      call: () =>
        apiClient.DELETE(
          "/api/v1/tenants/{tenant_id}/learning/sessions/{session_id}",
          {
            params: { path: { tenant_id: tenantId, session_id: targetSessionId } },
          },
        ),
      fallbackError: "Could not delete study session.",
      onSuccess: () => {
        if (sessionId === targetSessionId) {
          handleNewConversation();
        }
        void refreshSidebarLists(false);
      },
    });
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

  const latestAssistantId = [...messages]
    .reverse()
    .find((m) => m.role === "assistant")?.id;

  return (
    <div className="viora-learning-layout">
      <aside className="viora-learning-rail" aria-label="Study History and Personal Library">
        <button
          type="button"
          className="viora-btn viora-btn-primary"
          disabled={busy || Boolean(uploadingLabel)}
          onClick={handleNewConversation}
        >
          + New Study Session
        </button>

        <div className="viora-rail-tabs" role="tablist" aria-label="Workspace panels">
          <button
            type="button"
            role="tab"
            aria-selected={railTab === "history"}
            className={`viora-rail-tab${railTab === "history" ? " is-active" : ""}`}
            onClick={() => setRailTab("history")}
          >
            History ({sessions.length})
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={railTab === "library"}
            className={`viora-rail-tab${railTab === "library" ? " is-active" : ""}`}
            onClick={() => setRailTab("library")}
          >
            Library ({library.length})
          </button>
        </div>

        {railTab === "history" && (
          <div className="viora-rail-list">
            {loadingHistory && sessions.length === 0 && (
              <p className="viora-field-hint">Loading recent sessions...</p>
            )}
            {!loadingHistory && sessions.length === 0 && (
              <p className="viora-field-hint">
                Your saved study conversations will appear here.
              </p>
            )}
            {sessions.map((item) => {
              const selected = item.session_id === sessionId;
              const isEditing = renamingId === item.session_id;
              return (
                <div
                  key={item.session_id}
                  className={`viora-rail-item${selected ? " is-selected" : ""}`}
                >
                  {isEditing ? (
                    <div style={{ display: "flex", flexDirection: "column", gap: "0.35rem" }}>
                      <input
                        type="text"
                        className="viora-input"
                        aria-label="Session title"
                        value={renameDraft}
                        onChange={(e) => setRenameDraft(e.target.value)}
                        onKeyDown={(e) => {
                          if (e.key === "Enter") {
                            e.preventDefault();
                            void handleSaveRename(item.session_id);
                          }
                        }}
                      />
                      <div className="viora-rail-actions">
                        <button
                          type="button"
                          className="viora-btn viora-btn-sm viora-btn-primary"
                          onClick={() => void handleSaveRename(item.session_id)}
                        >
                          Save
                        </button>
                        <button
                          type="button"
                          className="viora-btn viora-btn-sm"
                          onClick={() => {
                            setRenamingId(null);
                            setRenameDraft("");
                          }}
                        >
                          Cancel
                        </button>
                      </div>
                    </div>
                  ) : (
                    <>
                      <button
                        type="button"
                        className="viora-rail-item-title"
                        disabled={busy}
                        onClick={() => void openSession(item.session_id)}
                      >
                        {item.title}
                      </button>
                      <div className="viora-rail-meta">
                        <span>{item.message_count} messages</span>
                        <span>{formatShortDate(item.updated_at)}</span>
                      </div>
                      <div className="viora-rail-actions">
                        <button
                          type="button"
                          className="viora-btn viora-btn-sm"
                          disabled={busy}
                          onClick={() => {
                            setRenamingId(item.session_id);
                            setRenameDraft(item.title);
                          }}
                        >
                          Rename
                        </button>
                        <button
                          type="button"
                          className="viora-btn viora-btn-sm"
                          disabled={busy}
                          onClick={() => void handleDeleteSession(item.session_id)}
                        >
                          Delete
                        </button>
                      </div>
                    </>
                  )}
                </div>
              );
            })}
          </div>
        )}

        {railTab === "library" && (
          <div className="viora-rail-list">
            {library.length === 0 && (
              <p className="viora-field-hint">
                Attach a PDF, image, or study note below to build your personal study library.
              </p>
            )}
            {library.map((mat) => {
              const alreadyAttached = attachments.some(
                (a) => a.attachment.attachment_id === mat.attachment_id,
              );
              const unusable = mat.status === "unreadable";
              return (
                <div key={mat.attachment_id} className="viora-rail-item">
                  <div style={{ fontWeight: 600, fontSize: "0.83rem", wordBreak: "break-word" }}>
                    {mat.title}
                  </div>
                  <div className="viora-row" style={{ gap: "0.3rem", flexWrap: "wrap" }}>
                    <StatusBadge tone="neutral">{attachmentKindLabel(mat)}</StatusBadge>
                    <StatusBadge tone={attachmentTone(mat.status)}>
                      {attachmentStatusLabel(mat.status)}
                    </StatusBadge>
                  </div>
                  <span className="viora-field-hint">{mat.status_message}</span>
                  <div className="viora-rail-actions">
                    {!unusable && (
                      <button
                        type="button"
                        className={`viora-btn viora-btn-sm ${alreadyAttached ? "" : "viora-btn-primary"}`}
                        disabled={
                          busy ||
                          alreadyAttached ||
                          attachments.length >= MAX_ATTACHMENTS
                        }
                        onClick={() => attachMaterialToChat(mat)}
                      >
                        {alreadyAttached ? "Attached" : "Use in Chat"}
                      </button>
                    )}
                    <button
                      type="button"
                      className="viora-btn viora-btn-sm"
                      disabled={busy}
                      onClick={() => void handleDeleteMaterial(mat.attachment_id)}
                    >
                      Delete
                    </button>
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </aside>

      <div className="viora-learning-shell">
        <PageHeader
          title={sessionId ? sessionTitle : "Personal Learning"}
          subtitle="Ask questions, explore topics, or study from your private PDFs, images, and saved notes."
          actions={
            sessionId ? (
              <div className="viora-row">
                <button
                  type="button"
                  className="viora-btn viora-btn-sm"
                  disabled={busy}
                  onClick={() => {
                    setRailTab("history");
                    setRenamingId(sessionId);
                    setRenameDraft(sessionTitle);
                  }}
                >
                  Rename
                </button>
                <button
                  type="button"
                  className="viora-btn viora-btn-sm"
                  disabled={busy || Boolean(uploadingLabel)}
                  onClick={handleNewConversation}
                >
                  New Session
                </button>
              </div>
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
                  Start a new conversation, resume a saved session from History, or attach materials from your Personal Library.
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
                        aria-label={`Detach ${attachment.title}`}
                        onClick={() =>
                          handleRemoveAttachment(attachment.attachment_id)
                        }
                      >
                        Detach
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
    </div>
  );
}
